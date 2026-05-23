"""
Sandbox — walk-forward lookback retraining audit.

Current production uses FROZEN per-pair best lookbacks:
  EURUSD MR5 | GBPUSD MR3 | USDJPY MR10 | AUDUSD MR21 | NZDUSD MR10 | USDCAD MR3

Selected once using full 2019-2025 OOS Sharpe. Question: does this generalize
forward, or does the "best lookback" drift over time?

Walk-forward audit:
  - At each year-end, refit best lookback per pair using past 3 years training
  - Apply refitted lookback to NEXT year (OOS for that fold)
  - Measure: per-pair Sharpe per year + portfolio Sharpe per year
  - Compare: frozen lookback Sharpe vs walk-forward Sharpe

If walk-forward beats frozen → frozen is suboptimal, time to retrain schedule.
If frozen beats walk-forward → frozen is stable, no retraining needed.
If they're similar → either works ; pick simplicity.

Production implication: how often to refit (annually vs every 2 years vs never)?
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

import strategy_propfirm_cashmax as cash


HERE = Path(__file__).parent
OUT_REPORT = HERE / "walkforward_lookback_audit_report.md"
OUT_METRICS = HERE / "walkforward_lookback_audit_metrics.json"

ANN_DAYS = 252
LOOKBACKS_TESTED = [3, 5, 10, 21]
TRAIN_YEARS = 3  # rolling training window
FX_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD"]
FROZEN_BEST_LB = {"EURUSD": 5, "GBPUSD": 3, "USDJPY": 10,
                   "AUDUSD": 21, "NZDUSD": 10, "USDCAD": 3}


def pair_daily_net_for_lb(pair: str, daily_close: pd.Series, lookback: int) -> pd.Series:
    return cash.pair_daily_net(pair, daily_close, lookback)


def metrics(pl: pd.Series) -> dict:
    pl = pl.dropna()
    if len(pl) < 30:
        return {"n": len(pl), "sharpe": 0.0, "ann_ret": 0.0}
    m = float(pl.mean())
    s = float(pl.std())
    sharpe = (m / s) * math.sqrt(ANN_DAYS) if s > 0 else 0.0
    return {"n": len(pl), "sharpe": sharpe, "ann_ret": m * ANN_DAYS}


def main() -> None:
    print("Loading M5 closes...")
    m5_closes = {p: cash.load_m5_close(p) for p in FX_PAIRS}
    daily_closes = {p: m5_closes[p].resample("1D").last().dropna() for p in FX_PAIRS}

    # Build per-pair per-lookback daily streams (full history)
    streams: dict = {}
    for p in FX_PAIRS:
        streams[p] = {}
        for lb in LOOKBACKS_TESTED:
            streams[p][lb] = pair_daily_net_for_lb(p, daily_closes[p], lb)

    # Walk-forward years: for each test year Y in 2022..2025, train on Y-3..Y-1
    test_years = [2022, 2023, 2024, 2025]
    walkforward: dict = {}
    frozen: dict = {}
    for year in test_years:
        train_start = pd.Timestamp(f"{year - TRAIN_YEARS}-01-01", tz="UTC")
        train_end = pd.Timestamp(f"{year - 1}-12-31 23:59:59", tz="UTC")
        test_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        test_end = pd.Timestamp(f"{year}-12-31 23:59:59", tz="UTC")
        print(f"\n=== Year {year} | train {train_start.date()}→{train_end.date()} | "
              f"test {test_start.date()}→{test_end.date()} ===")

        # Per pair, find best lookback on train, apply on test
        per_pair_walkforward_test = {}
        per_pair_frozen_test = {}
        best_lb_picks = {}
        for p in FX_PAIRS:
            # Pick best LB on train
            train_sharpes = {}
            for lb in LOOKBACKS_TESTED:
                pl_train = streams[p][lb][(streams[p][lb].index >= train_start)
                                            & (streams[p][lb].index <= train_end)]
                train_sharpes[lb] = metrics(pl_train)["sharpe"]
            best_lb = max(LOOKBACKS_TESTED, key=lambda x: train_sharpes[x])
            best_lb_picks[p] = best_lb

            # Apply on test
            wf_pl = streams[p][best_lb][(streams[p][best_lb].index >= test_start)
                                          & (streams[p][best_lb].index <= test_end)]
            per_pair_walkforward_test[p] = wf_pl

            # Frozen baseline = use FROZEN_BEST_LB
            frozen_lb = FROZEN_BEST_LB[p]
            frozen_pl = streams[p][frozen_lb][(streams[p][frozen_lb].index >= test_start)
                                                & (streams[p][frozen_lb].index <= test_end)]
            per_pair_frozen_test[p] = frozen_pl

            print(f"  {p}: train best LB={best_lb} (frozen={frozen_lb}) "
                  f"WF test Sh={metrics(wf_pl)['sharpe']:+.2f} "
                  f"frozen test Sh={metrics(frozen_pl)['sharpe']:+.2f}")

        # Aggregate equal-weight portfolio for the year
        wf_stack = pd.DataFrame(per_pair_walkforward_test).fillna(0.0).mean(axis=1)
        frozen_stack = pd.DataFrame(per_pair_frozen_test).fillna(0.0).mean(axis=1)
        m_wf = metrics(wf_stack)
        m_frozen = metrics(frozen_stack)
        walkforward[year] = {"sharpe": m_wf["sharpe"], "ann_ret": m_wf["ann_ret"],
                              "n": m_wf["n"], "best_lb_picks": best_lb_picks}
        frozen[year] = {"sharpe": m_frozen["sharpe"], "ann_ret": m_frozen["ann_ret"],
                         "n": m_frozen["n"]}
        print(f"  Portfolio year {year}: WF Sh={m_wf['sharpe']:+.2f} "
              f"frozen Sh={m_frozen['sharpe']:+.2f}")

    # Summary
    print("\n## Summary (portfolio-level per year)")
    print(f"{'year':<6} | {'WF Sh':>7} | {'frozen Sh':>10} | {'delta':>7} | best_LB picks (WF)")
    delta_summary = []
    for year in test_years:
        delta = walkforward[year]["sharpe"] - frozen[year]["sharpe"]
        delta_summary.append(delta)
        picks_str = ", ".join(f"{p}:{lb}" for p, lb in walkforward[year]["best_lb_picks"].items())
        print(f"{year:<6} | {walkforward[year]['sharpe']:>+7.2f} | "
              f"{frozen[year]['sharpe']:>+10.2f} | {delta:>+7.2f} | {picks_str}")

    avg_delta = sum(delta_summary) / len(delta_summary)
    print(f"\nAverage Δ (WF - frozen) Sharpe across {len(test_years)} years: {avg_delta:+.2f}")

    if avg_delta > 0.1:
        verdict = "WALK-FORWARD WINS : refit annually recommended"
    elif avg_delta < -0.1:
        verdict = "FROZEN WINS : frozen is more stable, do NOT refit"
    else:
        verdict = "INDIFFERENT : both work, keep frozen (simpler ops)"

    # Report
    lines: list[str] = []
    def emit(s: str = "") -> None:
        lines.append(s)
    emit("# Walk-Forward Lookback Audit")
    emit("")
    emit("Tests whether the FROZEN per-pair best lookback selection (chosen once on full")
    emit("2019-2025 OOS) generalizes forward, vs annual refit on rolling 3-year train.")
    emit("")
    emit(f"Frozen lookbacks: {FROZEN_BEST_LB}")
    emit("")

    emit("## Per-year portfolio Sharpe : walk-forward vs frozen")
    emit("| year | WF Sharpe | frozen Sharpe | Δ (WF-frozen) | best LB picks (WF) |")
    emit("|---|---:|---:|---:|---|")
    for year in test_years:
        delta = walkforward[year]["sharpe"] - frozen[year]["sharpe"]
        picks_str = ", ".join(f"{p}:{lb}" for p, lb in walkforward[year]["best_lb_picks"].items())
        emit(f"| {year} | {walkforward[year]['sharpe']:+.2f} | "
             f"{frozen[year]['sharpe']:+.2f} | {delta:+.2f} | {picks_str} |")
    emit(f"\nAverage Δ Sharpe: **{avg_delta:+.2f}**")
    emit("")

    emit("## Lookback drift per pair across years (WF picks)")
    emit("| pair | 2022 | 2023 | 2024 | 2025 | frozen | stable? |")
    emit("|---|---:|---:|---:|---:|---:|---|")
    for p in FX_PAIRS:
        row = [str(walkforward[y]["best_lb_picks"][p]) for y in test_years]
        frozen_lb = FROZEN_BEST_LB[p]
        stable = "yes" if len(set(walkforward[y]["best_lb_picks"][p] for y in test_years)) == 1 else "DRIFT"
        emit(f"| {p} | {row[0]} | {row[1]} | {row[2]} | {row[3]} | {frozen_lb} | {stable} |")
    emit("")

    emit(f"## VERDICT: {verdict}")
    emit("")
    emit("Operational implication:")
    if "WALK-FORWARD WINS" in verdict:
        emit("- Add annual retraining schedule to production")
        emit("- Refit each January using past 3 years OOS Sharpe per pair")
        emit("- Update signal generator with new lookbacks")
    elif "FROZEN WINS" in verdict:
        emit("- Keep frozen lookbacks indefinitely")
        emit("- Annual retraining adds noise, hurts forward performance")
        emit("- Simplifies ops dramatically")
    else:
        emit("- Either approach works")
        emit("- Recommend KEEPING FROZEN for operational simplicity")
        emit("- Re-audit annually to confirm no major drift")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))
    OUT_METRICS.write_text(json.dumps({
        "walkforward": walkforward,
        "frozen": frozen,
        "avg_delta": avg_delta,
        "verdict": verdict,
        "lookbacks_tested": LOOKBACKS_TESTED,
        "frozen_best_lb": FROZEN_BEST_LB,
    }, indent=2, default=str))
    print(f"\ndone\nfiles: {OUT_REPORT.name}, {OUT_METRICS.name}")
    print(f"verdict: {verdict}")


if __name__ == "__main__":
    main()
