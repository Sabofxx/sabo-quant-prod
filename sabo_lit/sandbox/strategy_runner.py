"""
Parameterized, config-driven runner — one strategy on one isolated account.

    python strategy_runner.py --config fxtsm
    python strategy_runner.py --config gold --dry-run

Replaces the hardcoded mono-strategy automated_runner for the multi-account
program. State is fully isolated per strategy (cfg.live_dir); nothing is shared.
Reuses capital_connector for session/execution/realized-sync and reuses the FX
schedule guard from automated_runner. Daily cadence today; the structure leaves
the door open to an intraday loop (call run_once() multiple times/day).

Account safety: the active Capital session account is asserted == cfg.account_id
BEFORE signal generation AND again inside execute_delta_csv (defense in depth).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

import capital_connector as cc
from automated_runner import market_execution_allowed
from strategy_config import StrategyConfig, load_config
from strategy_signals import generate_delta_orders
from telegram_notifier import send_telegram

DATA_DIR = Path(__file__).parent / "data"


def notify(text: str) -> None:
    """Best-effort Telegram message; silent no-op when secrets absent."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return
    try:
        send_telegram(token, chat_id, text)
    except Exception as exc:
        log(f"WARN: telegram send failed: {exc}")


def _snapshot_rows(cfg: StrategyConfig) -> list[dict]:
    path = cfg.live_dir / "automated_daily_pnl.jsonl"
    rows: list[dict] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def daily_drawdown_check(rows: list[dict], balance: float,
                         max_daily_dd_pct: float,
                         today: str | None = None) -> tuple[bool, str]:
    """Same-day loss vs the last PRIOR day's close balance.

    (The pre-run snapshot rewrites today's row, so comparing against "today's
    first snapshot" — the legacy approach — always reads ~0 on a daily cadence.)
    Returns (allowed_to_trade, reason).
    """
    today = today or datetime.now(UTC).date().isoformat()
    prior = [r for r in rows
             if r.get("date", "") < today and float(r.get("balance", 0) or 0) > 0]
    if not prior or balance <= 0:
        return True, "no prior-day balance yet"
    prev_close = float(prior[-1]["balance"])
    dd_pct = (balance - prev_close) / prev_close * 100
    if dd_pct < max_daily_dd_pct:
        return False, (f"DAILY DD BREAKER: {dd_pct:+.2f}% < {max_daily_dd_pct:+.2f}% "
                       f"(prev close {prev_close:,.2f} -> now {balance:,.2f})")
    return True, f"daily DD {dd_pct:+.2f}% within budget"


def peak_drawdown_check(rows: list[dict], balance: float,
                        max_peak_dd_pct: float) -> tuple[bool, str, dict]:
    """Drawdown from the all-time equity peak (FTMO-style max-loss guard)."""
    balances = [float(r.get("balance", 0) or 0) for r in rows
                if float(r.get("balance", 0) or 0) > 0]
    if balance > 0:
        balances.append(balance)
    if not balances:
        return True, "no balance history yet", {}
    peak = max(balances)
    dd_pct = (balance - peak) / peak * 100 if peak > 0 else 0.0
    stats = {"peak": peak, "current": balance, "dd_pct": dd_pct}
    if dd_pct < max_peak_dd_pct:
        return False, (f"PEAK DD BREAKER: {dd_pct:+.2f}% < {max_peak_dd_pct:+.2f}% "
                       f"(peak {peak:,.2f} -> now {balance:,.2f})"), stats
    return True, f"peak DD {dd_pct:+.2f}% within budget (peak {peak:,.2f})", stats


def refresh_csv(client: cc.CapitalClient, symbol: str, csv_name: str) -> int:
    """Fetch latest daily candles for an instrument and append to its data CSV.

    Generic over instrument/filename (unlike the FX-hardcoded legacy helper), so
    it serves FX, Gold and future phase-2 instruments. Bootstraps the file if
    missing (solves the gitignored-data gap on the GH runner). Returns new bars.
    """
    epic = cc.SYMBOL_MAP.get(symbol)
    if not epic:
        log(f"WARN: no epic mapping for {symbol}; skip refresh")
        return 0
    try:
        candles = client.get_daily_candles(epic, max_bars=200)
    except Exception as exc:
        log(f"WARN: candle fetch failed for {symbol}/{epic}: {exc}")
        return 0
    rows = []
    for cnd in candles:
        t = cnd.get("snapshotTimeUTC") or cnd.get("snapshotTime")
        o, h, l, c = (cnd.get(k, {}) for k in ("openPrice", "highPrice", "lowPrice", "closePrice"))
        def mid(x):
            b, a = x.get("bid"), x.get("ask")
            vals = [v for v in (b, a) if v is not None]
            return sum(vals) / len(vals) if vals else None
        if None in (mid(o), mid(h), mid(l), mid(c)) or not t:
            continue
        rows.append((pd.Timestamp(t, tz="UTC") if pd.Timestamp(t).tzinfo is None
                     else pd.Timestamp(t), mid(o), mid(h), mid(l), mid(c)))
    if not rows:
        return 0
    fresh = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"]).set_index("ts")
    path = DATA_DIR / csv_name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            f.write("timestamp,open,high,low,close\n")
            for ts, r in fresh.iterrows():
                f.write(f"{int(ts.timestamp()*1000)},{r['open']},{r['high']},{r['low']},{r['close']}\n")
        log(f"  BOOTSTRAP {csv_name}: {len(fresh)} bars")
        return len(fresh)
    last = pd.read_csv(path).tail(1)
    last_ts = pd.Timestamp(int(last["timestamp"].iloc[0]), unit="ms", tz="UTC") if len(last) else None
    new = fresh[fresh.index > last_ts] if last_ts is not None else fresh
    if new.empty:
        return 0
    with path.open("a") as f:
        for ts, r in new.iterrows():
            f.write(f"{int(ts.timestamp()*1000)},{r['open']},{r['high']},{r['low']},{r['close']}\n")
    return len(new)


def log(msg: str) -> None:
    print(f"[{datetime.now(UTC).isoformat(timespec='seconds')}] {msg}", flush=True)


def build_client(cfg: StrategyConfig) -> cc.CapitalClient:
    api_key = cfg.secret("CAPITAL_API_KEY")
    identifier = cfg.secret("CAPITAL_IDENTIFIER")
    password = cfg.secret("CAPITAL_API_PASSWORD")
    if not (api_key and identifier and password):
        log(f"FATAL: missing CAPITAL_* secrets for {cfg.id} (suffix={cfg.secret_suffix!r})")
        sys.exit(2)
    env = cfg.secret("CAPITAL_ENVIRONMENT") or cfg.env
    return cc.CapitalClient(api_key, identifier, password, env=env)


def append_jsonl(path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def snapshot(client: cc.CapitalClient, cfg: StrategyConfig, tag: str) -> dict:
    acc = client.account_summary()
    bal = acc.get("balance", {})
    rec = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "date": datetime.now(UTC).date().isoformat(),
        "tag": tag,
        "strategy": cfg.id,
        "account_id": client.account_id,
        "balance": float(bal.get("balance", 0) or 0),
        "available": float(bal.get("available", 0) or 0),
        "deposit": float(bal.get("deposit", 0) or 0),
        "profit_loss": float(bal.get("profitLoss", 0) or 0),
        "currency": acc.get("currency") or client.currency,
    }
    # one row per UTC date (cap), like the legacy snapshot
    pnl = cfg.live_dir / "automated_daily_pnl.jsonl"
    rows = []
    if pnl.exists():
        for line in pnl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if r.get("date") != rec["date"]:
                    rows.append(r)
            except json.JSONDecodeError:
                continue
    rows.append(rec)
    pnl.parent.mkdir(parents=True, exist_ok=True)
    pnl.write_text("\n".join(json.dumps(r, default=str) for r in rows) + "\n")
    return rec


def write_account_state(client: cc.CapitalClient, cfg: StrategyConfig,
                         market_open: bool, reason: str, executed: bool) -> None:
    acc = client.account_summary()
    bal = acc.get("balance", {})
    positions = []
    try:
        for entry in client.get_positions():
            p = entry.get("position", {})
            m = entry.get("market", {})
            positions.append({
                "deal_id": p.get("dealId"), "epic": m.get("epic"),
                "direction": p.get("direction"), "size": float(p.get("size", 0) or 0),
                "open_level": float(p.get("level", 0) or 0),
                "profit_loss": float(p.get("profit", 0) or 0),
            })
    except Exception as exc:
        log(f"WARN: positions fetch failed: {exc}")
    state = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "strategy": cfg.id, "account_id": client.account_id,
        "environment": client.base.split("//")[1].split("-")[0],
        "balance": float(bal.get("balance", 0) or 0),
        "available": float(bal.get("available", 0) or 0),
        "margin_used": float(bal.get("deposit", 0) or 0) - float(bal.get("available", 0) or 0),
        "profit_loss": float(bal.get("profitLoss", 0) or 0),
        "deposit": float(bal.get("deposit", 0) or 0),
        "currency": acc.get("currency") or client.currency,
        "market_open": market_open, "market_reason": reason,
        "executed_this_run": executed, "positions": positions,
    }
    (cfg.live_dir / "account_state.json").write_text(json.dumps(state, indent=2))


def run_once(cfg: StrategyConfig, dry_run: bool = False) -> int:
    log(f"=== strategy={cfg.id} account={cfg.account_id or '(unset)'} env={cfg.env} dry_run={dry_run} ===")
    if not dry_run and not cfg.account_id:
        log("FATAL: account_id empty in config — refusing live run (no implicit "
            "active-account trading). Set account_id or use --dry-run.")
        sys.exit(7)
    cfg.live_dir.mkdir(parents=True, exist_ok=True)
    cc.set_live_dir(cfg.live_dir)  # isolate executions/slippage/realized to this strategy

    client = build_client(cfg)
    client.login()
    log(f"Logged in. session account={client.account_id} currency={client.currency}")

    # Pin to the strategy's account (Cas A: switch; Cas B: already correct) + hard assert
    if cfg.account_id and client.account_id != cfg.account_id:
        log(f"Switching active account {client.account_id} -> {cfg.account_id}")
        client.switch_account(cfg.account_id)
    if cfg.account_id:
        client.assert_active_account(cfg.account_id)  # raises on mismatch
        log(f"Account guard OK: session == config ({cfg.account_id})")

    snap = snapshot(client, cfg, "pre")
    equity = snap["balance"] or snap["deposit"]
    log(f"Equity={equity:.2f} {snap['currency']}")

    # Refresh each instrument's daily bars from the broker so signals use fresh
    # data (and bootstrap the CSV on the GH runner where data/ is gitignored).
    for ins in cfg.instruments:
        n = refresh_csv(client, ins.symbol, ins.csv)
        if n:
            log(f"  data {ins.symbol}: +{n} bars")

    # Confirm each instrument's epic + candle format against the live broker.
    # In dry-run this is the proof that "GOLD"/index/oil epics and the candle
    # schema are correct on THIS account — fail loud if not.
    market_status: dict[str, str] = {}
    for ins in cfg.instruments:
        epic = cc.SYMBOL_MAP.get(ins.symbol)
        if not epic:
            log(f"FATAL: no epic mapping for {ins.symbol}"); sys.exit(8)
        try:
            mkt = client.get_market(epic)
            snap = mkt.get("snapshot", {})
            inst = mkt.get("instrument", {})
            market_status[ins.symbol] = str(snap.get("marketStatus") or "?")
            log(f"  EPIC CONFIRM {ins.symbol} -> epic='{inst.get('epic', epic)}' "
                f"name='{inst.get('name','?')}' bid={snap.get('bid')} offer={snap.get('offer')} "
                f"status={market_status[ins.symbol]}")
        except Exception as exc:
            log(f"FATAL: epic '{epic}' for {ins.symbol} not resolvable on this account: {exc}")
            sys.exit(8)
        try:
            cnd = client.get_daily_candles(epic, max_bars=2)
            log(f"  CANDLE FORMAT {ins.symbol}: {cnd[0] if cnd else 'EMPTY'}")
        except Exception as exc:
            log(f"FATAL: candle fetch for {ins.symbol}/{epic} failed: {exc}")
            sys.exit(8)

    rows_orders = generate_delta_orders(cfg, equity)
    nonflat = [r for r in rows_orders if r["signal"] != 0]
    log(f"Signals: {len(rows_orders)} instruments, {len(nonflat)} non-flat, "
        f"leverage={rows_orders[0]['leverage'] if rows_orders else 0}")
    for r in rows_orders:
        epic = cc.SYMBOL_MAP.get(r["instrument"], "?")
        side = "BUY" if r["delta_notional_usd"] > 0 else "SELL" if r["delta_notional_usd"] < 0 else "FLAT"
        log(f"  {'WOULD ORDER' if dry_run else 'ORDER'} {r['instrument']} (epic={epic}) "
            f"{side} signal={r['signal']:+.0f} delta=${r['delta_notional_usd']:+,.0f} "
            f"account={cfg.account_id or '(unset)'}")

    delta_csv = cfg.live_dir / "prop_delta_orders_latest.csv"
    allowed, reason = market_execution_allowed()
    log(f"Market execution: {reason}")

    # Broker-reported market status is authoritative per instrument (the FX
    # schedule guard above is FX-centric; gold/indices have their own hours).
    tradeable = [s for s, st in market_status.items() if st.upper() == "TRADEABLE"]
    if allowed and market_status and not tradeable:
        allowed = False
        reason = f"no instrument TRADEABLE (status={market_status})"
        log(f"Market status guard: {reason}")

    # Risk breakers (ported from the legacy runner — the new pipeline ran
    # without any drawdown halt): daily loss vs prior close, and peak DD.
    if allowed and not dry_run:
        snap_rows = _snapshot_rows(cfg)
        daily_pct = float(os.environ.get("DAILY_DD_BREAKER_PCT", "-2.0"))
        peak_pct = float(os.environ.get("MAX_PEAK_DD_PCT", "-8.0"))
        d_ok, d_msg = daily_drawdown_check(snap_rows, equity, daily_pct)
        p_ok, p_msg, _ = peak_drawdown_check(snap_rows, equity, peak_pct)
        log(f"Daily breaker: {d_msg}")
        log(f"Peak breaker: {p_msg}")
        if not d_ok or not p_ok:
            allowed = False
            reason = d_msg if not d_ok else p_msg
            notify(f"⛔️ <b>{cfg.id}</b> execution HALTED\n{reason}")

    executed = False
    if dry_run:
        log("DRY-RUN: chain validated (login/switch/assert/epic/candles/signal). "
            "No order placed. STOP.")
    elif not allowed:
        log(f"INFO: skip execution: {reason}")
    else:
        cc.execute_delta_csv(client, delta_csv,
                             min_notional=cfg.min_notional_usd, dry_run=dry_run,
                             reset=True, expected_account_id=cfg.account_id)
        executed = True

    n_realized = 0
    if not dry_run:
        try:
            n_realized = cc.sync_realized_from_transactions(client)
        except Exception as exc:
            log(f"WARN: realized sync failed: {exc}")
    post = snapshot(client, cfg, "post")
    write_account_state(client, cfg, allowed, reason, executed)

    if not dry_run:
        sig_lines = "\n".join(
            f"  {r['instrument']}: {'LONG' if r['signal'] > 0 else 'SHORT' if r['signal'] < 0 else 'FLAT'}"
            f" (${r['delta_notional_usd']:+,.0f})" for r in rows_orders)
        _, _, peak_stats = peak_drawdown_check(_snapshot_rows(cfg), post["balance"],
                                               float(os.environ.get("MAX_PEAK_DD_PCT", "-8.0")))
        notify(
            f"{'✅' if executed else '⏸'} <b>{cfg.id}</b> daily run\n"
            f"Balance: {post['balance']:,.2f} {post['currency']}\n"
            f"Peak DD: {peak_stats.get('dd_pct', 0.0):+.2f}%\n"
            f"Executed: {'yes' if executed else f'no — {reason}'}\n"
            f"Realized synced: +{n_realized}\n"
            f"{sig_lines}"
        )

    log(f"Done: {cfg.id}. State in {cfg.live_dir}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Config-driven single-strategy runner")
    ap.add_argument("--config", required=True, help="strategy id or path (e.g. fxtsm)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = load_config(args.config)
    sys.exit(run_once(cfg, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
