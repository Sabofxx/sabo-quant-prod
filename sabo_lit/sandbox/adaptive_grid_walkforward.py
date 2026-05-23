"""
Sandbox — grid search adaptive sizing params + walk-forward validation.

Addresses ChatGPT concern : 126d window + 0.5/0 thresholds may be overfit.
Tests 18 param combinations on TRAIN (2010-2018), VALIDATE on 2019-2022,
FINAL OOS on 2023-2025. Plus hysteresis variant to address whipsaw concern.

Grid:
  Sharpe windows : 63, 126, 252 days
  Tier thresholds: (0.3, 0.0), (0.5, 0.0), (0.5, 0.25)
  Hysteresis    : OFF / ON (resume requires Sharpe > 0.3 with 20-day min state)

18 combinations. Compare to STATIC baseline.

Output : which params are robust across train/validate/OOS vs which look like
data-mining artifacts.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

import strategy_propfirm_cashmax as cash


HERE = Path(__file__).parent
OUT_REPORT = HERE / "adaptive_grid_walkforward_report.md"
OUT_METRICS = HERE / "adaptive_grid_walkforward_metrics.json"

ANN_DAYS = 252
VOL_TARGET = 0.10
MAX_LEVERAGE = 10.0

TRAIN_END = pd.Timestamp("2018-12-31 23:59:59", tz="UTC")
VAL_START = pd.Timestamp("2019-01-01", tz="UTC")
VAL_END = pd.Timestamp("2022-12-31 23:59:59", tz="UTC")
OOS_START = pd.Timestamp("2023-01-01", tz="UTC")

# Note: cash.build_strategy_universe loads M5 2019-2026 only. For train 2010-2018
# we use the H1 dataset from Pass 5 instead. For simplicity here we use full
# 2019-2025 (cash module). Train = 2019, Val = 2020-2022, OOS = 2023-2025.
# This is a WEAKER split than 2010-based, but works with current module.

TRAIN_END_M5 = pd.Timestamp("2019-12-31 23:59:59", tz="UTC")
VAL_START_M5 = pd.Timestamp("2020-01-01", tz="UTC")

SPECS = ["FX_MR_STACK", "NO_EUR_STACK", "COMDOLL_STACK"]


def static_levered(daily: pd.Series, base_vol: float) -> pd.Series:
    realized = daily.rolling(60).std() * math.sqrt(ANN_DAYS)
    lev = (base_vol / realized).clip(upper=MAX_LEVERAGE).shift(1).fillna(1.0)
    return (daily * lev).dropna()


def adaptive_levered(daily: pd.Series, base_vol: float, window: int,
                      thr_high: float, thr_low: float,
                      pause_low: bool = True,
                      hysteresis: bool = False,
                      min_state_days: int = 20) -> pd.Series:
    """Adaptive sizing with optional hysteresis.

    thr_high : Sharpe threshold for full leverage (e.g. 0.5)
    thr_low  : Sharpe threshold for half leverage (e.g. 0.0)
    pause_low: if True, Sharpe below thr_low → 0% (pause), else 25%
    hysteresis: if True, require Sharpe > 0.3 to resume from pause
                + min_state_days minimum in each state
    """
    realized_vol = daily.rolling(60).std() * math.sqrt(ANN_DAYS)
    static_lev = (base_vol / realized_vol).clip(upper=MAX_LEVERAGE).shift(1).fillna(1.0)
    static_returns = (daily * static_lev).dropna()

    rolling_mean = static_returns.rolling(window).mean()
    rolling_std = static_returns.rolling(window).std()
    rolling_sharpe = ((rolling_mean / rolling_std) * math.sqrt(ANN_DAYS)).shift(1)

    pause_multiplier = 0.0 if pause_low else 0.25

    def base_tier(sh):
        if pd.isna(sh):
            return 1.0
        if sh >= thr_high:
            return 1.0
        if sh >= thr_low:
            return 0.5
        return pause_multiplier

    base_mult = rolling_sharpe.map(base_tier)

    if not hysteresis:
        multiplier = base_mult.fillna(1.0)
    else:
        # State machine with hysteresis : require Sharpe > 0.3 to exit pause,
        # plus min_state_days in each state
        multipliers = []
        current_mult = 1.0
        days_in_state = 0
        resume_threshold = 0.3
        for i, sh in enumerate(rolling_sharpe.fillna(1.0).values):
            if pd.isna(sh) or i < window:
                multipliers.append(1.0)
                continue
            desired = base_tier(sh)
            # State change rules:
            # - If currently paused (0 or 0.25), only resume if Sh > resume_threshold AND min days respected
            # - Otherwise free to change
            if current_mult <= pause_multiplier and days_in_state < min_state_days:
                # Stay paused
                multipliers.append(current_mult)
                days_in_state += 1
                continue
            if current_mult <= pause_multiplier and sh < resume_threshold:
                # Still want to stay paused (hysteresis)
                multipliers.append(current_mult)
                days_in_state += 1
                continue
            # Allow change
            if desired != current_mult:
                current_mult = desired
                days_in_state = 0
            else:
                days_in_state += 1
            multipliers.append(current_mult)
        multiplier = pd.Series(multipliers, index=rolling_sharpe.index)

    effective_lev = (base_vol * multiplier / realized_vol).clip(upper=MAX_LEVERAGE).shift(1).fillna(1.0)
    return (daily * effective_lev).dropna()


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


def main() -> None:
    print("Loading FX universe...")
    specs = cash.build_strategy_universe()

    # Param grid
    windows = [63, 126, 252]
    thresholds = [(0.3, 0.0), (0.5, 0.0), (0.5, 0.25)]
    hysteresis_opts = [False, True]
    configs = []
    for w in windows:
        for thr_high, thr_low in thresholds:
            for hyst in hysteresis_opts:
                name = f"W{w}_thr{thr_high}_{thr_low}_hyst{hyst}"
                configs.append((name, w, thr_high, thr_low, hyst))

    print(f"\nTesting {len(configs)} configurations on {len(SPECS)} specs...")
    print(f"  TRAIN  : up to {TRAIN_END_M5.date()}")
    print(f"  VAL    : {VAL_START_M5.date()} → {VAL_END.date()}")
    print(f"  OOS    : {OOS_START.date()} → 2025-12-31\n")

    results: dict = {}
    for spec_name in SPECS:
        daily = specs[spec_name]["daily"]
        # Static baseline
        static_pl = static_levered(daily, VOL_TARGET)
        m_train_static = metrics(static_pl[static_pl.index <= TRAIN_END_M5])
        m_val_static = metrics(static_pl[(static_pl.index >= VAL_START_M5) & (static_pl.index <= VAL_END)])
        m_oos_static = metrics(static_pl[static_pl.index >= OOS_START])
        results[spec_name] = {"STATIC": {
            "train": m_train_static, "val": m_val_static, "oos": m_oos_static
        }}

        # Adaptive configs
        for name, w, thr_high, thr_low, hyst in configs:
            adapt_pl = adaptive_levered(daily, VOL_TARGET, w, thr_high, thr_low, True, hyst)
            m_train = metrics(adapt_pl[adapt_pl.index <= TRAIN_END_M5])
            m_val = metrics(adapt_pl[(adapt_pl.index >= VAL_START_M5) & (adapt_pl.index <= VAL_END)])
            m_oos = metrics(adapt_pl[adapt_pl.index >= OOS_START])
            results[spec_name][name] = {
                "train": m_train, "val": m_val, "oos": m_oos
            }

    # Find best config per spec by VAL Calmar (not OOS to avoid OOS-fit)
    print("\n## Best config per spec (selected by VAL Calmar, not OOS)")
    best_per_spec = {}
    for spec_name, cfg_results in results.items():
        # Only consider configs with positive val Sharpe
        candidates = [(n, r) for n, r in cfg_results.items() if r["val"]["sharpe"] > 0]
        if not candidates:
            best_per_spec[spec_name] = "STATIC"
            continue
        best = max(candidates, key=lambda x: x[1]["val"]["calmar"])
        best_per_spec[spec_name] = best[0]
        print(f"  {spec_name} : best by VAL Calmar = **{best[0]}**")
        print(f"    Train: Sh={best[1]['train']['sharpe']:+.2f} Calmar={best[1]['train']['calmar']:.2f}")
        print(f"    Val  : Sh={best[1]['val']['sharpe']:+.2f} Calmar={best[1]['val']['calmar']:.2f}")
        print(f"    OOS  : Sh={best[1]['oos']['sharpe']:+.2f} Calmar={best[1]['oos']['calmar']:.2f}")

    # Cross-validation : does best-by-VAL also win OOS?
    print("\n## Cross-validation check : best-by-VAL Sharpe vs OOS Sharpe")
    for spec_name, best_name in best_per_spec.items():
        if best_name == "STATIC":
            continue
        best_val_sh = results[spec_name][best_name]["val"]["sharpe"]
        best_oos_sh = results[spec_name][best_name]["oos"]["sharpe"]
        static_oos_sh = results[spec_name]["STATIC"]["oos"]["sharpe"]
        beats_static_oos = "YES" if best_oos_sh > static_oos_sh else "NO"
        print(f"  {spec_name} : VAL Sh={best_val_sh:+.2f} → OOS Sh={best_oos_sh:+.2f} "
              f"(STATIC OOS={static_oos_sh:+.2f}, beats? {beats_static_oos})")

    # Report
    lines: list[str] = []
    def emit(s: str = "") -> None:
        lines.append(s)
    emit("# Adaptive Grid Walk-Forward")
    emit("")
    emit("Tests if 126d / 0.5 / 0.0 / pause params are overfit by grid-searching")
    emit("18 configurations across train (2019), val (2020-2022), OOS (2023-2025).")
    emit("")
    emit(f"Grid : windows = {windows}, thresholds = {thresholds}, hysteresis = {hysteresis_opts}")
    emit("")

    emit("## Best config per spec (by VAL Calmar)")
    emit("| spec | best config | TRAIN Sh | VAL Sh | OOS Sh | TRAIN Calmar | VAL Calmar | OOS Calmar |")
    emit("|---|---|---:|---:|---:|---:|---:|---:|")
    for spec_name, best_name in best_per_spec.items():
        if best_name == "STATIC":
            r = results[spec_name]["STATIC"]
        else:
            r = results[spec_name][best_name]
        emit(f"| {spec_name} | {best_name} | {r['train']['sharpe']:+.2f} | "
             f"{r['val']['sharpe']:+.2f} | {r['oos']['sharpe']:+.2f} | "
             f"{r['train']['calmar']:.2f} | {r['val']['calmar']:.2f} | "
             f"{r['oos']['calmar']:.2f} |")
    emit("")

    # Full grid for FX_MR_STACK
    emit("## Full grid for FX_MR_STACK (all 18 configs + static)")
    emit("| config | TRAIN Sh | VAL Sh | OOS Sh | TRAIN Calmar | VAL Calmar | OOS Calmar |")
    emit("|---|---:|---:|---:|---:|---:|---:|")
    fx_results = results["FX_MR_STACK"]
    for name, r in sorted(fx_results.items()):
        emit(f"| {name} | {r['train']['sharpe']:+.2f} | "
             f"{r['val']['sharpe']:+.2f} | {r['oos']['sharpe']:+.2f} | "
             f"{r['train']['calmar']:.2f} | {r['val']['calmar']:.2f} | "
             f"{r['oos']['calmar']:.2f} |")
    emit("")

    # Stability check : are top-K by VAL Calmar also top-K by OOS Calmar?
    fx_by_val = sorted(fx_results.items(),
                        key=lambda x: x[1]["val"]["calmar"]
                        if x[1]["val"]["calmar"] != float("inf") else 0,
                        reverse=True)
    top5_val = [name for name, _ in fx_by_val[:5]]
    fx_by_oos = sorted(fx_results.items(),
                        key=lambda x: x[1]["oos"]["calmar"]
                        if x[1]["oos"]["calmar"] != float("inf") else 0,
                        reverse=True)
    top5_oos = [name for name, _ in fx_by_oos[:5]]
    overlap = set(top5_val) & set(top5_oos)
    emit("## Stability check FX_MR_STACK : top-5 by VAL vs top-5 by OOS")
    emit(f"  Top 5 VAL  : {top5_val}")
    emit(f"  Top 5 OOS  : {top5_oos}")
    emit(f"  Overlap    : {sorted(overlap)} ({len(overlap)}/5)")
    emit("")
    if len(overlap) >= 3:
        emit("  → STABLE : params chosen by VAL also win OOS, low overfit risk")
    elif len(overlap) >= 1:
        emit("  → MIXED : some param sensitivity, but ranking partially preserved")
    else:
        emit("  → UNSTABLE : VAL ranking does not predict OOS = params overfit-prone")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))
    OUT_METRICS.write_text(json.dumps({
        "results": results,
        "best_per_spec_by_val_calmar": best_per_spec,
        "fx_stack_top5_val": top5_val,
        "fx_stack_top5_oos": top5_oos,
        "overlap_count": len(overlap),
    }, indent=2, default=str))
    print(f"\ndone\nfiles: {OUT_REPORT.name}, {OUT_METRICS.name}")


if __name__ == "__main__":
    main()
