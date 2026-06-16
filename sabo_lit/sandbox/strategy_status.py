"""
Per-strategy live status recap — read-only monitoring.

    python strategy_status.py gold
    python strategy_status.py index

Reads the strategy's ISOLATED live/ dir (realized_pnl.jsonl, automated_daily_pnl.jsonl,
slippage.jsonl) and prints realized PnL, win rate, #closed trades, slippage, drawdown
from peak, and a PASS/FAIL against the pre-registered elimination criteria in its config.
No broker calls, no writes. Run it whenever to track accumulation toward the verdict.
"""
from __future__ import annotations
import json
import math
import statistics
import sys
from datetime import UTC, datetime, timedelta

from strategy_config import load_config

ANN = 252


def load_jsonl(path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "gold"
    cfg = load_config(name)
    d = cfg.live_dir
    realized = load_jsonl(d / "realized_pnl.jsonl")
    snaps = load_jsonl(d / "automated_daily_pnl.jsonl")
    slip = [float(r.get("slippage_pips", 0) or 0) for r in load_jsonl(d / "slippage.jsonl")]

    print(f"\n=== {cfg.id.upper()} status (account {cfg.account_id}) — {d} ===")
    if not realized and not snaps:
        print("  No live data yet. (First run pending / no closed trades.)")
        return

    profits = [float(r.get("profit", 0) or 0) for r in realized]
    n = len(profits)
    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p < 0]
    total = sum(profits)
    wr = len(wins) / n * 100 if n else 0.0
    pf = (sum(wins) / abs(sum(losses))) if losses else float("inf")
    cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    last7 = sum(p for p, r in zip(profits, realized) if r.get("ts", "") >= cutoff)

    # live Sharpe from daily balance changes
    bals = [float(s.get("balance", 0) or 0) for s in snaps if float(s.get("balance", 0) or 0) > 0]
    sharpe = dd = 0.0
    if len(bals) >= 3:
        rets = [bals[i] / bals[i - 1] - 1 for i in range(1, len(bals))]
        mu, sd = statistics.mean(rets), (statistics.pstdev(rets) or 0)
        sharpe = mu / sd * math.sqrt(ANN) if sd > 0 else 0.0
        peak = max(bals)
        dd = (bals[-1] - peak) / peak * 100 if peak else 0.0

    print(f"  Realized PnL : {total:+,.2f}   ({n} closed trades)")
    print(f"  Win rate     : {wr:.0f}%  ({len(wins)}W / {len(losses)}L)   profit factor {pf:.2f}")
    if wins: print(f"  Best / Worst : {max(wins):+,.2f} / {min(losses) if losses else 0:+,.2f}")
    print(f"  Last 7 days  : {last7:+,.2f}")
    print(f"  Live Sharpe  : {sharpe:+.2f}   (from {len(bals)} daily snapshots)")
    print(f"  Drawdown/peak: {dd:+.2f}%")
    if slip:
        print(f"  Slippage     : median {statistics.median(slip):.2f} pip (n={len(slip)})")

    # verdict vs pre-registered criteria
    print("  --- vs elimination criteria ---")
    def mark(ok): return "✅" if ok else "❌"
    print(f"  {mark(n >= cfg.min_trades)} trades {n}/{cfg.min_trades}")
    print(f"  {mark(sharpe >= cfg.min_live_sharpe)} live Sharpe {sharpe:+.2f} (min {cfg.min_live_sharpe})")
    print(f"  {mark(total > 0)} realized PnL > 0")
    if slip:
        med = statistics.median(slip)
        print(f"  {mark(med <= cfg.max_median_slippage_pips)} slippage {med:.2f} <= {cfg.max_median_slippage_pips} pip")
    if n < cfg.min_trades:
        print(f"  ⏳ accumulating — verdict at {cfg.min_trades} trades / {cfg.min_observation_days} days")
    print()


if __name__ == "__main__":
    main()
