"""
Sandbox — fully automated daily runner for GitHub Actions cron.

Pipeline:
  1. Verify Capital.com credentials + login
  2. Fetch fresh daily candles from Capital.com (last 30 days per pair)
  3. Append to existing CSV history
  4. Run signal generator with --adaptive (ADAPTIVE_75_50)
  5. Read current Capital.com positions
  6. Execute delta orders via capital_connector
  7. Log fills + daily P&L
  8. Generate dashboard

If any critical step fails, exits non-zero (GitHub Actions catches → Telegram alert).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from capital_connector import CapitalClient, SYMBOL_MAP
import tradingview_filter


HERE = Path(__file__).parent
LIVE_DIR = HERE / "live"
DATA_DIR = HERE / "data"
RUN_LOG = LIVE_DIR / "automated_runner.log"
LATEST_PNL = LIVE_DIR / "automated_daily_pnl.jsonl"
ACCOUNT_STATE = LIVE_DIR / "account_state.json"


def log(msg: str) -> None:
    LIVE_DIR.mkdir(exist_ok=True)
    line = f"[{datetime.now(UTC).isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with RUN_LOG.open("a") as f:
        f.write(line + "\n")


def check_env() -> tuple[str, str, str, str]:
    key = os.environ.get("CAPITAL_API_KEY")
    ident = os.environ.get("CAPITAL_IDENTIFIER")
    pwd = os.environ.get("CAPITAL_API_PASSWORD")
    env = os.environ.get("CAPITAL_ENVIRONMENT", "demo")
    if not all([key, ident, pwd]):
        log("ERROR: missing CAPITAL_API_KEY / CAPITAL_IDENTIFIER / CAPITAL_API_PASSWORD")
        sys.exit(2)
    return key, ident, pwd, env


def candles_to_df(candles: list[dict]) -> pd.DataFrame:
    """Convert Capital.com /prices response candles to OHLC DataFrame indexed by UTC ts."""
    rows = []
    for c in candles:
        ts = pd.Timestamp(c["snapshotTime"], tz="UTC")
        # Capital returns bid/ask for each OHLC — use mid
        op = c["openPrice"]
        hp = c["highPrice"]
        lp = c["lowPrice"]
        cp = c["closePrice"]
        o = (float(op["bid"]) + float(op["ask"])) / 2
        h = (float(hp["bid"]) + float(hp["ask"])) / 2
        low = (float(lp["bid"]) + float(lp["ask"])) / 2
        cl = (float(cp["bid"]) + float(cp["ask"])) / 2
        rows.append({"timestamp": ts, "open": o, "high": h, "low": low, "close": cl})
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index("timestamp").sort_index()


def update_historical_csv(pair: str, fresh_daily: pd.DataFrame) -> int:
    """Bootstrap or append daily bars to M5 CSV history.
    On GitHub Actions runner the CSV is gitignored (too big), so first run
    creates it from fetched Capital bars. Subsequent runs append fresh tail.
    Returns count of new bars added."""
    if fresh_daily.empty:
        return 0
    csv_path = DATA_DIR / f"{pair.lower()}-m5-bid-2019-01-01-2026-01-01.csv"

    # BOOTSTRAP : create file with header + all fetched bars
    if not csv_path.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w") as f:
            f.write("timestamp,open,high,low,close\n")
            for ts, row in fresh_daily.iterrows():
                ts_ms = int(ts.timestamp() * 1000)
                f.write(f"{ts_ms},{row['open']},{row['high']},{row['low']},{row['close']}\n")
        log(f"  BOOTSTRAP: created {csv_path.name} with {len(fresh_daily)} bars")
        return len(fresh_daily)

    # APPEND : existing CSV → only new bars after last timestamp
    last_line = subprocess.run(
        ["tail", "-1", str(csv_path)],
        capture_output=True, text=True
    ).stdout.strip()
    if not last_line:
        return 0
    try:
        last_ms = int(last_line.split(",")[0])
        last_ts = pd.Timestamp(last_ms, unit="ms", tz="UTC")
    except (ValueError, IndexError):
        log(f"WARN: cannot parse last line of {csv_path}")
        return 0
    new_bars = fresh_daily[fresh_daily.index > last_ts]
    if new_bars.empty:
        return 0
    with csv_path.open("a") as f:
        for ts, row in new_bars.iterrows():
            ts_ms = int(ts.timestamp() * 1000)
            f.write(f"{ts_ms},{row['open']},{row['high']},{row['low']},{row['close']}\n")
    return len(new_bars)


def run_step(label: str, cmd: list[str]) -> int:
    log(f"STEP: {label} → {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(HERE), capture_output=True, text=True)
    if result.stdout:
        for line in result.stdout.splitlines()[-20:]:
            log(f"  | {line}")
    if result.returncode != 0:
        log(f"  STDERR: {result.stderr[:2000]}")
        log(f"  STEP FAILED: {label} (exit {result.returncode})")
    return result.returncode


def log_account_snapshot(client: CapitalClient) -> None:
    """Append daily snapshot to JSONL (capped at 1 entry per UTC date — overwrite latest)."""
    acc = client.account_summary()
    bal = acc.get("balance", {})
    today = datetime.now(UTC).date().isoformat()
    snapshot = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "date": today,
        "balance": float(bal.get("balance", 0)),
        "available": float(bal.get("available", 0)),
        "deposit": float(bal.get("deposit", 0)),
        "profit_loss": float(bal.get("profitLoss", 0)),
        "currency": acc.get("currency"),
    }
    LIVE_DIR.mkdir(exist_ok=True)

    # Cap 1 entry per date : rewrite file dropping any existing row with same date
    existing_rows = []
    if LATEST_PNL.exists():
        for line in LATEST_PNL.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("date") != today:
                existing_rows.append(row)
    existing_rows.append(snapshot)
    with LATEST_PNL.open("w") as f:
        for row in existing_rows:
            f.write(json.dumps(row) + "\n")

    log(f"Account snapshot: bal={snapshot['balance']:.2f} {snapshot['currency']} "
        f"avail={snapshot['available']:.2f} pl={snapshot['profit_loss']:+.2f}")


def write_account_state(client: CapitalClient, market_open: bool,
                          market_reason: str, executed: bool) -> None:
    """Write rich live/account_state.json snapshot for Telegram/dashboard consumers.

    Includes full balance, currency, environment, open positions with pricing,
    market schedule info, run context.
    """
    acc = client.account_summary()
    bal = acc.get("balance", {})

    positions_raw = []
    try:
        positions_raw = client.get_positions()
    except Exception as exc:
        log(f"WARN: positions fetch for state file failed: {exc}")

    positions: list[dict] = []
    for entry in positions_raw:
        pos = entry.get("position", {})
        market = entry.get("market", {})
        positions.append({
            "deal_id": pos.get("dealId"),
            "epic": market.get("epic"),
            "instrument": market.get("instrumentName"),
            "direction": pos.get("direction"),
            "size": float(pos.get("size", 0) or 0),
            "open_level": float(pos.get("level", 0) or 0),
            "current_bid": float(market.get("bid", 0) or 0),
            "current_offer": float(market.get("offer", 0) or 0),
            "profit_loss": float(pos.get("profit", 0) or 0),
            "currency": pos.get("currency"),
            "created": pos.get("createdDate"),
        })

    env = os.environ.get("CAPITAL_ENVIRONMENT", "demo")
    state = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "environment": env,
        "broker": "Capital.com",
        "account_id": acc.get("accountId"),
        "account_name": acc.get("accountName"),
        "currency": acc.get("currency"),
        "balance": float(bal.get("balance", 0) or 0),
        "available": float(bal.get("available", 0) or 0),
        "deposit": float(bal.get("deposit", 0) or 0),
        "profit_loss": float(bal.get("profitLoss", 0) or 0),
        "margin_used": float(bal.get("balance", 0) or 0) - float(bal.get("available", 0) or 0),
        "positions": positions,
        "positions_count": len(positions),
        "market_open": market_open,
        "market_reason": market_reason,
        "executed_this_run": executed,
    }
    LIVE_DIR.mkdir(exist_ok=True)
    ACCOUNT_STATE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    log(f"Wrote {ACCOUNT_STATE.name}: positions={len(positions)} "
        f"bal={state['balance']:.2f} env={env}")



def reconcile_positions(client: CapitalClient) -> tuple[bool, str]:
    """Compare current Capital positions to last run's account_state.json snapshot.

    Returns (matches, detail). Drift = positions opened/closed between runs by
    something other than our pipeline (manual intervention, broker auto-stop,
    margin call). Non-fatal — surface in Telegram but don't block execution.
    """
    if not ACCOUNT_STATE.exists():
        return True, "no prior state file (first run)"
    try:
        last = json.loads(ACCOUNT_STATE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return True, "prior state file unparseable"
    prior_deals = {p.get("deal_id") for p in last.get("positions", []) if p.get("deal_id")}
    try:
        live_positions = client.get_positions()
    except Exception as exc:
        return False, f"reconcile fetch failed: {exc}"
    live_deals = {p.get("position", {}).get("dealId") for p in live_positions
                   if p.get("position", {}).get("dealId")}
    missing = prior_deals - live_deals
    added = live_deals - prior_deals
    if not missing and not added:
        return True, f"OK ({len(prior_deals)} deals match)"
    parts = []
    if missing:
        parts.append(f"{len(missing)} deals disappeared since last run")
    if added:
        parts.append(f"{len(added)} unexpected new deals")
    return False, "; ".join(parts)


def circuit_breaker_check(client: CapitalClient,
                            max_daily_dd_pct: float) -> tuple[bool, str]:
    """Check if today's PnL vs today's first snapshot triggers circuit breaker.

    Returns (allowed_to_trade, reason). True = OK to execute; False = skip
    today's execution. Threshold default -2% (env DAILY_DD_BREAKER_PCT).
    """
    snapshots = []
    if LATEST_PNL.exists():
        for line in LATEST_PNL.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                snapshots.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    today = datetime.now(UTC).date().isoformat()
    today_snaps = [s for s in snapshots if s.get("date") == today]
    if not today_snaps:
        return True, "no today snapshot yet (first of day)"
    first_today = today_snaps[0]
    try:
        acc = client.account_summary()
        bal = float(acc.get("balance", {}).get("balance", 0))
    except Exception as exc:
        return True, f"balance fetch failed ({exc}); skip breaker check"
    open_bal = float(first_today.get("balance", bal))
    if open_bal <= 0:
        return True, "open_bal zero; skip breaker"
    dd_pct = (bal - open_bal) / open_bal * 100
    if dd_pct < max_daily_dd_pct:
        return False, (f"DAILY DD BREAKER: {dd_pct:+.2f}% < {max_daily_dd_pct:+.2f}% "
                        f"(open {open_bal:,.2f} → now {bal:,.2f})")
    return True, f"DD {dd_pct:+.2f}% within budget"


def market_execution_allowed(now: datetime | None = None) -> tuple[bool, str]:
    """Return whether FX execution is allowed on Capital.com at current UTC time.

    Capital.com FX timetable from rejected orders observed in logs:
    - Mon-Thu close at 20:59:50 UTC, reopen 21:05 UTC
    - Fri close at 20:59:50 UTC
    - Sun open at 21:00 UTC

    We use conservative minute-level guards to avoid submitting during closure.
    """
    current = now or datetime.now(UTC)
    weekday = current.weekday()  # Mon=0, Sun=6
    minutes = current.hour * 60 + current.minute
    if weekday == 5:
        return False, "Saturday FX market closed"
    if weekday == 4 and minutes >= 20 * 60 + 55:
        return False, "Friday post-close FX market closed"
    if weekday == 6 and minutes < 21 * 60 + 10:
        return False, "Sunday pre-open FX market closed"
    if weekday in {0, 1, 2, 3} and (20 * 60 + 55) <= minutes < (21 * 60 + 10):
        return False, "Daily maintenance break 20:55-21:10 UTC"
    return True, "FX execution window open"

def main() -> None:
    log("=" * 60)
    log("Automated daily runner started")

    key, ident, pwd, env = check_env()
    log(f"Capital env={env}")
    client = CapitalClient(key, ident, pwd, env)
    try:
        client.login()
        log(f"Logged in. account_id={client.account_id} currency={client.currency}")
    except Exception as e:
        log(f"FATAL: Capital.com login failed: {e}")
        sys.exit(2)

    try:
        log_account_snapshot(client)
    except Exception as e:
        log(f"WARN: account snapshot failed: {e}")

    log("Fetching fresh daily candles from Capital.com...")
    updated_pairs = 0
    # 200 bars : enough headroom for MR21 lookback + ~6 months stability
    for pair, epic in SYMBOL_MAP.items():
        try:
            candles = client.get_daily_candles(epic, max_bars=200)
            df = candles_to_df(candles)
            n_new = update_historical_csv(pair, df)
            log(f"  {pair} ({epic}): fetched {len(df)} bars, appended {n_new}")
            if n_new > 0:
                updated_pairs += 1
        except Exception as e:
            log(f"  ERROR fetching {pair}: {e}")
        time.sleep(0.3)
    log(f"Updated {updated_pairs}/{len(SYMBOL_MAP)} pair CSVs")

    py = sys.executable
    rc = run_step("signal_generator", [
        py, "propfirm_signal_generator.py", "--adaptive"
    ])
    if rc != 0:
        log("FATAL: signal generator failed")
        sys.exit(rc)

    try:
        tv_state = tradingview_filter.apply_live_filter_from_env(LIVE_DIR)
        log(
            "TradingView filter: "
            f"mode={tv_state.get('mode')} "
            f"valid={tv_state.get('valid_confirmations')} "
            f"accepted={tv_state.get('accepted_orders')} "
            f"rejected={tv_state.get('rejected_orders')}"
        )
    except Exception as e:
        if os.environ.get("TV_FILTER_FAIL_CLOSED", "0").lower() in {"1", "true", "yes"}:
            log(f"FATAL: TradingView filter failed: {e}")
            sys.exit(2)
        log(f"WARN: TradingView filter failed open: {e}")

    # Position reconciliation : detect broker-side drift since last run
    rec_ok, rec_msg = reconcile_positions(client)
    if rec_ok:
        log(f"Position reconciliation: {rec_msg}")
    else:
        log(f"WARN: Position reconciliation FAILED: {rec_msg}")

    delta_csv = LIVE_DIR / "prop_delta_orders_latest.csv"
    execution_allowed, execution_reason = market_execution_allowed()
    log(f"Market execution check: {execution_reason}")

    # Circuit breaker : skip execute if intraday DD exceeds budget
    breaker_pct = float(os.environ.get("DAILY_DD_BREAKER_PCT", "-2.0"))
    breaker_allowed, breaker_msg = circuit_breaker_check(client, breaker_pct)
    log(f"Circuit breaker: {breaker_msg}")

    executed = False
    if not delta_csv.exists():
        log(f"WARN: {delta_csv} not found, skipping execution")
    elif not execution_allowed:
        log(f"INFO: skipping Capital execution: {execution_reason}")
    elif not breaker_allowed:
        log(f"INFO: skipping Capital execution: {breaker_msg}")
        execution_reason = breaker_msg
        execution_allowed = False
    else:
        min_notional = os.environ.get("EXEC_MIN_NOTIONAL_USD", "500")
        max_per_order = os.environ.get("MAX_NOTIONAL_PER_ORDER_USD", "250000")
        rc = run_step("capital_executor", [
            py, "capital_connector.py", "--execute", str(delta_csv),
            "--min-notional", min_notional,
            "--live-max-notional", max_per_order,
            "--reset",  # close all existing before opening fresh — idempotent rebalance
        ])
        if rc != 0:
            log("FATAL: capital_connector failed; execution state uncertain")
            try:
                log_account_snapshot(client)
            except Exception as e:
                log(f"WARN: failure snapshot failed: {e}")
            try:
                write_account_state(
                    client,
                    execution_allowed,
                    f"capital_connector failed after market check: {execution_reason}",
                    executed=False,
                )
            except Exception as e:
                log(f"WARN: failure account_state write failed: {e}")
            sys.exit(rc)
        executed = True

    rc = run_step("live_tracker_refresh", [py, "live_tracker.py"])
    if rc != 0:
        log("WARN: live_tracker failed")

    try:
        log_account_snapshot(client)
    except Exception as e:
        log(f"WARN: final snapshot failed: {e}")

    try:
        write_account_state(client, execution_allowed, execution_reason, executed)
    except Exception as e:
        log(f"WARN: account_state write failed: {e}")

    # Generate static HTML dashboard from latest state files
    rc = run_step("dashboard_generator", [py, "dashboard_generator.py"])
    if rc != 0:
        log("WARN: dashboard_generator failed")

    # Send Telegram daily summary (success path)
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        rc = run_step("telegram_notify", [
            py, "telegram_notifier.py", "--run-summary", "--status", "success"
        ])
        if rc != 0:
            log("WARN: telegram notify failed")
    else:
        log("INFO: TELEGRAM_* env vars not set, skipping notification")

    log("Automated daily runner completed successfully")
    log("=" * 60)


if __name__ == "__main__":
    main()
