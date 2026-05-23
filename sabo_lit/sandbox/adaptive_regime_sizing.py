"""
Sandbox — adaptive regime sizing to address weak-edge regime sensitivity.

Problem identified by 16-year backtest (2010-2025) and QuantStats:
  - Full-sample Sharpe 0.24-0.42 (research-grade, not strong)
  - Edge concentrated in specific regimes (2010-2012, 2014-2016, 2023-2025)
  - Bad regimes (2013, 2017, 2020, 2022) bleed strategy

Solution : adaptive leverage based on rolling realized strategy Sharpe.
  - If rolling 126-day Sharpe > 0.5  → 100% leverage (vol-target 10%)
  - If 0 < rolling Sharpe < 0.5      → 50% leverage (vol-target 5%)
  - If rolling Sharpe < 0            → 25% leverage (vol-target 2.5%)
                                       (small position keeps signal alive
                                        but limits damage during bad regimes)
  - Optionally: rolling Sharpe < -0.5 → 0% (full pause)

This trades some upside in good regimes for protection in bad regimes.
Tests whether adaptive sizing improves the 7-year and 16-year Calmar ratios.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

import strategy_propfirm_cashmax as cash


HERE = Path(__file__).parent
OUT_REPORT = HERE / "adaptive_regime_sizing_report.md"
OUT_METRICS = HERE / "adaptive_regime_sizing_metrics.json"

ANN_DAYS = 252
VOL_TARGET_BASE = 0.10
ROLLING_SHARPE_WINDOW = 126  # ~6 months
MAX_LEVERAGE = 10.0
SPECS_TO_TEST = ["FX_MR_STACK", "NO_EUR_STACK", "COMDOLL_STACK"]


def static_vol_target_returns(daily: pd.Series, target_vol: float) -> pd.Series:
    """Static vol-target sizing (baseline = current production)."""
    realized = daily.rolling(60).std() * math.sqrt(ANN_DAYS)
    lev = (target_vol / realized).clip(upper=MAX_LEVERAGE).shift(1).fillna(1.0)
    return (daily * lev).dropna()


def adaptive_regime_returns(daily: pd.Series, base_vol: float,
                             sharpe_window: int = 126,
                             tiers: list[tuple[float, float]] | None = None
                             ) -> tuple[pd.Series, pd.Series]:
    """Apply adaptive sizing based on rolling realized Sharpe.

    tiers : list of (sharpe_threshold, multiplier_on_base_vol).
            E.g. [(0.5, 1.0), (0.0, 0.5), (-1e9, 0.25)] means:
              rolling Sh >= 0.5 → 1.0× base_vol
              0    <= Sh < 0.5  → 0.5× base_vol
              else               → 0.25× base_vol
    Returns (adaptive_levered_returns, applied_multiplier_series)."""
    if tiers is None:
        tiers = [(0.5, 1.0), (0.0, 0.5), (-1e9, 0.25)]

    realized_vol = daily.rolling(60).std() * math.sqrt(ANN_DAYS)
    # Rolling Sharpe based on STATIC vol-target (so the rolling judgment is fair)
    static_lev = (base_vol / realized_vol).clip(upper=MAX_LEVERAGE).shift(1).fillna(1.0)
    static_returns = (daily * static_lev).dropna()
    # Rolling Sharpe annualized
    rolling_mean = static_returns.rolling(sharpe_window).mean()
    rolling_std = static_returns.rolling(sharpe_window).std()
    rolling_sharpe = (rolling_mean / rolling_std) * math.sqrt(ANN_DAYS)
    rolling_sharpe = rolling_sharpe.shift(1)  # avoid look-ahead

    # Map rolling Sharpe to multiplier per tiers
    def to_multiplier(sh):
        if pd.isna(sh):
            return 1.0  # default before warmup
        for threshold, mult in tiers:
            if sh >= threshold:
                return mult
        return tiers[-1][1]

    multiplier = rolling_sharpe.map(to_multiplier).fillna(1.0)

    # Apply : effective vol-target per day = base_vol × multiplier
    effective_lev = (base_vol * multiplier / realized_vol).clip(upper=MAX_LEVERAGE).shift(1).fillna(1.0)
    adaptive_returns = (daily * effective_lev).dropna()
    return adaptive_returns, multiplier


def metrics(pl: pd.Series) -> dict:
    pl = pl.dropna()
    if len(pl) < 30:
        return {"n": len(pl), "sharpe": 0.0, "ann_ret": 0.0, "max_dd": 0.0, "calmar": 0.0}
    mean = float(pl.mean())
    std = float(pl.std())
    sharpe = (mean / std) * math.sqrt(ANN_DAYS) if std > 0 else 0.0
    cum = pl.cumsum()
    dd = float((cum - cum.cummax()).min())
    calmar = (mean * ANN_DAYS) / abs(dd) if dd < 0 else float("inf")
    return {"n": len(pl), "sharpe": sharpe, "ann_ret": mean * ANN_DAYS,
            "max_dd": dd, "calmar": calmar}


def per_year_metrics(pl: pd.Series) -> dict:
    out = {}
    for year, group in pl.groupby(pl.index.year):
        if len(group) < 30:
            continue
        m = metrics(group)
        out[int(year)] = {"sharpe": m["sharpe"], "ann_ret": m["ann_ret"]}
    return out


def main() -> None:
    print("Loading FX strategy universe...")
    specs = cash.build_strategy_universe()

    # Test adaptive sizing : 3 tier configurations
    tier_configs = {
        "STATIC_baseline": None,  # marker for baseline (no adaptive)
        "ADAPTIVE_50_25": [(0.5, 1.0), (0.0, 0.5), (-1e9, 0.25)],     # 100/50/25
        "ADAPTIVE_75_50": [(0.5, 1.0), (0.0, 0.75), (-1e9, 0.50)],    # 100/75/50 (gentler)
        "ADAPTIVE_50_0":  [(0.5, 1.0), (0.0, 0.5), (-1e9, 0.0)],      # 100/50/0 (drastic)
    }

    results: dict = {}
    multipliers: dict = {}
    for spec_name in SPECS_TO_TEST:
        if spec_name not in specs:
            continue
        daily = specs[spec_name]["daily"]
        results[spec_name] = {}
        for config_name, tiers in tier_configs.items():
            if tiers is None:  # static baseline
                lev_returns = static_vol_target_returns(daily, VOL_TARGET_BASE)
            else:
                lev_returns, mult = adaptive_regime_returns(daily, VOL_TARGET_BASE,
                                                              ROLLING_SHARPE_WINDOW, tiers)
                multipliers[(spec_name, config_name)] = mult
            m_full = metrics(lev_returns)
            m_2019_22 = metrics(lev_returns[(lev_returns.index >= pd.Timestamp("2019-01-01", tz="UTC"))
                                              & (lev_returns.index <= pd.Timestamp("2022-12-31", tz="UTC"))])
            m_2023_25 = metrics(lev_returns[lev_returns.index >= pd.Timestamp("2023-01-01", tz="UTC")])
            results[spec_name][config_name] = {
                "full": m_full,
                "2019_22": m_2019_22,
                "2023_25": m_2023_25,
                "by_year": per_year_metrics(lev_returns),
            }
            print(f"  {spec_name:<14} | {config_name:<18} | "
                  f"FULL Sh={m_full['sharpe']:+.2f} ann={m_full['ann_ret']*100:+.1f}% "
                  f"DD={m_full['max_dd']*100:+.1f}% Calmar={m_full['calmar']:.2f}")
            print(f"  {spec_name:<14} | {config_name:<18} | "
                  f"2019-22 Sh={m_2019_22['sharpe']:+.2f}  2023-25 Sh={m_2023_25['sharpe']:+.2f}")
        print()

    # Report
    lines: list[str] = []
    def emit(s: str = "") -> None:
        lines.append(s)
    emit("# Adaptive Regime Sizing Audit")
    emit("")
    emit("Problem: 7-year FULL-sample Sharpe is only 0.42 (FX_MR_STACK 10% vol-target).")
    emit("Edge concentrated in good regimes. Bad regimes (2017, 2020, 2022) bleed.")
    emit("")
    emit("Solution tested: scale leverage by rolling 126-day realized Sharpe.")
    emit("- High Sharpe regime (rolling > 0.5) : full vol-target")
    emit("- Mid Sharpe (0 to 0.5) : 50%-75% vol-target")
    emit("- Negative Sharpe : 0%-50% vol-target")
    emit("")

    emit("## Full-sample comparison (per spec × config)")
    emit("| spec | config | Sharpe | ann_ret% | max_dd% | Calmar |")
    emit("|---|---|---:|---:|---:|---:|")
    for spec_name, configs in results.items():
        for config_name, r in configs.items():
            f = r["full"]
            emit(f"| {spec_name} | {config_name} | {f['sharpe']:+.2f} | "
                 f"{f['ann_ret']*100:+.1f} | {f['max_dd']*100:+.1f} | {f['calmar']:.2f} |")
    emit("")

    emit("## Per-regime comparison (2019-22 bad regime, 2023-25 good regime)")
    emit("| spec | config | 2019-22 Sh | 2023-25 Sh | improvement vs static |")
    emit("|---|---|---:|---:|---|")
    for spec_name, configs in results.items():
        static_19_22 = configs["STATIC_baseline"]["2019_22"]["sharpe"]
        static_23_25 = configs["STATIC_baseline"]["2023_25"]["sharpe"]
        for config_name, r in configs.items():
            sh_19_22 = r["2019_22"]["sharpe"]
            sh_23_25 = r["2023_25"]["sharpe"]
            improvement = "—"
            if config_name != "STATIC_baseline":
                bad_improvement = sh_19_22 - static_19_22
                good_change = sh_23_25 - static_23_25
                improvement = f"bad +{bad_improvement:+.2f}, good {good_change:+.2f}"
            emit(f"| {spec_name} | {config_name} | {sh_19_22:+.2f} | "
                 f"{sh_23_25:+.2f} | {improvement} |")
    emit("")

    # Find best adaptive config per spec
    emit("## Best adaptive config per spec (by full-sample Calmar)")
    emit("| spec | best config | static Calmar | best Calmar | improvement |")
    emit("|---|---|---:|---:|---:|")
    best_configs = {}
    for spec_name, configs in results.items():
        static_calmar = configs["STATIC_baseline"]["full"]["calmar"]
        best = max(
            (c for c in configs if c != "STATIC_baseline"),
            key=lambda c: configs[c]["full"]["calmar"]
            if configs[c]["full"]["calmar"] != float("inf") else 0
        )
        best_calmar = configs[best]["full"]["calmar"]
        improvement = best_calmar - static_calmar
        emit(f"| {spec_name} | {best} | {static_calmar:.2f} | {best_calmar:.2f} | "
             f"{improvement:+.2f} |")
        best_configs[spec_name] = best
    emit("")

    emit("## Per-year Sharpe (FX_MR_STACK)")
    emit("| year | STATIC | ADAPTIVE_50_25 | ADAPTIVE_75_50 | ADAPTIVE_50_0 |")
    emit("|---|---:|---:|---:|---:|")
    by_year_static = results["FX_MR_STACK"]["STATIC_baseline"]["by_year"]
    for year in sorted(by_year_static):
        row = f"| {year} | {by_year_static[year]['sharpe']:+.2f} |"
        for cfg in ("ADAPTIVE_50_25", "ADAPTIVE_75_50", "ADAPTIVE_50_0"):
            v = results["FX_MR_STACK"][cfg]["by_year"].get(year, {"sharpe": 0.0})["sharpe"]
            row += f" {v:+.2f} |"
        emit(row)
    emit("")

    emit("## Verdict")
    emit("")
    # Check if any adaptive config consistently beats static
    static_avg_calmar = sum(r["STATIC_baseline"]["full"]["calmar"]
                              for r in results.values()) / len(results)
    adaptive_avg_calmars = {}
    for cfg in ("ADAPTIVE_50_25", "ADAPTIVE_75_50", "ADAPTIVE_50_0"):
        adaptive_avg_calmars[cfg] = sum(
            r[cfg]["full"]["calmar"] for r in results.values()
        ) / len(results)
    best_adaptive = max(adaptive_avg_calmars, key=adaptive_avg_calmars.get)
    best_adaptive_calmar = adaptive_avg_calmars[best_adaptive]
    if best_adaptive_calmar > static_avg_calmar * 1.2:
        verdict = f"**ADAPTIVE WINS** : {best_adaptive} avg Calmar {best_adaptive_calmar:.2f} vs static {static_avg_calmar:.2f}"
    elif best_adaptive_calmar > static_avg_calmar:
        verdict = f"ADAPTIVE marginally better : {best_adaptive} avg Calmar {best_adaptive_calmar:.2f} vs static {static_avg_calmar:.2f}"
    else:
        verdict = f"STATIC remains best : avg Calmar {static_avg_calmar:.2f} vs adaptive {best_adaptive_calmar:.2f}"
    emit(verdict)
    emit("")
    emit("Recommendation:")
    if "ADAPTIVE WINS" in verdict or "marginally better" in verdict:
        emit(f"- Deploy {best_adaptive} adaptive sizing on production")
        emit("- Reduces drawdown during bad regimes while preserving upside in good")
        emit("- Add to signal_generator: compute rolling Sharpe per spec, scale vol_target accordingly")
    else:
        emit("- Static vol-target remains preferred")
        emit("- Adaptive sizing reduces leverage in bad regimes but also misses recovery rallies")
        emit("- Operational complexity not justified by marginal improvement")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))
    OUT_METRICS.write_text(json.dumps({
        "results": results,
        "best_configs_per_spec": best_configs,
        "avg_calmars": {**{"STATIC": static_avg_calmar}, **adaptive_avg_calmars},
        "verdict": verdict,
    }, indent=2, default=str))
    print(f"\ndone\nfiles: {OUT_REPORT.name}, {OUT_METRICS.name}")
    print(f"verdict: {verdict}")


if __name__ == "__main__":
    main()
