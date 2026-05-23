"""
Sandbox — validate Codex H1 picks against statistical critique.

Codex picked top H1 strategies by score function heavily weighted on OOS Sharpe
from 1748 candidates. Two known statistical issues:
  1. OOS selection bias : picking by OOS Sharpe inflates apparent edge
  2. Multiple testing : 1748 candidates × no Bonferroni = top 3 may be lucky

This script applies 3 corrections to assess true robustness:

  A. HELD-OUT FINAL TEST : split OOS 2024-2025 into:
     - VALIDATION_OOS : 2024-01-01 → 2025-09-30 (used by codex score)
     - HELD_OUT       : 2025-10-01 → 2025-12-31 (never touched)
     If picks survive held-out → real edge. If collapse → selection artifact.

  B. BOOTSTRAP CI on each pick's full OOS Sharpe (2000 resamples) → uncertainty bounds

  C. MULTIPLE-TESTING NULL DISTRIBUTION : generate 1000 random ±1 daily signals
     on each pick's instrument, compute OOS Sharpe distribution. If actual Sharpe
     not in top 5% of null → consistent with luck given 1748 searches.

Verdicts per pick:
  ROBUST       : held-out Sh > 0.4 + CI low > 0 + actual > null p95
  PROBABLE     : 2 of 3 criteria pass
  FRAGILE      : 1 of 3 pass
  LIKELY_NOISE : 0 of 3 pass
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pandas as pd

# Reuse existing H1 loaders
import strategy_h1_edge_miner as h1


HERE = Path(__file__).parent
OUT_REPORT = HERE / "validate_codex_picks_report.md"
OUT_METRICS = HERE / "validate_codex_picks_metrics.json"

PICKS = {
    "H1_NASDAQ_TREND": ("usatechidxusd", "MA_LONG", 200),
    "H1_CHFJPY_BREAK": ("chfjpy", "DON_BREAK", 100),
    "H1_DOW_TSM": ("usa30idxusd", "TSM", 5),
}

ANN_DAYS = 252
SEED = 20260522
N_BOOT = 2000
N_NULL = 1000
HELD_OUT_START = pd.Timestamp("2025-10-01", tz="UTC")
VAL_OOS_START = pd.Timestamp("2024-01-01", tz="UTC")
VAL_OOS_END = pd.Timestamp("2025-09-30 23:59:59", tz="UTC")


def daily_pl_for_pick(symbol: str, family: str, lookback: int) -> pd.Series:
    """Compute daily P&L for a pick using H1 miner's loaders + signal funcs."""
    prices = h1.load_symbol(symbol)
    if prices.empty:
        raise RuntimeError(f"empty H1 data for {symbol}")
    daily_prices = prices.resample("1D").last().dropna()
    if family == "MA_LONG":
        signal = h1.signal_ma_filter_daily(daily_prices, lookback, "LONG")
    elif family == "DON_BREAK":
        signal = h1.signal_donchian_daily(daily_prices, lookback, "BREAK")
    elif family == "TSM":
        signal = h1.signal_mr_tsm_daily(daily_prices, lookback, "TSM")
    else:
        raise ValueError(family)
    return h1.signed_next_day_return(signal, daily_prices)


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
            "max_dd": dd, "wr": float((pl != 0).mean() * 100)}


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


def random_signal_null_distribution(symbol: str, n_iter: int, seed: int) -> tuple[float, float, float]:
    """For given instrument, generate n_iter random ±1 daily signals + compute OOS Sharpe.
    Returns (p50, p90, p95) of null Sharpe distribution.
    This is the per-instrument null. With 1748 candidates tested, the relevant
    'family-wise' null would be wider — we use per-instrument as conservative proxy."""
    prices = h1.load_symbol(symbol)
    daily_prices = prices.resample("1D").last().dropna()
    # OOS-only daily prices
    oos_prices = daily_prices[daily_prices.index >= VAL_OOS_START]
    n = len(oos_prices)
    if n < 100:
        return 0.0, 0.0, 0.0
    rng = random.Random(seed)
    sharpes = []
    for _ in range(n_iter):
        sig = pd.Series([rng.choice([-1, 1]) for _ in range(n)], index=oos_prices.index)
        pl = h1.signed_next_day_return(sig, oos_prices)
        m = metrics(pl)
        sharpes.append(m["sharpe"])
    sharpes.sort()
    return sharpes[n_iter // 2], sharpes[int(n_iter * 0.90)], sharpes[int(n_iter * 0.95)]


def verdict_for_pick(held_out_sh: float, ci_low: float, actual_oos_sh: float,
                     null_p95: float) -> tuple[str, list[str]]:
    checks = []
    pass_held = held_out_sh > 0.4
    pass_ci = ci_low > 0
    pass_null = actual_oos_sh > null_p95
    checks.append(f"held_out_Sh > 0.4 : {'PASS' if pass_held else 'FAIL'} ({held_out_sh:+.2f})")
    checks.append(f"CI95 low > 0    : {'PASS' if pass_ci else 'FAIL'} ({ci_low:+.2f})")
    checks.append(f"actual > null_p95 : {'PASS' if pass_null else 'FAIL'} (actual {actual_oos_sh:+.2f} vs p95 {null_p95:+.2f})")
    passed = sum([pass_held, pass_ci, pass_null])
    if passed == 3:
        return "ROBUST", checks
    if passed == 2:
        return "PROBABLE", checks
    if passed == 1:
        return "FRAGILE", checks
    return "LIKELY_NOISE", checks


def main() -> None:
    results: dict = {}
    print("Validating Codex H1 picks against 3 statistical tests...")
    print(f"  HELD_OUT window : {HELD_OUT_START.date()} → 2025-12-31")
    print(f"  Bootstrap CI    : {N_BOOT} resamples on full OOS")
    print(f"  Null search     : {N_NULL} random signals per instrument\n")

    for pick_name, (symbol, family, lookback) in PICKS.items():
        print(f"\n=== {pick_name} ({symbol} {family} L={lookback}) ===")
        pl = daily_pl_for_pick(symbol, family, lookback)
        pl_oos_full = pl[pl.index >= VAL_OOS_START]
        pl_val_oos = pl[(pl.index >= VAL_OOS_START) & (pl.index <= VAL_OOS_END)]
        pl_held_out = pl[pl.index >= HELD_OUT_START]
        m_full = metrics(pl_oos_full)
        m_val = metrics(pl_val_oos)
        m_held = metrics(pl_held_out)
        print(f"  Full OOS     : Sh={m_full['sharpe']:+.2f} ret={m_full['ann_ret']*100:+.1f}% n={m_full['n']}")
        print(f"  Val OOS      : Sh={m_val['sharpe']:+.2f} ret={m_val['ann_ret']*100:+.1f}% n={m_val['n']}")
        print(f"  HELD-OUT     : Sh={m_held['sharpe']:+.2f} ret={m_held['ann_ret']*100:+.1f}% n={m_held['n']}")

        ci_low, ci_med, ci_high = bootstrap_sharpe(pl_oos_full, N_BOOT, SEED)
        print(f"  Bootstrap CI : [{ci_low:+.2f}, {ci_high:+.2f}] median={ci_med:+.2f}")

        null_p50, null_p90, null_p95 = random_signal_null_distribution(symbol, N_NULL, SEED + 100)
        print(f"  Null distrib : p50={null_p50:+.2f} p90={null_p90:+.2f} p95={null_p95:+.2f}")

        verdict, checks = verdict_for_pick(m_held["sharpe"], ci_low, m_full["sharpe"], null_p95)
        print(f"  VERDICT: **{verdict}**")
        for chk in checks:
            print(f"    - {chk}")

        results[pick_name] = {
            "symbol": symbol, "family": family, "lookback": lookback,
            "full_oos": m_full, "val_oos": m_val, "held_out": m_held,
            "bootstrap_ci": [ci_low, ci_med, ci_high],
            "null_p50": null_p50, "null_p90": null_p90, "null_p95": null_p95,
            "verdict": verdict, "checks": checks,
        }

    # Also test FX_MR_STACK on held-out for comparison
    print("\n\n=== BASELINE COMPARISON : FX_MR_STACK ===")
    print("(Built from M5 FX data — different module. Skipped here.)")
    print("Use existing strategy_propfirm_cashmax.py output for baseline reference.")

    # Report
    lines: list[str] = []
    def emit(s: str) -> None:
        lines.append(s)

    emit("# Codex H1 Picks — Statistical Validation")
    emit("")
    emit("Three corrections applied vs original score-function selection:")
    emit("")
    emit(f"- **HELD-OUT TEST** : {HELD_OUT_START.date()} → 2025-12-31 never touched by codex selection")
    emit(f"- **BOOTSTRAP CI95** : {N_BOOT} resamples on full OOS daily P&L")
    emit(f"- **NULL DISTRIBUTION** : {N_NULL} random ±1 signals on each instrument OOS")
    emit("")
    emit("Verdict thresholds:")
    emit("- ROBUST : held_out Sh > 0.4 AND CI low > 0 AND actual > null p95")
    emit("- PROBABLE : 2 of 3 pass")
    emit("- FRAGILE : 1 of 3 pass")
    emit("- LIKELY_NOISE : 0 of 3 pass")
    emit("")

    emit("## Results per pick")
    emit("| pick | val OOS Sh | held-out Sh | CI95 low | null p95 | verdict |")
    emit("|---|---:|---:|---:|---:|---|")
    for name, r in results.items():
        emit(f"| {name} | {r['val_oos']['sharpe']:+.2f} | "
             f"{r['held_out']['sharpe']:+.2f} | {r['bootstrap_ci'][0]:+.2f} | "
             f"{r['null_p95']:+.2f} | **{r['verdict']}** |")
    emit("")

    for name, r in results.items():
        emit(f"### {name} ({r['symbol']} {r['family']} L={r['lookback']})")
        emit(f"- Full OOS    : Sharpe {r['full_oos']['sharpe']:+.2f}, ret {r['full_oos']['ann_ret']*100:+.1f}%, n={r['full_oos']['n']}")
        emit(f"- Val OOS     : Sharpe {r['val_oos']['sharpe']:+.2f}, ret {r['val_oos']['ann_ret']*100:+.1f}%, n={r['val_oos']['n']}")
        emit(f"- Held-out Q4 : Sharpe {r['held_out']['sharpe']:+.2f}, ret {r['held_out']['ann_ret']*100:+.1f}%, n={r['held_out']['n']}")
        emit(f"- Bootstrap CI95: [{r['bootstrap_ci'][0]:+.2f}, {r['bootstrap_ci'][2]:+.2f}] median {r['bootstrap_ci'][1]:+.2f}")
        emit(f"- Null distrib  : p50 {r['null_p50']:+.2f} / p90 {r['null_p90']:+.2f} / p95 {r['null_p95']:+.2f}")
        emit(f"- VERDICT: **{r['verdict']}**")
        for chk in r["checks"]:
            emit(f"  - {chk}")
        emit("")

    emit("## Interpretation")
    emit("")
    robust_count = sum(1 for r in results.values() if r["verdict"] == "ROBUST")
    probable_count = sum(1 for r in results.values() if r["verdict"] == "PROBABLE")
    fragile_count = sum(1 for r in results.values() if r["verdict"] == "FRAGILE")
    noise_count = sum(1 for r in results.values() if r["verdict"] == "LIKELY_NOISE")
    emit(f"- ROBUST: {robust_count} / {len(results)}")
    emit(f"- PROBABLE: {probable_count} / {len(results)}")
    emit(f"- FRAGILE: {fragile_count} / {len(results)}")
    emit(f"- LIKELY_NOISE: {noise_count} / {len(results)}")
    emit("")
    emit("**Recommendation per verdict**:")
    emit("- ROBUST → keep in production allocation")
    emit("- PROBABLE → keep with reduced sizing (50%) for 30-day forward test")
    emit("- FRAGILE → research-only ; do not allocate live capital")
    emit("- LIKELY_NOISE → drop from allocation immediately")
    emit("")
    emit("**Note on null distribution interpretation**:")
    emit("Per-instrument null is conservative. With 1748 candidates tested, family-wise null")
    emit("(Bonferroni-style) would be wider. If pick beats per-instrument p95 but not by margin,")
    emit("treat as PROBABLE not ROBUST.")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))
    OUT_METRICS.write_text(json.dumps(results, indent=2, default=str))
    print("\n\ndone")
    print(f"files: {OUT_REPORT.name}, {OUT_METRICS.name}")
    for name, r in results.items():
        print(f"  {name}: {r['verdict']}")


if __name__ == "__main__":
    main()
