"""
30-day forward-demo harness using the production FX ADAPTIVE_75_50 stack.

This is an offline rehearsal: it computes daily targets, simulates delta fills,
deducts slippage, logs daily P&L, and compares realized demo P&L against a
historical expectation band.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

import live_news_calendar as news
import strategy_propfirm_cashmax as cash


HERE = Path(__file__).parent
OUT_DIR = HERE / "demo_30day"
DEFAULT_ACCOUNTS = HERE / "propfirm_hybrid_8x200_accounts.json"
ANN_DAYS = 252
VOL_LOOKBACK = 60
ADAPTIVE_WINDOW = 126
MAX_LEVERAGE = 10.0
SEED = 20260522

ADAPTIVE_TIERS = [(0.3, 1.0), (0.0, 0.75), (-1e9, 0.5)]

FX_SPECS = {
    "FX_MR_STACK": [("EURUSD", 5), ("GBPUSD", 3), ("USDJPY", 10), ("AUDUSD", 21), ("NZDUSD", 10), ("USDCAD", 3)],
    "NO_EUR_STACK": [("GBPUSD", 3), ("USDJPY", 10), ("AUDUSD", 21), ("NZDUSD", 10), ("USDCAD", 3)],
    "COMDOLL_STACK": [("AUDUSD", 21), ("NZDUSD", 10), ("USDCAD", 3)],
}


@dataclass(frozen=True)
class Fill:
    ts: str
    date: str
    account_id: str
    instrument: str
    side: str
    target_notional_usd: float
    previous_notional_usd: float
    delta_notional_usd: float
    estimated_lots: float
    fill_price: float
    slippage_bps: float


@dataclass(frozen=True)
class DailyPnl:
    date: str
    account_id: str
    strategy: str
    gross_pnl_pct: float
    slippage_cost_pct: float
    net_pnl_pct: float
    blackout_reason: str


def load_accounts(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    accounts = []
    for account in raw.get("accounts", []):
        if account.get("enabled", True) and account["strategy"] in FX_SPECS:
            accounts.append(account)
    if not accounts:
        raise RuntimeError("no enabled production FX accounts found")
    return accounts


def adaptive_leverage(daily: pd.Series, target_vol: float) -> pd.Series:
    realized = daily.rolling(VOL_LOOKBACK).std() * math.sqrt(ANN_DAYS)
    base_leverage = (target_vol / realized).clip(upper=MAX_LEVERAGE).shift(1).fillna(1.0)
    static_returns = (daily * base_leverage).dropna()
    rolling_mean = static_returns.rolling(ADAPTIVE_WINDOW).mean()
    rolling_std = static_returns.rolling(ADAPTIVE_WINDOW).std()
    rolling_sharpe = ((rolling_mean / rolling_std) * math.sqrt(ANN_DAYS)).shift(1)

    def to_mult(value: float) -> float:
        if pd.isna(value):
            return 1.0
        for threshold, multiplier in ADAPTIVE_TIERS:
            if value >= threshold:
                return multiplier
        return 0.5

    multiplier = rolling_sharpe.map(to_mult).fillna(1.0)
    return (target_vol * multiplier / realized).clip(upper=MAX_LEVERAGE).shift(1).fillna(1.0)


def fx_signal(close: pd.Series, lookback: int, as_of: pd.Timestamp) -> float:
    history = close.loc[:as_of].dropna()
    if len(history) <= lookback:
        return 0.0
    cumulative = history.iloc[-1] / history.iloc[-1 - lookback] - 1.0
    if cumulative > 0:
        return -1.0
    if cumulative < 0:
        return 1.0
    return 0.0


def estimate_lots(pair: str, notional: float, price: float) -> float:
    if abs(notional) == 0:
        return 0.0
    if pair in {"EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"}:
        return abs(notional) / max(price, 1e-12) / 100_000
    return abs(notional) / 100_000


def target_positions(
    account: dict[str, Any],
    leverage: float,
    closes: dict[str, pd.Series],
    as_of: pd.Timestamp,
    blackout_reason: str,
) -> dict[str, dict[str, float]]:
    members = FX_SPECS[account["strategy"]]
    account_size = float(account["account_size"])
    weight = 1.0 / len(members)
    out = {}
    for pair, lookback in members:
        close = closes[pair]
        price = float(close.loc[:as_of].iloc[-1])
        signal = 0.0 if blackout_reason else fx_signal(close, lookback, as_of)
        notional = account_size * leverage * weight * signal
        out[pair] = {"target_notional": notional, "price": price}
    return out


def simulate_slippage_bps(rng: random.Random, mean_bps: float, std_bps: float) -> float:
    return max(0.0, rng.gauss(mean_bps, std_bps))


def write_jsonl(path: Path, rows: list[Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            payload = asdict(row) if hasattr(row, "__dataclass_fields__") else row
            handle.write(json.dumps(payload) + "\n")


def build_spec_returns(specs: dict[str, dict], accounts: list[dict[str, Any]]) -> dict[str, dict[str, pd.Series]]:
    paths = {}
    for account in accounts:
        spec = specs[account["strategy"]]
        leverage = adaptive_leverage(spec["daily"], float(account["target_vol"]))
        close = (spec["daily"] * leverage).dropna()
        paths[account["account_id"]] = {"daily": close, "leverage": leverage, "strategy": account["strategy"]}
    return paths


def expectation_band(paths: dict[str, dict[str, pd.Series]], start: pd.Timestamp, days: int) -> dict[str, float]:
    portfolio = pd.DataFrame({account_id: data["daily"] for account_id, data in paths.items()}).fillna(0.0).mean(axis=1)
    hist = portfolio[portfolio.index < start].dropna()
    mean = float(hist.mean()) if len(hist) else 0.0
    std = float(hist.std()) if len(hist) > 1 else 0.0
    expected = mean * days
    half_width = 1.96 * std * math.sqrt(days)
    return {
        "daily_mean": mean,
        "daily_std": std,
        "expected_cum": expected,
        "ci95_low": expected - half_width,
        "ci95_high": expected + half_width,
    }


def run_demo(args: argparse.Namespace) -> dict[str, Any]:
    rng = random.Random(args.seed)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    accounts = load_accounts(Path(args.accounts))
    specs = cash.build_strategy_universe()
    paths = build_spec_returns(specs, accounts)
    pairs = sorted({pair for members in FX_SPECS.values() for pair, _ in members})
    closes = {pair: cash.load_m5_close(pair).resample("1D").last().dropna() for pair in pairs}

    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC")
    dates = [date for date in pd.date_range(start, end, freq="D", tz="UTC") if date.weekday() < 5]
    blackout_by_date = news.blackout_map(start, end)
    current_positions: dict[tuple[str, str], float] = {}
    fills: list[Fill] = []
    daily_pnls: list[DailyPnl] = []

    for date in dates:
        blackout_reason = blackout_by_date.get(date.strftime("%Y-%m-%d"), "")
        for account in accounts:
            account_id = account["account_id"]
            account_size = float(account["account_size"])
            account_path = paths[account_id]
            leverage = float(account_path["leverage"].reindex([date]).ffill().iloc[0])
            targets = target_positions(account, leverage, closes, date, blackout_reason)
            slippage_cost_pct = 0.0
            for pair, target in targets.items():
                key = (account_id, pair)
                previous = current_positions.get(key, 0.0)
                delta = float(target["target_notional"]) - previous
                if abs(delta) < 1e-9:
                    continue
                side = "BUY" if delta > 0 else "SELL"
                slippage_bps = simulate_slippage_bps(rng, args.slippage_mean_bps, args.slippage_std_bps)
                slippage_cost_pct += abs(delta) * (slippage_bps / 10_000) / account_size
                price = float(target["price"]) * (1.0 + (slippage_bps / 10_000) * (1 if side == "BUY" else -1))
                fills.append(
                    Fill(
                        ts=datetime.now(UTC).isoformat(timespec="seconds"),
                        date=str(date.date()),
                        account_id=account_id,
                        instrument=pair,
                        side=side,
                        target_notional_usd=round(float(target["target_notional"]), 2),
                        previous_notional_usd=round(previous, 2),
                        delta_notional_usd=round(delta, 2),
                        estimated_lots=round(estimate_lots(pair, delta, float(target["price"])), 4),
                        fill_price=round(price, 8),
                        slippage_bps=round(slippage_bps, 3),
                    )
                )
                current_positions[key] = float(target["target_notional"])

            gross = 0.0 if blackout_reason else float(account_path["daily"].reindex([date]).fillna(0.0).iloc[0])
            net = gross - slippage_cost_pct
            daily_pnls.append(
                DailyPnl(
                    date=str(date.date()),
                    account_id=account_id,
                    strategy=account["strategy"],
                    gross_pnl_pct=gross,
                    slippage_cost_pct=slippage_cost_pct,
                    net_pnl_pct=net,
                    blackout_reason=blackout_reason,
                )
            )

    write_jsonl(out_dir / "fills_log.jsonl", fills)
    write_jsonl(out_dir / "daily_pnl.jsonl", daily_pnls)
    portfolio_daily = pd.Series(
        [row.net_pnl_pct for row in daily_pnls],
        index=pd.MultiIndex.from_tuples([(row.date, row.account_id) for row in daily_pnls]),
    ).groupby(level=0).mean()
    band = expectation_band(paths, start, len(portfolio_daily))
    realized_cum = float(portfolio_daily.sum())
    slippage_values = [fill.slippage_bps for fill in fills]
    alerts = []
    if realized_cum < band["ci95_low"]:
        alerts.append("CUM_PNL_BELOW_HISTORICAL_CI95_LOW")
    if any(row.net_pnl_pct <= -0.04 for row in daily_pnls):
        alerts.append("ACCOUNT_DAILY_LOSS_NEAR_PROP_LIMIT")

    payload = {
        "run_date": datetime.now(UTC).isoformat(timespec="seconds"),
        "start": str(start.date()),
        "end": str(end.date()),
        "n_trading_days": len(portfolio_daily),
        "n_fills": len(fills),
        "realized_cum_pct": realized_cum,
        "expectation_band": band,
        "mean_slippage_bps": sum(slippage_values) / len(slippage_values) if slippage_values else 0.0,
        "max_slippage_bps": max(slippage_values) if slippage_values else 0.0,
        "alerts": alerts,
        "outputs": {
            "fills": str(out_dir / "fills_log.jsonl"),
            "daily_pnl": str(out_dir / "daily_pnl.jsonl"),
            "report": str(out_dir / "demo_30day_report.md"),
        },
    }
    (out_dir / "demo_30day_metrics.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_report(out_dir / "demo_30day_report.md", payload, daily_pnls, fills)
    return payload


def write_report(path: Path, payload: dict[str, Any], daily_pnls: list[DailyPnl], fills: list[Fill]) -> None:
    band = payload["expectation_band"]
    lines = [
        "# 30-Day Demo Harness Report",
        "",
        f"Run date: {payload['run_date']}",
        f"Window: {payload['start']} → {payload['end']}",
        f"Trading days: {payload['n_trading_days']}",
        f"Fills simulated: {payload['n_fills']}",
        "",
        "## P&L vs Expected",
        "",
        f"- Realized cumulative portfolio P&L: `{payload['realized_cum_pct']*100:+.2f}%`",
        f"- Historical expected cumulative: `{band['expected_cum']*100:+.2f}%`",
        f"- Historical CI95: `[{band['ci95_low']*100:+.2f}%, {band['ci95_high']*100:+.2f}%]`",
        "",
        "## Slippage",
        "",
        f"- Mean simulated slippage: `{payload['mean_slippage_bps']:.2f} bps`",
        f"- Max simulated slippage: `{payload['max_slippage_bps']:.2f} bps`",
        "",
        "## Alerts",
        "",
    ]
    if payload["alerts"]:
        lines.extend([f"- `{alert}`" for alert in payload["alerts"]])
    else:
        lines.append("- None")
    lines.extend([
        "",
        "## Daily Portfolio P&L",
        "",
        "| date | gross% | slippage% | net% | blackout |",
        "|---|---:|---:|---:|---|",
    ])
    by_date: dict[str, list[DailyPnl]] = {}
    for row in daily_pnls:
        by_date.setdefault(row.date, []).append(row)
    for date, rows in sorted(by_date.items()):
        gross = sum(row.gross_pnl_pct for row in rows) / len(rows)
        slip = sum(row.slippage_cost_pct for row in rows) / len(rows)
        net = sum(row.net_pnl_pct for row in rows) / len(rows)
        blackout = ",".join(sorted({row.blackout_reason for row in rows if row.blackout_reason}))
        lines.append(f"| {date} | {gross*100:+.2f} | {slip*100:.3f} | {net*100:+.2f} | {blackout} |")
    lines.extend([
        "",
        "## Caveats",
        "",
        "- This is an offline rehearsal using historical prices, not a broker demo account.",
        "- Fills use synthetic slippage, not real spread/commission from a prop-firm platform.",
        "- CFD multipliers are not used because production stack is FX-only.",
        "- Use this harness to verify file flow and risk alarms; then run a real MT5/cTrader demo for execution quality.",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run 30-day offline demo harness.")
    parser.add_argument("--start", default="2025-12-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--accounts", default=str(DEFAULT_ACCOUNTS))
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--slippage-mean-bps", type=float, default=0.25)
    parser.add_argument("--slippage-std-bps", type=float, default=0.35)
    parser.add_argument("--seed", type=int, default=SEED)
    return parser.parse_args()


def main() -> None:
    payload = run_demo(parse_args())
    print("30-day demo harness complete.")
    print(f"  trading_days={payload['n_trading_days']} fills={payload['n_fills']}")
    print(f"  realized_cum={payload['realized_cum_pct']*100:+.2f}%")
    print(
        f"  expected_ci95=[{payload['expectation_band']['ci95_low']*100:+.2f}%, "
        f"{payload['expectation_band']['ci95_high']*100:+.2f}%]"
    )
    print(f"  report={payload['outputs']['report']}")


if __name__ == "__main__":
    main()
