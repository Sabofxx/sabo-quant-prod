"""
Sandbox — block bootstrap validation (vs iid bootstrap which underestimates CI).

iid bootstrap shuffles individual days = destroys autocorrelation = underestimates
real CI width by 20-40% typically. Block bootstrap resamples contiguous blocks
of N days = preserves autocorrelation = honest CIs.

Apply block bootstrap (block=5 days = weekly chunks) to the 3 validated ROBUST
FX specs. Compare CI95 width vs original iid bootstrap.

If block-bootstrap CI low drops below 0 for any spec → previous ROBUST verdict
was overconfident. Demote to PROBABLE.
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pandas as pd

import strategy_propfirm_cashmax as cash


HERE = Path(__file__).parent
OUT_REPORT = HERE / "validate_block_bootstrap_report.md"
OUT_METRICS = HERE / "validate_block_bootstrap_metrics.json"

ANN_DAYS = 252
SEED = 20260522
N_BOOT = 2000
BLOCK_SIZES = [1, 5, 10, 20]  # 1=iid baseline ; 5/10/20=weekly/biweekly/monthly blocks
OOS_START = pd.Timestamp("2024-01-01", tz="UTC")

SPECS = ["FX_MR_STACK", "NO_EUR_STACK", "COMDOLL_STACK", "EURUSD_MR5"]


def block_bootstrap_sharpe(pl: pd.Series, block_size: int, n_iter: int, seed: int
                            ) -> tuple[float, float, float]:
    """Block bootstrap: resample contiguous chunks of `block_size` days.
    Preserves local autocorrelation in returns."""
    rng = random.Random(seed)
    arr = pl.dropna().to_list()
    L = len(arr)
    if L < block_size * 2:
        return 0.0, 0.0, 0.0
    n_blocks = max(1, L // block_size)
    shs = []
    for _ in range(n_iter):
        # Pick n_blocks random starting indices
        sample = []
        for _ in range(n_blocks):
            start = rng.randrange(L - block_size + 1)
            sample.extend(arr[start:start + block_size])
        # Trim to original length
        sample = sample[:L]
        m = sum(sample) / len(sample)
        var = sum((x - m) ** 2 for x in sample) / (len(sample) - 1)
        std = math.sqrt(var)
        shs.append((m / std) * math.sqrt(ANN_DAYS) if std > 0 else 0.0)
    shs.sort()
    return shs[int(n_iter * 0.025)], shs[n_iter // 2], shs[int(n_iter * 0.975) - 1]


def main() -> None:
    print("Loading FX strategy universe (M5 + per-pair best LB)...")
    specs = cash.build_strategy_universe()
    print(f"  Specs: {list(specs)}\n")

    results: dict = {}
    print("Running block bootstrap on OOS daily P&L for each spec...")
    print(f"{'spec':<14} | {'block':>5} | {'CI low':>8} | {'CI median':>10} | {'CI high':>8} | width")
    for spec_name in SPECS:
        if spec_name not in specs:
            continue
        pl = specs[spec_name]["daily"]
        pl_oos = pl[pl.index >= OOS_START]
        spec_result = {}
        for block in BLOCK_SIZES:
            ci_low, ci_med, ci_high = block_bootstrap_sharpe(
                pl_oos, block, N_BOOT, SEED + block)
            width = ci_high - ci_low
            print(f"{spec_name:<14} | {block:>5} | {ci_low:>+8.2f} | {ci_med:>+10.2f} | "
                  f"{ci_high:>+8.2f} | {width:.2f}")
            spec_result[block] = {"ci_low": ci_low, "ci_median": ci_med,
                                    "ci_high": ci_high, "width": width}
        results[spec_name] = spec_result
        print()

    # Compare iid vs block-5 CI widths
    print("\n## Width inflation iid → block-5 (autocorrelation impact)")
    print(f"{'spec':<14} | iid width | block-5 width | inflation% | block-5 CI low | change verdict")
    inflation_summary = {}
    for spec_name, spec_result in results.items():
        iid_width = spec_result[1]["width"]
        block5_width = spec_result[5]["width"]
        inflation = (block5_width / iid_width - 1) * 100 if iid_width > 0 else 0
        block5_low = spec_result[5]["ci_low"]
        if block5_low > 0:
            verdict_change = "STILL ROBUST"
        elif block5_low > -0.2:
            verdict_change = "DEMOTED to PROBABLE"
        else:
            verdict_change = "DEMOTED to FRAGILE"
        inflation_summary[spec_name] = {
            "iid_width": iid_width, "block5_width": block5_width,
            "inflation_pct": inflation, "block5_ci_low": block5_low,
            "verdict_change": verdict_change,
        }
        print(f"{spec_name:<14} | {iid_width:>9.2f} | {block5_width:>13.2f} | "
              f"{inflation:>+10.0f} | {block5_low:>+14.2f} | {verdict_change}")

    # Report
    lines: list[str] = []
    def emit(s: str = "") -> None:
        lines.append(s)
    emit("# Block Bootstrap Validation (autocorrelation honest CIs)")
    emit("")
    emit("iid bootstrap assumes returns are independent. Real daily returns cluster")
    emit("(vol regimes, momentum days). iid CIs are systematically too narrow.")
    emit("")
    emit("Block bootstrap resamples contiguous chunks → preserves local autocorrelation →")
    emit("realistic CI widths. Block size 5 = weekly chunks ; 10 = biweekly ; 20 = monthly.")
    emit("")
    emit(f"Applied to {len(SPECS)} validated FX specs. N bootstrap iter: {N_BOOT}.")
    emit("")

    emit("## CI95 Sharpe by block size")
    emit("| spec | block | CI low | CI median | CI high | width |")
    emit("|---|---:|---:|---:|---:|---:|")
    for spec_name, spec_result in results.items():
        for block in BLOCK_SIZES:
            r = spec_result[block]
            emit(f"| {spec_name} | {block} | {r['ci_low']:+.2f} | "
                 f"{r['ci_median']:+.2f} | {r['ci_high']:+.2f} | {r['width']:.2f} |")
    emit("")

    emit("## Width inflation iid → block-5")
    emit("| spec | iid width | block-5 width | inflation % | block-5 CI low | verdict change |")
    emit("|---|---:|---:|---:|---:|---|")
    for spec_name, inf in inflation_summary.items():
        emit(f"| {spec_name} | {inf['iid_width']:.2f} | {inf['block5_width']:.2f} | "
             f"{inf['inflation_pct']:+.0f}% | {inf['block5_ci_low']:+.2f} | "
             f"**{inf['verdict_change']}** |")
    emit("")

    emit("## Interpretation")
    emit("")
    demoted = [n for n, inf in inflation_summary.items()
                if inf["verdict_change"] != "STILL ROBUST"]
    if not demoted:
        emit("All specs survive block bootstrap → CI low remained > 0 with autocorrelation respected.")
        emit("Original ROBUST verdicts confirmed.")
    else:
        emit(f"**{len(demoted)} specs DEMOTED** after honest CI: {demoted}")
        emit("These specs had iid-bootstrap CI low > 0 but block-bootstrap CI low ≤ 0.")
        emit("Means : their edge is consistent with autocorrelated noise.")
        emit("")
        emit("Action: reduce position size on demoted specs or drop entirely.")
    emit("")

    emit("## Methodology")
    emit("- iid bootstrap : shuffle individual days (kills autocorr → narrow CI = overconfident)")
    emit("- Block bootstrap : sample contiguous N-day chunks (preserves autocorr → wide CI = honest)")
    emit("- Block 5 chosen as primary : captures typical 1-week vol regime persistence in FX MR")
    emit("- For comparison, block 10/20 shown : monthly regime persistence")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))
    OUT_METRICS.write_text(json.dumps({
        "ci_by_block": results,
        "inflation_iid_vs_block5": inflation_summary,
    }, indent=2, default=str))
    print(f"\ndone\nfiles: {OUT_REPORT.name}, {OUT_METRICS.name}")


if __name__ == "__main__":
    main()
