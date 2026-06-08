"""
Generate daily target signals for the current best prop-firm portfolio.

This is a paper-order generator, not a broker connector. It computes:
  - next-session signals for each account strategy,
  - current vol-target leverage from realized strategy volatility,
  - signed notional exposure by account/instrument,
  - lockout status from optional account state.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

import live_news_calendar as news
import strategy_propfirm_cashmax as cash


HERE = Path(__file__).parent
DEFAULT_ACCOUNTS = HERE / "propfirm_hybrid_8x200_accounts.json"
DEFAULT_OUT_DIR = HERE / "live"
DEFAULT_BROKER_MAP = HERE / "broker_symbol_map.json"
ANN_DAYS = 252
VOL_LOOKBACK = 60
MAX_LEVERAGE = cash.MAX_LEVERAGE


FX_SPECS = {
    "FX_MR_STACK": [("EURUSD", 5), ("GBPUSD", 3), ("USDJPY", 10), ("AUDUSD", 21), ("NZDUSD", 10), ("USDCAD", 3)],
    "EURUSD_MR5": [("EURUSD", 5)],
    "NO_EUR_STACK": [("GBPUSD", 3), ("USDJPY", 10), ("AUDUSD", 21), ("NZDUSD", 10), ("USDCAD", 3)],
    "COMDOLL_STACK": [("AUDUSD", 21), ("NZDUSD", 10), ("USDCAD", 3)],
    "FAST_STACK": [("EURUSD", 3), ("GBPUSD", 3), ("USDCAD", 3)],
}

# H1 index/cross specs (NASDAQ/CHFJPY/DOW) and their composites were dropped
# after the Capital.com pivot removed those instruments' intraday data feed.
# Active account configs reference FX specs only, so they contributed nothing but
# a WARN per run. Removed. See git history if H1 data is ever restored.


@dataclass(frozen=True)
class Component:
    instrument: str
    source: str
    signal: float
    weight: float
    last_price: float


@dataclass(frozen=True)
class SpecState:
    daily_returns: pd.Series
    components: list[Component]


def parse_as_of(value: str | None) -> pd.Timestamp | None:
    if value is None:
        return None
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return timestamp.floor("D")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_broker_symbols(path: Path, broker: str | None) -> dict[str, str]:
    if broker is None:
        return {}
    if not path.exists():
        raise FileNotFoundError(f"broker map not found: {path}")
    raw = load_json(path)
    brokers = raw.get("brokers", {})
    if broker not in brokers:
        known = ", ".join(sorted(brokers))
        raise KeyError(f"unknown broker key {broker!r}. Known brokers: {known}")
    broker_entry = brokers[broker]
    if not broker_entry.get("verified", False):
        print(f"WARNING: broker map {broker!r} is not verified. Confirm symbols in platform before live.")
    return dict(broker_entry.get("symbols", {}))


def load_state(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    raw = load_json(path)
    return {item["account_id"]: item for item in raw.get("accounts", [])}


def load_fx_daily_closes() -> dict[str, pd.Series]:
    closes = {}
    for pair in sorted({pair for members in FX_SPECS.values() for pair, _ in members}):
        closes[pair] = cash.load_m5_close(pair).resample("1D").last().dropna()
    return closes


def latest_common_date(specs: dict[str, SpecState]) -> pd.Timestamp:
    latest_dates = []
    for spec in specs.values():
        if not spec.daily_returns.empty:
            latest_dates.append(spec.daily_returns.index.max())
    if not latest_dates:
        raise RuntimeError("no strategy data available")
    return min(latest_dates).floor("D")


def fx_signal_from_close(close: pd.Series, lookback: int, as_of: pd.Timestamp) -> float:
    history = close.loc[:as_of].dropna()
    if len(history) <= lookback:
        return 0.0
    cumulative = history.iloc[-1] / history.iloc[-1 - lookback] - 1.0
    if cumulative > 0:
        return -1.0
    if cumulative < 0:
        return 1.0
    return 0.0


def build_fx_spec(name: str, members: list[tuple[str, int]], closes: dict[str, pd.Series], as_of: pd.Timestamp) -> SpecState:
    streams = {}
    components = []
    weight = 1.0 / len(members)
    for pair, lookback in members:
        close = closes[pair]
        streams[f"{pair}_{lookback}"] = cash.pair_daily_net(pair, close, lookback)
        signal = fx_signal_from_close(close, lookback, as_of)
        components.append(
            Component(
                instrument=pair,
                source=f"{pair}_MR{lookback}",
                signal=signal,
                weight=weight,
                last_price=float(close.loc[:as_of].iloc[-1]),
            )
        )
    daily = pd.DataFrame(streams).fillna(0.0).mean(axis=1)
    return SpecState(daily_returns=daily, components=components)


def build_specs(as_of: pd.Timestamp | None) -> tuple[dict[str, SpecState], pd.Timestamp]:
    closes = load_fx_daily_closes()
    preliminary_as_of = as_of or min(series.index.max() for series in closes.values()).floor("D")

    specs: dict[str, SpecState] = {}
    for name, members in FX_SPECS.items():
        specs[name] = build_fx_spec(name, members, closes, preliminary_as_of)

    final_as_of = as_of or latest_common_date(specs)
    if final_as_of != preliminary_as_of:
        return build_specs(final_as_of)
    return specs, final_as_of


# ADAPTIVE_75_50 : validated on 16-year H1 backtest 2010-2025
# Original ADAPTIVE_50_0 was OVERFIT to 2019-2025 (lost on pre-2019 unseen data)
# 75_50 generalizes : Sh > 0.3 → 100%, 0 < Sh < 0.3 → 75%, Sh < 0 → 50%
ADAPTIVE_TIERS = [(0.3, 1.0), (0.0, 0.75), (-1e9, 0.5)]
ADAPTIVE_SHARPE_WINDOW = 126


def adaptive_multiplier(daily_returns: pd.Series, target_vol: float, as_of: pd.Timestamp) -> tuple[float, float]:
    """Compute adaptive multiplier based on rolling 126-day realized Sharpe.
    Returns (multiplier_on_base_vol, rolling_sharpe)."""
    history = daily_returns.loc[:as_of].dropna()
    if len(history) < ADAPTIVE_SHARPE_WINDOW:
        return 1.0, 0.0
    realized_vol_static = history.rolling(VOL_LOOKBACK).std() * math.sqrt(ANN_DAYS)
    static_lev = (target_vol / realized_vol_static).clip(upper=MAX_LEVERAGE).shift(1).fillna(1.0)
    static_returns = (history * static_lev).dropna()
    recent = static_returns.tail(ADAPTIVE_SHARPE_WINDOW)
    if len(recent) < 5:
        return 1.0, 0.0
    rmean = float(recent.mean())
    rstd = float(recent.std())
    rolling_sharpe = (rmean / rstd) * math.sqrt(ANN_DAYS) if rstd > 0 else 0.0
    for thr, mult in ADAPTIVE_TIERS:
        if rolling_sharpe >= thr:
            return mult, rolling_sharpe
    return ADAPTIVE_TIERS[-1][1], rolling_sharpe


def latest_leverage(daily_returns: pd.Series, target_vol: float, as_of: pd.Timestamp,
                    adaptive: bool = False) -> tuple[float, float, float, float]:
    """Returns (leverage, realized_vol, adaptive_multiplier, rolling_sharpe).
    If adaptive=True, scales target_vol by rolling-Sharpe-based multiplier."""
    history = daily_returns.loc[:as_of].dropna()
    realized_vol = float(history.tail(VOL_LOOKBACK).std() * math.sqrt(ANN_DAYS)) if len(history) >= 2 else 0.0
    if realized_vol <= 0 or math.isnan(realized_vol):
        return 0.0, realized_vol, 1.0, 0.0
    mult = 1.0
    rolling_sharpe = 0.0
    if adaptive:
        mult, rolling_sharpe = adaptive_multiplier(daily_returns, target_vol, as_of)
    effective_vol_target = target_vol * mult
    if effective_vol_target <= 0:
        return 0.0, realized_vol, mult, rolling_sharpe
    return min(MAX_LEVERAGE, effective_vol_target / realized_vol), realized_vol, mult, rolling_sharpe


def account_locked(account: dict[str, Any], state: dict[str, Any], rules: dict[str, Any]) -> tuple[bool, str]:
    if not account.get("enabled", True):
        return True, "account disabled in config"
    if state.get("manual_lock", False):
        return True, "manual lock in state"
    daily_pnl = float(state.get("daily_pnl_pct", 0.0))
    overall_pnl = float(state.get("overall_pnl_pct", 0.0))
    if daily_pnl <= float(rules.get("daily_lockout_pct", -0.04)):
        return True, f"daily pnl lockout {daily_pnl:.2%}"
    if overall_pnl <= float(rules.get("overall_lockout_pct", -0.08)):
        return True, f"overall pnl lockout {overall_pnl:.2%}"
    return False, ""


def signed_notional(account_size: float, leverage: float, component: Component) -> float:
    return account_size * leverage * component.weight * component.signal


def estimated_order_size(instrument: str, signed_notional_usd: float, last_price: float) -> tuple[float | None, float | None, str]:
    abs_notional = abs(signed_notional_usd)
    if abs_notional == 0:
        return 0.0, 0.0, "flat"
    if instrument in {"EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"} and last_price > 0:
        units = abs_notional / last_price
        return units, units / 100_000, "FX quote-USD estimate"
    if instrument in {"USDJPY", "USDCAD"}:
        units = abs_notional
        return units, units / 100_000, "FX base-USD estimate"
    return None, None, "contract sizing depends on broker symbol"


def orders_for_account(
    account: dict[str, Any],
    spec: SpecState,
    leverage: float,
    locked: bool,
    broker_symbols: dict[str, str],
) -> list[dict[str, Any]]:
    account_size = float(account["account_size"])
    rows = []
    for component in spec.components:
        notional = 0.0 if locked else signed_notional(account_size, leverage, component)
        if abs(notional) < 1e-9:
            side = "FLAT"
        elif notional > 0:
            side = "LONG"
        else:
            side = "SHORT"
        units, lots, sizing_note = estimated_order_size(component.instrument, notional, component.last_price)
        rows.append(
            {
                "account_id": account["account_id"],
                "strategy": account["strategy"],
                "instrument": component.instrument,
                "broker_symbol": broker_symbols.get(component.instrument, component.instrument),
                "source": component.source,
                "side": side,
                "signal": component.signal if not locked else 0.0,
                "weight": component.weight,
                "target_notional_usd": round(notional, 2),
                "target_pct_account": round(notional / account_size, 6),
                "estimated_units": None if units is None else round(units, 2),
                "estimated_standard_lots": None if lots is None else round(lots, 4),
                "sizing_note": sizing_note,
                "last_price": round(component.last_price, 8),
                "magic": account.get("magic"),
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    if fieldnames is None:
        fieldnames = list(rows[0].keys()) if rows else []
    if not fieldnames:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, summary: dict[str, Any], accounts: list[dict[str, Any]], orders: list[dict[str, Any]]) -> None:
    lines = [
        "# Prop Firm Daily Signals",
        "",
        f"Generated at: {summary['generated_at']}",
        f"As of close: {summary['as_of']}",
        f"Portfolio: {summary['portfolio']}",
        "",
        "## Accounts",
        "",
        "| account | strategy | target vol | leverage | realized vol | gross notional | lock |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for account in accounts:
        lines.append(
            f"| {account['account_id']} | {account['strategy']} | {account['target_vol']*100:.0f}% | "
            f"{account['leverage']:.2f}x | {account['realized_vol']*100:.2f}% | "
            f"${account['gross_notional_usd']:.0f} | {account['lock_reason']} |"
        )
    lines.extend([
        "",
        "## Non-Flat Orders",
        "",
        "| account | strategy | symbol | side | notional | pct account | source |",
        "|---|---|---|---|---:|---:|---|",
    ])
    non_flat = [row for row in orders if row["side"] != "FLAT"]
    if not non_flat:
        lines.append("| none |  |  |  | 0 | 0 |  |")
    for row in non_flat:
        lines.append(
            f"| {row['account_id']} | {row['strategy']} | {row['broker_symbol']} | {row['side']} | "
            f"${row['target_notional_usd']:.0f} | {row['target_pct_account']*100:.2f}% | {row['source']} |"
        )
    lines.extend([
        "",
        "## FX Lot Estimates",
        "",
        "| account | symbol | side | est lots | sizing note |",
        "|---|---|---|---:|---|",
    ])
    fx_rows = [row for row in non_flat if row["estimated_standard_lots"] is not None and row["estimated_standard_lots"] > 0]
    if not fx_rows:
        lines.append("| none |  |  | 0 |  |")
    for row in fx_rows:
        lines.append(
            f"| {row['account_id']} | {row['broker_symbol']} | {row['side']} | "
            f"{row['estimated_standard_lots']:.4f} | {row['sizing_note']} |"
        )
    lines.extend([
        "",
        "## Execution Notes",
        "",
        "- This file gives target exposure, not delta orders. Compare with current broker positions before trading.",
        "- If an account lock is active, all target notionals are forced to zero.",
        "- Broker symbols in config are placeholders until replaced with exact firm symbols.",
        "- CFD index sizing depends on the firm's contract specification; use target notional until broker multipliers are configured.",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def load_current_positions(path: Path | None) -> dict[tuple[str, str], float]:
    """Load broker positions JSON.
    Format: {"positions": [{"account_id": "A1", "instrument": "EURUSD", "notional_usd": 50000}, ...]}
    Returns dict keyed by (account_id, instrument)."""
    if path is None or not path.exists():
        return {}
    raw = load_json(path)
    out: dict = {}
    for row in raw.get("positions", []):
        key = (row["account_id"], row["instrument"])
        out[key] = float(row.get("notional_usd", 0.0))
    return out


def generate(args: argparse.Namespace) -> dict[str, Any]:
    config = load_json(Path(args.accounts))
    state = load_state(Path(args.state) if args.state else None)
    current_positions = load_current_positions(Path(args.positions) if args.positions else None)
    specs, as_of = build_specs(parse_as_of(args.as_of))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # News blackout check : if as_of is blackout day, force all accounts flat
    blackout_today = news.is_blackout_day(as_of)
    blackout_reason = news.reason_for_blackout(as_of) if blackout_today else ""
    if blackout_today and not args.ignore_news:
        print(f"WARNING: {as_of.date()} is news blackout ({blackout_reason}). All accounts forced flat.")

    rules = config.get("default_firm_rules", {})
    broker_symbols = load_broker_symbols(Path(args.broker_map), args.broker)
    broker_symbols.update(config.get("instrument_overrides", {}))
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    account_rows = []
    order_rows = []

    for account in config["accounts"]:
        spec_name = account["strategy"]
        if spec_name not in specs:
            raise KeyError(f"unknown strategy in account config: {spec_name}")
        spec = specs[spec_name]
        leverage, realized_vol, adapt_mult, rolling_sh = latest_leverage(
            spec.daily_returns, float(account["target_vol"]), as_of,
            adaptive=args.adaptive,
        )
        locked, lock_reason = account_locked(account, state.get(account["account_id"], {}), rules)
        # If adaptive paused (mult=0), force account flat with reason
        if args.adaptive and adapt_mult == 0.0:
            locked = True
            lock_reason = f"adaptive pause: rolling 126d Sh={rolling_sh:+.2f} < 0"
        # Apply news blackout (unless --ignore-news)
        if blackout_today and not args.ignore_news:
            locked = True
            lock_reason = f"news blackout: {blackout_reason}"
        account_orders = orders_for_account(account, spec, leverage, locked, broker_symbols)
        # Compute delta vs current positions
        for row in account_orders:
            key = (row["account_id"], row["instrument"])
            current = current_positions.get(key, 0.0)
            target = row["target_notional_usd"]
            delta = target - current
            row["current_notional_usd"] = round(current, 2)
            row["delta_notional_usd"] = round(delta, 2)
            row["delta_side"] = "BUY" if delta > 0 else ("SELL" if delta < 0 else "NONE")
        gross = sum(abs(row["target_notional_usd"]) for row in account_orders)
        delta_gross = sum(abs(row["delta_notional_usd"]) for row in account_orders)
        account_rows.append(
            {
                "account_id": account["account_id"],
                "strategy": spec_name,
                "target_vol": float(account["target_vol"]),
                "adaptive_multiplier": adapt_mult,
                "rolling_sharpe_126d": rolling_sh,
                "leverage": 0.0 if locked else leverage,
                "raw_leverage": leverage,
                "realized_vol": realized_vol,
                "gross_notional_usd": 0.0 if locked else gross,
                "delta_gross_notional_usd": delta_gross,
                "locked": locked,
                "lock_reason": lock_reason,
            }
        )
        order_rows.extend(account_orders)

    summary = {
        "generated_at": generated_at,
        "as_of": str(as_of.date()),
        "portfolio": config.get("portfolio", ""),
        "accounts_path": str(Path(args.accounts)),
        "state_path": str(Path(args.state)) if args.state else "",
        "positions_path": str(Path(args.positions)) if args.positions else "",
        "blackout_day": blackout_today,
        "blackout_reason": blackout_reason,
        "total_gross_notional_usd": round(sum(row["gross_notional_usd"] for row in account_rows), 2),
        "total_delta_notional_usd": round(sum(row["delta_gross_notional_usd"] for row in account_rows), 2),
    }
    payload = {"summary": summary, "accounts": account_rows, "orders": order_rows}

    json_path = out_dir / f"prop_signals_{as_of.date()}.json"
    csv_path = out_dir / f"prop_orders_{as_of.date()}.csv"
    md_path = out_dir / f"prop_signals_{as_of.date()}.md"
    delta_csv_path = out_dir / f"prop_delta_orders_{as_of.date()}.csv"
    latest_json = out_dir / "prop_signals_latest.json"
    latest_csv = out_dir / "prop_orders_latest.csv"
    latest_md = out_dir / "prop_signals_latest.md"
    latest_delta_csv = out_dir / "prop_delta_orders_latest.csv"

    for path in (json_path, latest_json):
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    for path in (csv_path, latest_csv):
        write_csv(path, order_rows)
    for path in (md_path, latest_md):
        write_markdown(path, summary, account_rows, order_rows)
    # Delta-only CSV : just the non-zero deltas, for execution
    delta_rows = [r for r in order_rows if abs(r.get("delta_notional_usd", 0.0)) > 1e-9]
    delta_fields = list(order_rows[0].keys()) if order_rows else None
    for path in (delta_csv_path, latest_delta_csv):
        write_csv(path, delta_rows, delta_fields)

    print(f"As of: {summary['as_of']}")
    print(f"Accounts: {len(account_rows)}")
    print(f"Orders (target): {len(order_rows)}")
    print(f"Delta orders (execute): {len(delta_rows)}")
    print(f"Total target gross: ${summary['total_gross_notional_usd']:.0f}")
    print(f"Total delta gross : ${summary['total_delta_notional_usd']:.0f}")
    if blackout_today:
        print(f"NEWS BLACKOUT: {blackout_reason} (use --ignore-news to override)")
    print(f"Wrote: {json_path}")
    print(f"Wrote: {csv_path}")
    print(f"Wrote: {md_path}")
    print(f"Wrote: {delta_csv_path}")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate daily prop-firm target signals.")
    parser.add_argument("--as-of", default=None, help="Completed close date YYYY-MM-DD. Defaults to latest common date.")
    parser.add_argument("--accounts", default=str(DEFAULT_ACCOUNTS), help="Account config JSON.")
    parser.add_argument("--state", default=None, help="Optional account state JSON for lockouts.")
    parser.add_argument("--positions", default=None, help="Optional current broker positions JSON for delta orders.")
    parser.add_argument("--broker", default=None, help="Broker key from broker_symbol_map.json, e.g. ftmo_mt5.")
    parser.add_argument("--broker-map", default=str(DEFAULT_BROKER_MAP), help="Broker symbol map JSON.")
    parser.add_argument("--ignore-news", action="store_true", help="Bypass news blackout check (NOT recommended).")
    parser.add_argument("--adaptive", action="store_true", help="Apply ADAPTIVE_75_50 regime sizing (recommended).")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Output directory.")
    return parser.parse_args()


if __name__ == "__main__":
    generate(parse_args())
