"""
Sandbox — apply same 3-test statistical validation to FX BASELINE specs.

validate_codex_picks tested only H1 picks. But FX baselines (FX_MR_STACK,
EURUSD_MR5, NO_EUR_STACK, COMDOLL_STACK) are the core of every portfolio claim.
They MUST pass the same statistical bar before deploy.

Same 3 tests:
  A. HELD-OUT FINAL : 2025-10-01 → 2025-12-31 never touched in any prior selection
  B. BOOTSTRAP CI95 on full OOS Sharpe (2000 resamples)
  C. NULL DISTRIBUTION : 1000 random ±1 daily signals on same instruments

If any baseline FAILS held-out OR < null p95 → claim of "validated FX edge" is wrong.
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pandas as pd

import strategy_propfirm_cashmax as cash


HERE = Path(__file__).parent
OUT_REPORT = HERE / "validate_fx_baseline_report.md"
OUT_METRICS = HERE / "validate_fx_baseline_metrics.json"

ANN_DAYS = 252
SEED = 20260522
N_BOOT = 2000
N_NULL = 1000
HELD_OUT_START = pd.Timestamp("2025-10-01", tz="UTC")
VAL_OOS_START = pd.Timestamp("2024-01-01", tz="UTC")
VAL_OOS_END = pd.Timestamp("2025-09-30 23:59:59", tz="UTC")

SPECS_TO_VALIDATE = ["FX_MR_STACK", "EURUSD_MR5", "NO_EUR_STACK", "COMDOLL_STACK"]


def metrics(pl: pd.Series) -> dict:
    pl = pl.dropna()
    if len(pl) < 30:
        return {"n": len(pl), "sharpe": 0.0, "ann_ret": 0.0, "max_dd": 0.0, "wr": 0.0}
    mean = float(pl.mean())
    std = float(pl.std())
    sharpe = (mean / std) * math.sqrt(ANN_DAYS) if std > 0 else 0.0
    cum = pl.cumsum()
    dd = float((cum - cum.cummax()).min())
    return {"n": len(pl), "sharpe": sharpe, "ann_ret": mean * ANN_DAYS,
            "max_dd": dd, "wr": float((pl > 0).mean() * 100)}


def bootstrap_sharpe(pl: pd.Series, n: int, seed: int) -> tuple[float, float, float]:
    rng = random.Random(seed)
    arr = pl.dropna().to_list()
    L = len(arr)
    if L < 30:
        return 0.0, 0.0, 0.0
    shs = []
    for _ in range(n):
        s = [arr[rng.randrange(L)] for _ in range(L)]
        m = sum(s) / L
        var = sum((x - m) ** 2 for x in s) / (L - 1)
        std = math.sqrt(var)
        shs.append((m / std) * math.sqrt(ANN_DAYS) if std > 0 else 0.0)
    shs.sort()
    return shs[int(n * 0.025)], shs[n // 2], shs[int(n * 0.975) - 1]


def random_null_for_spec(spec_daily: pd.Series, n_iter: int, seed: int) -> tuple[float, float, float]:
    """For given spec OOS daily P&L stream, generate n_iter random sign-flips.
    Returns (p50, p90, p95) of null Sharpe distribution."""
    rng = random.Random(seed)
    pl_oos = spec_daily[spec_daily.index >= VAL_OOS_START].dropna()
    arr = pl_oos.to_list()
    L = len(arr)
    if L < 100:
        return 0.0, 0.0, 0.0
    shs = []
    for _ in range(n_iter):
        # Random sign per day (preserves magnitude distribution but kills sign info)
        flipped = [arr[i] if rng.random() < 0.5 else -arr[i] for i in range(L)]
        m = sum(flipped) / L
        var = sum((x - m) ** 2 for x in flipped) / (L - 1)
        std = math.sqrt(var)
        shs.append((m / std) * math.sqrt(ANN_DAYS) if std > 0 else 0.0)
    shs.sort()
    return shs[n_iter // 2], shs[int(n_iter * 0.90)], shs[int(n_iter * 0.95)]


def verdict_for_baseline(held_out_sh: float, ci_low: float, actual_sh: float,
                          null_p95: float) -> tuple[str, list[str]]:
    pass_held = held_out_sh > 0.4
    pass_ci = ci_low > 0
    pass_null = actual_sh > null_p95
    checks = [
        f"held_out_Sh > 0.4 : {'PASS' if pass_held else 'FAIL'} ({held_out_sh:+.2f})",
        f"CI95 low > 0    : {'PASS' if pass_ci else 'FAIL'} ({ci_low:+.2f})",
        f"actual > null_p95 : {'PASS' if pass_null else 'FAIL'} (actual {actual_sh:+.2f} vs p95 {null_p95:+.2f})",
    ]
    passed = sum([pass_held, pass_ci, pass_null])
    if passed == 3:
        return "ROBUST", checks
    if passed == 2:
        return "PROBABLE", checks
    if passed == 1:
        return "FRAGILE", checks
    return "LIKELY_NOISE", checks


def main() -> None:
    print("Building FX strategy universe (loads M5)...")
    specs = cash.build_strategy_universe()
    print(f"  Universe specs : {list(specs)}")
    print(f"  Validating : {SPECS_TO_VALIDATE}")
    print(f"\n  Held-out window : {HELD_OUT_START.date()} → 2025-12-31")
    print(f"  Bootstrap CI    : {N_BOOT} resamples")
    print(f"  Null distrib    : {N_NULL} random sign-flips on OOS magnitude\n")

    results: dict = {}
    for name in SPECS_TO_VALIDATE:
        if name not in specs:
            print(f"  SKIP {name} (not in universe)")
            continue
        print(f"\n=== {name} ===")
        daily = specs[name]["daily"]
        pl_full_oos = daily[daily.index >= VAL_OOS_START]
        pl_val_oos = daily[(daily.index >= VAL_OOS_START) & (daily.index <= VAL_OOS_END)]
        pl_held_out = daily[daily.index >= HELD_OUT_START]
        m_full = metrics(pl_full_oos)
        m_val = metrics(pl_val_oos)
        m_held = metrics(pl_held_out)
        print(f"  Full OOS : Sh={m_full['sharpe']:+.2f} ret={m_full['ann_ret']*100:+.1f}% n={m_full['n']}")
        print(f"  Val OOS  : Sh={m_val['sharpe']:+.2f} ret={m_val['ann_ret']*100:+.1f}% n={m_val['n']}")
        print(f"  HELD-OUT : Sh={m_held['sharpe']:+.2f} ret={m_held['ann_ret']*100:+.1f}% n={m_held['n']}")
        ci_low, ci_med, ci_high = bootstrap_sharpe(pl_full_oos, N_BOOT, SEED)
        print(f"  Bootstrap CI95 : [{ci_low:+.2f}, {ci_high:+.2f}] median {ci_med:+.2f}")
        null_p50, null_p90, null_p95 = random_null_for_spec(daily, N_NULL, SEED + 100)
        print(f"  Null distrib   : p50={null_p50:+.2f} p90={null_p90:+.2f} p95={null_p95:+.2f}")
        verdict, checks = verdict_for_baseline(m_held["sharpe"], ci_low, m_full["sharpe"], null_p95)
        print(f"  VERDICT: **{verdict}**")
        for chk in checks:
            print(f"    - {chk}")
        results[name] = {
            "full_oos": m_full, "val_oos": m_val, "held_out": m_held,
            "bootstrap_ci": [ci_low, ci_med, ci_high],
            "null_p50": null_p50, "null_p90": null_p90, "null_p95": null_p95,
            "verdict": verdict, "checks": checks,
        }

    # Report
    lines: list[str] = []
    def emit(s: str = "") -> None:
        lines.append(s)
    emit("# FX Baseline Statistical Validation")
    emit("")
    emit("Same 3-test framework as `validate_codex_picks` applied to FX BASELINE specs.")
    emit("Goal: confirm baselines pass the same statistical bar before deployment.")
    emit("")
    emit("## Summary table")
    emit("| spec | val_OOS Sh | held-out Sh | CI95 low | null p95 | verdict |")
    emit("|---|---:|---:|---:|---:|---|")
    for name, r in results.items():
        emit(f"| {name} | {r['val_oos']['sharpe']:+.2f} | "
             f"{r['held_out']['sharpe']:+.2f} | {r['bootstrap_ci'][0]:+.2f} | "
             f"{r['null_p95']:+.2f} | **{r['verdict']}** |")
    emit("")
    for name, r in results.items():
        emit(f"### {name}")
        emit(f"- Full OOS    : Sh {r['full_oos']['sharpe']:+.2f}, ret {r['full_oos']['ann_ret']*100:+.1f}%, n={r['full_oos']['n']}")
        emit(f"- Held-out Q4 : Sh {r['held_out']['sharpe']:+.2f}, ret {r['held_out']['ann_ret']*100:+.1f}%, n={r['held_out']['n']}")
        emit(f"- Bootstrap CI95 : [{r['bootstrap_ci'][0]:+.2f}, {r['bootstrap_ci'][2]:+.2f}] median {r['bootstrap_ci'][1]:+.2f}")
        emit(f"- Null distrib   : p50 {r['null_p50']:+.2f} / p90 {r['null_p90']:+.2f} / p95 {r['null_p95']:+.2f}")
        emit(f"- VERDICT: **{r['verdict']}**")
        for chk in r["checks"]:
            emit(f"  - {chk}")
        emit("")

    # Final action
    robust = [n for n, r in results.items() if r["verdict"] == "ROBUST"]
    probable = [n for n, r in results.items() if r["verdict"] == "PROBABLE"]
    fragile = [n for n, r in results.items() if r["verdict"] == "FRAGILE"]
    noise = [n for n, r in results.items() if r["verdict"] == "LIKELY_NOISE"]
    emit("## Verdict counts")
    emit(f"- ROBUST: {robust}")
    emit(f"- PROBABLE: {probable}")
    emit(f"- FRAGILE: {fragile}")
    emit(f"- LIKELY_NOISE: {noise}")
    emit("")
    emit("## Action")
    emit("- ROBUST baselines → deploy as-is in multi-firm allocation")
    emit("- PROBABLE → deploy with 75% sizing in forward test")
    emit("- FRAGILE → research-only, do not deploy")
    emit("- LIKELY_NOISE → drop entirely")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))
    OUT_METRICS.write_text(json.dumps(results, indent=2, default=str))
    print("\ndone")
    print(f"files: {OUT_REPORT.name}, {OUT_METRICS.name}")
    for name, r in results.items():
        print(f"  {name}: {r['verdict']}")


if __name__ == "__main__":
    main()
