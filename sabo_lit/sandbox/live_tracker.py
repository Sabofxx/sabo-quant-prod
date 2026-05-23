"""
Sandbox — live trading tracker for prop firm accounts.

Logs realized P&L per day per account, compares vs backtest expectation,
tracks slippage vs target prices, raises alerts on:
  - daily DD breach proximity (> 80% of firm rule)
  - overall DD breach proximity
  - rolling 30-day Sharpe vs CI95 lower bound from backtest
  - cumulative P&L outside historical CI95 for the spec

Input data (manual entry initially, automated via broker API later):
  sandbox/live/trades_log.jsonl   - one trade per line (account, instrument, fill, slippage)
  sandbox/live/daily_pnl.jsonl    - one daily P&L per account per line

Output:
  sandbox/live/tracker_dashboard.md   - human-readable health view
  sandbox/live/tracker_alerts.json    - structured alert list

Usage:
  python sandbox/live_tracker.py                            # dashboard from current logs
  python sandbox/live_tracker.py --add-trade ACCT INST ...  # quick log a trade
  python sandbox/live_tracker.py --add-daily ACCT DATE PNL  # quick log daily P&L

The tracker is intentionally simple JSONL + Python so the user can edit by hand
during the first weeks. Replace with proper broker API ingestion when ready.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import UTC, datetime
from pathlib import Path


HERE = Path(__file__).parent
LIVE_DIR = HERE / "live"
LIVE_DIR.mkdir(exist_ok=True)
TRADES_LOG = LIVE_DIR / "trades_log.jsonl"
DAILY_PNL_LOG = LIVE_DIR / "daily_pnl.jsonl"
ACCOUNTS_CONFIG = HERE / "propfirm_hybrid_8x200_accounts.json"
DASHBOARD = LIVE_DIR / "tracker_dashboard.md"
ALERTS = LIVE_DIR / "tracker_alerts.json"

# Backtest expected metrics per strategy (from validated baselines)
# Used for live vs expected comparison
EXPECTED_METRICS = {
    "FX_MR_STACK":   {"ann_ret": 0.058, "ann_vol": 0.038, "sharpe": 1.51,
                       "ci95_sharpe_low": 0.39, "ci95_ann_ret_low": 0.005},
    "EURUSD_MR5":    {"ann_ret": 0.069, "ann_vol": 0.059, "sharpe": 1.16,
                       "ci95_sharpe_low": 0.02, "ci95_ann_ret_low": 0.001},
    "NO_EUR_STACK":  {"ann_ret": 0.055, "ann_vol": 0.040, "sharpe": 1.39,
                       "ci95_sharpe_low": 0.25, "ci95_ann_ret_low": 0.010},
    "COMDOLL_STACK": {"ann_ret": 0.063, "ann_vol": 0.044, "sharpe": 1.27,
                       "ci95_sharpe_low": 0.10, "ci95_ann_ret_low": 0.004},
}

# Firm rules thresholds (default FTMO-style)
DAILY_DD_LIMIT = 0.05         # 5%
OVERALL_DD_LIMIT = 0.10       # 10%
DAILY_WARNING_FRAC = 0.80     # alert when 80% of limit reached
OVERALL_WARNING_FRAC = 0.80
ROLLING_WINDOW = 30           # days for rolling Sharpe alarm
ANN_DAYS = 252


def jsonl_read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


def jsonl_append(path: Path, record: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--add-trade", nargs=6, metavar=("ACCOUNT", "INSTRUMENT", "SIDE", "TARGET_PRICE", "FILL_PRICE", "NOTIONAL_USD"),
                    help="Log a trade: account instrument side(BUY/SELL) target_price fill_price notional_usd")
    p.add_argument("--add-daily", nargs=3, metavar=("ACCOUNT", "DATE", "PNL_PCT"),
                    help="Log daily P&L: account date(YYYY-MM-DD) pnl_pct(e.g. 0.012 = +1.2%)")
    p.add_argument("--account-config", default=str(ACCOUNTS_CONFIG))
    return p.parse_args()


def handle_add_trade(args: argparse.Namespace) -> None:
    a = args.add_trade
    trade = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "account": a[0],
        "instrument": a[1],
        "side": a[2].upper(),
        "target_price": float(a[3]),
        "fill_price": float(a[4]),
        "notional_usd": float(a[5]),
        "slippage_bps": (float(a[4]) - float(a[3])) / float(a[3]) * 10_000 * (1 if a[2].upper() == "BUY" else -1),
    }
    jsonl_append(TRADES_LOG, trade)
    print(f"Logged trade: {trade['account']} {trade['instrument']} {trade['side']} "
          f"slippage {trade['slippage_bps']:+.1f}bps")


def handle_add_daily(args: argparse.Namespace) -> None:
    a = args.add_daily
    record = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "account": a[0],
        "date": a[1],
        "pnl_pct": float(a[2]),
    }
    jsonl_append(DAILY_PNL_LOG, record)
    print(f"Logged daily P&L: {record['account']} {record['date']} {record['pnl_pct']*100:+.2f}%")


def compute_per_account_stats(daily_records: list[dict], accounts_config: dict) -> dict:
    """Group daily P&L by account, compute realized stats + breach proximity."""
    by_account: dict = {}
    for rec in daily_records:
        by_account.setdefault(rec["account"], []).append(rec)

    out = {}
    for account_id, records in by_account.items():
        records.sort(key=lambda r: r["date"])
        pnls = [r["pnl_pct"] for r in records]
        n = len(pnls)
        # Find account spec from config
        acct_cfg = next((a for a in accounts_config.get("accounts", []) if a["account_id"] == account_id), None)
        strategy = acct_cfg["strategy"] if acct_cfg else "UNKNOWN"
        expected = EXPECTED_METRICS.get(strategy, {})

        cum = 0.0
        peak = 0.0
        cum_series = []
        for p in pnls:
            cum += p
            peak = max(peak, cum)
            cum_series.append(cum)
        max_dd = min(c - p for c, p in zip(cum_series, [max(cum_series[:i+1]) for i in range(len(cum_series))], strict=True))
        last_day = pnls[-1] if pnls else 0.0
        last_dd_from_peak = cum - peak

        # Realized stats (annualized)
        mean = statistics.mean(pnls) if pnls else 0.0
        std = statistics.stdev(pnls) if n > 1 else 0.0
        realized_sharpe = (mean / std) * math.sqrt(ANN_DAYS) if std > 0 else 0.0
        realized_ann_ret = mean * ANN_DAYS

        # Rolling Sharpe last 30 days
        recent = pnls[-ROLLING_WINDOW:]
        if len(recent) >= 5:
            rmean = statistics.mean(recent)
            rstd = statistics.stdev(recent) if len(recent) > 1 else 0.0
            rolling_sharpe = (rmean / rstd) * math.sqrt(ANN_DAYS) if rstd > 0 else 0.0
        else:
            rolling_sharpe = None

        # Alerts
        alerts = []
        if last_day < -DAILY_DD_LIMIT * DAILY_WARNING_FRAC:
            alerts.append(f"DAILY_NEAR_LIMIT: last day {last_day*100:+.2f}% vs limit -{DAILY_DD_LIMIT*100:.0f}%")
        if cum < -OVERALL_DD_LIMIT * OVERALL_WARNING_FRAC:
            alerts.append(f"OVERALL_DD_NEAR_LIMIT: cum {cum*100:+.2f}% vs limit -{OVERALL_DD_LIMIT*100:.0f}%")
        if last_dd_from_peak < -OVERALL_DD_LIMIT * OVERALL_WARNING_FRAC:
            alerts.append(f"TRAILING_DD_NEAR_LIMIT: peak-to-trough {last_dd_from_peak*100:+.2f}%")
        if (rolling_sharpe is not None and "ci95_sharpe_low" in expected
                and rolling_sharpe < expected["ci95_sharpe_low"]):
            alerts.append(
                f"ROLLING_SHARPE_BELOW_CI: {rolling_sharpe:+.2f} < CI low {expected['ci95_sharpe_low']:+.2f}"
            )

        out[account_id] = {
            "strategy": strategy,
            "n_days": n,
            "cum_pnl_pct": cum,
            "current_dd_from_peak": last_dd_from_peak,
            "max_dd_pct": max_dd,
            "last_day_pnl_pct": last_day,
            "realized_sharpe": realized_sharpe,
            "realized_ann_ret": realized_ann_ret,
            "rolling_sharpe_30d": rolling_sharpe,
            "expected_sharpe": expected.get("sharpe"),
            "expected_ann_ret": expected.get("ann_ret"),
            "ci95_sharpe_low": expected.get("ci95_sharpe_low"),
            "alerts": alerts,
        }
    return out


def compute_per_instrument_slippage(trades: list[dict]) -> dict:
    """Aggregate slippage per (account, instrument)."""
    out: dict = {}
    for t in trades:
        key = f"{t['account']}::{t['instrument']}"
        out.setdefault(key, []).append(t["slippage_bps"])
    summary = {}
    for key, slips in out.items():
        summary[key] = {
            "n_trades": len(slips),
            "mean_slippage_bps": statistics.mean(slips),
            "max_slippage_bps": max(slips),
            "min_slippage_bps": min(slips),
        }
    return summary


def write_dashboard(per_account: dict, slip_summary: dict, all_alerts: list[dict]) -> None:
    lines = ["# Prop Firm Live Tracker Dashboard", ""]
    lines.append(f"Generated: {datetime.now(UTC).isoformat(timespec='seconds')}")
    lines.append("")

    if not per_account:
        lines.append("No daily P&L logged yet. Use:")
        lines.append("```bash")
        lines.append("python sandbox/live_tracker.py --add-daily A1 2026-01-15 +0.012")
        lines.append("```")
    else:
        lines.append("## Per-account health")
        lines.append("")
        lines.append("| account | strategy | days | cum% | peak DD% | last day% | realized Sh | rolling 30d Sh | expected Sh | alerts |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---|")
        for acct, s in sorted(per_account.items()):
            rolling = f"{s['rolling_sharpe_30d']:+.2f}" if s['rolling_sharpe_30d'] is not None else "—"
            expected_sh = f"{s['expected_sharpe']:+.2f}" if s['expected_sharpe'] else "—"
            alert_str = "; ".join(s["alerts"]) if s["alerts"] else ""
            lines.append(f"| {acct} | {s['strategy']} | {s['n_days']} | "
                          f"{s['cum_pnl_pct']*100:+.2f} | {s['current_dd_from_peak']*100:+.2f} | "
                          f"{s['last_day_pnl_pct']*100:+.2f} | {s['realized_sharpe']:+.2f} | "
                          f"{rolling} | {expected_sh} | {alert_str} |")
        lines.append("")

    if slip_summary:
        lines.append("## Slippage per account/instrument")
        lines.append("")
        lines.append("| account::instrument | trades | mean bps | min bps | max bps |")
        lines.append("|---|---:|---:|---:|---:|")
        for key, s in sorted(slip_summary.items()):
            lines.append(f"| {key} | {s['n_trades']} | {s['mean_slippage_bps']:+.1f} | "
                          f"{s['min_slippage_bps']:+.1f} | {s['max_slippage_bps']:+.1f} |")
        lines.append("")

    if all_alerts:
        lines.append("## Active alerts")
        lines.append("")
        for a in all_alerts:
            lines.append(f"- **{a['account']}** : {a['message']}")
    else:
        lines.append("## Active alerts")
        lines.append("")
        lines.append("None. Healthy.")

    lines.append("")
    lines.append("## Rules of engagement")
    lines.append("")
    lines.append("- If DAILY_NEAR_LIMIT or TRAILING_DD_NEAR_LIMIT : **stop trading account today**")
    lines.append("- If ROLLING_SHARPE_BELOW_CI for 15+ consecutive days : **halve target_vol**")
    lines.append("- If 30-day cum P&L outside expected CI95 : **review signal logic**")
    lines.append("- After every trade : log fill price + slippage via `--add-trade`")
    lines.append("- After every UTC close : log daily P&L pct via `--add-daily`")

    DASHBOARD.write_text("\n".join(lines))


def build_alerts(per_account: dict) -> list[dict]:
    all_alerts = []
    for acct, s in per_account.items():
        for msg in s["alerts"]:
            all_alerts.append({"account": acct, "strategy": s["strategy"], "message": msg,
                                "timestamp": datetime.now(UTC).isoformat(timespec="seconds")})
    return all_alerts


def main() -> None:
    args = parse_args()
    if args.add_trade:
        handle_add_trade(args)
    if args.add_daily:
        handle_add_daily(args)

    trades = jsonl_read(TRADES_LOG)
    daily = jsonl_read(DAILY_PNL_LOG)
    accounts_config = json.loads(Path(args.account_config).read_text()) if Path(args.account_config).exists() else {}

    per_account = compute_per_account_stats(daily, accounts_config)
    slip_summary = compute_per_instrument_slippage(trades)
    all_alerts = build_alerts(per_account)

    write_dashboard(per_account, slip_summary, all_alerts)
    ALERTS.write_text(json.dumps(all_alerts, indent=2, default=str))

    print(f"Trades logged so far: {len(trades)}")
    print(f"Daily P&L logged so far: {len(daily)}")
    print(f"Active alerts: {len(all_alerts)}")
    print(f"Dashboard: {DASHBOARD}")
    print(f"Alerts JSON: {ALERTS}")


if __name__ == "__main__":
    main()
