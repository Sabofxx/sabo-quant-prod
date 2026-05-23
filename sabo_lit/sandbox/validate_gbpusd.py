"""
Sandbox — GBPUSD edge validation on Asia London Sweep v3_h3_strict.

Loads sandbox/asia_london_trades_v3_v3_h3_strict.json, filters pair=GBPUSD (n=36),
runs 6 validation probes (V1-V6) + final synthesis. Pure analysis ; no backtest mod.

Output:
  - stdout: per-V section + final synthesis + actionable_findings
  - sandbox/validate_gbpusd_report.md: stdout copy formatted as markdown
  - sandbox/validate_gbpusd_equity.html: GBPUSD cum R curve with DDs > 3R marked red

Ambiguity decisions (commented inline):
  - V1 halves: date-based split, exact per spec. 2019-01-01 → 2022-06-30 vs
    2022-07-01 → 2025-12-31. Trades placed by entry_time.
  - V4 expanding walk-forward: spec is fixed (v3_h3_strict), no actual model retrain.
    "Train" = historical data range pseudo-IS, "test" = next year. Train_pf vs test_pf
    measures whether per-period performance is consistent (not predictive).
  - V3 bootstrap PF clamped to 999 when sample has zero losses (sentinel).
  - V5 DD duration: calendar days between equity peak (DD start) and recovery
    (or last trade if never recovered).
  - V5 count_DD_3R: absolute drawdown magnitude > 3R counts.
  - V6 skewness/kurtosis: Fisher's definitions, biased estimators (n=36 too small
    for unbiased meaningful corrections).
  - V6 modality detection: heuristic — find local maxima in histogram >= 20% of
    global max, then declare bimodal if trough between two top peaks < 50% of
    lower peak. Else unimodal/other.
  - Overall grade scoring: weighted vote across V1-V4 verdicts. If V3 says
    INDISTINGUISHABLE_FROM_NOISE, cap overall at FRAGILE_EDGE regardless.
"""
from __future__ import annotations

import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go


HERE = Path(__file__).parent
IN_JSON = HERE / "asia_london_trades_v3_v3_h3_strict.json"
OUT_REPORT = HERE / "validate_gbpusd_report.md"
OUT_EQUITY = HERE / "validate_gbpusd_equity.html"


# =====================================================================
# Stat helpers
# =====================================================================
def stats(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "exp": 0.0, "total_R": 0.0}
    rs = [t["r_realized"] for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    wr = 100 * len(wins) / len(rs)
    sum_losses = sum(losses)
    if sum_losses < 0:
        pf = sum(wins) / abs(sum_losses)
    else:
        pf = float("inf") if wins else 0.0
    return {"n": len(rs), "wr": wr, "pf": pf, "exp": sum(rs) / len(rs),
            "total_R": sum(rs)}


def pf_s(pf: float) -> str:
    return f"{pf:.2f}" if pf != float("inf") else "inf"


# =====================================================================
# V1 — walk-forward halves
# =====================================================================
def v1_halves(trades: list[dict], emit) -> str:
    h1_end = pd.Timestamp("2022-06-30 23:59:59", tz="UTC")
    h2_start = pd.Timestamp("2022-07-01 00:00:00", tz="UTC")
    h1 = [t for t in trades if pd.to_datetime(t["entry_time"]) <= h1_end]
    h2 = [t for t in trades if pd.to_datetime(t["entry_time"]) >= h2_start]
    s1 = stats(h1)
    s2 = stats(h2)
    emit("## V1 WALK-FORWARD HALVES")
    emit("")
    emit("| half | range | n | wr% | pf | exp_R | total_R |")
    emit("|---|---|---|---|---|---|---|")
    emit(f"| H1 | 2019-01-01 → 2022-06-30 | {s1['n']} | {s1['wr']:.1f} | "
         f"{pf_s(s1['pf'])} | {s1['exp']:+.3f} | {s1['total_R']:+.2f} |")
    emit(f"| H2 | 2022-07-01 → 2025-12-31 | {s2['n']} | {s2['wr']:.1f} | "
         f"{pf_s(s2['pf'])} | {s2['exp']:+.3f} | {s2['total_R']:+.2f} |")
    pf1 = s1["pf"] if s1["pf"] != float("inf") else 99.0
    pf2 = s2["pf"] if s2["pf"] != float("inf") else 99.0
    dpf = pf2 - pf1
    dexp = s2["exp"] - s1["exp"]
    emit(f"  Δ PF = {dpf:+.2f}, Δ exp_R = {dexp:+.3f}")
    # verdict
    if s1["pf"] == 0 or s2["pf"] == 0:
        verdict = "CONCENTRATED"
    elif pf1 < 1.0 or pf2 < 1.0:
        verdict = "CONCENTRATED"
    elif pf1 > 1.3 and pf2 > 1.3:
        ratio = pf2 / pf1
        if 0.67 <= ratio < 1.5:
            verdict = "STABLE"
        elif ratio >= 1.5:
            verdict = "STRENGTHENING"
        else:
            verdict = "DEGRADING"
    elif pf2 >= 1.5 * pf1:
        verdict = "STRENGTHENING"
    elif pf1 > pf2:
        verdict = "DEGRADING"
    else:
        verdict = "STABLE"
    emit(f"verdict: {verdict}")
    emit("")
    return verdict


# =====================================================================
# V2 — per-year breakdown
# =====================================================================
def v2_per_year(trades: list[dict], emit) -> tuple[str, dict]:
    g: dict = defaultdict(list)
    for t in trades:
        y = pd.to_datetime(t["date"]).year
        g[y].append(t)
    emit("## V2 PER-YEAR")
    emit("")
    emit("| year | n | wr% | pf | exp_R | total_R | profitable |")
    emit("|---|---|---|---|---|---|---|")
    profitable = 0
    pos_totals: list[float] = []
    year_stats: dict = {}
    for y in range(2019, 2026):
        trs = g.get(y, [])
        s = stats(trs)
        year_stats[y] = s
        prof = "Y" if s["total_R"] > 0 else "N"
        if s["total_R"] > 0:
            profitable += 1
            pos_totals.append(s["total_R"])
        emit(f"| {y} | {s['n']} | {s['wr']:.1f} | {pf_s(s['pf'])} | "
             f"{s['exp']:+.3f} | {s['total_R']:+.2f} | {prof} |")
    sum_pos = sum(pos_totals)
    max_pos = max(pos_totals) if pos_totals else 0.0
    year_dom = (max_pos / sum_pos) if sum_pos > 0 else 0.0
    emit(f"year_dominance: {year_dom:.2f}")
    emit(f"profitable_count: {profitable}/7")
    if profitable < 4:
        verdict = "INCONSISTENT"
    elif year_dom > 0.6:
        verdict = "OUTLIER_DRIVEN"
    elif profitable >= 5 and year_dom <= 0.6:
        verdict = "ROBUST"
    else:
        verdict = "INCONSISTENT"
    emit(f"verdict: {verdict}")
    emit("")
    return verdict, year_stats


# =====================================================================
# V3 — bootstrap CI
# =====================================================================
def v3_bootstrap(trades: list[dict], emit, n_resample: int = 10000, seed: int = 42) -> tuple[str, dict]:
    rng = random.Random(seed)
    rs = [t["r_realized"] for t in trades]
    n = len(rs)
    pfs: list[float] = []
    exps: list[float] = []
    wrs: list[float] = []
    tots: list[float] = []
    for _ in range(n_resample):
        sample = [rng.choice(rs) for _ in range(n)]
        wins = [r for r in sample if r > 0]
        losses = [r for r in sample if r <= 0]
        sl = sum(losses)
        if sl < 0:
            pf = sum(wins) / abs(sl)
        else:
            pf = 999.0 if wins else 0.0
        pfs.append(pf)
        exps.append(sum(sample) / n)
        wrs.append(100 * len(wins) / n)
        tots.append(sum(sample))
    pfs.sort()
    exps.sort()
    wrs.sort()
    tots.sort()

    def ci(arr):
        lo = int(n_resample * 0.025)
        hi = int(n_resample * 0.975) - 1
        return arr[lo], arr[hi]

    pf_lo, pf_hi = ci(pfs)
    ex_lo, ex_hi = ci(exps)
    wr_lo, wr_hi = ci(wrs)
    tot_lo, tot_hi = ci(tots)
    emit("## V3 BOOTSTRAP CI (10000 resamples)")
    emit("")
    pf_note = "  (upper sentinel 999 — some resamples zero losses)" if pf_hi >= 999 else ""
    emit(f"PF      CI95: [{pf_lo:.2f}, {pf_hi:.2f}]{pf_note}")
    emit(f"exp_R   CI95: [{ex_lo:+.3f}, {ex_hi:+.3f}]")
    emit(f"wr%     CI95: [{wr_lo:.1f}, {wr_hi:.1f}]")
    emit(f"total_R CI95: [{tot_lo:+.2f}, {tot_hi:+.2f}]")
    if ex_lo > 0.05:
        verdict = "EDGE_CONFIRMED"
    elif ex_lo > 0:
        verdict = "WEAK_SIGNAL"
    else:
        verdict = "INDISTINGUISHABLE_FROM_NOISE"
    emit(f"verdict: {verdict}")
    emit("")
    return verdict, {"pf_ci": (pf_lo, pf_hi), "exp_ci": (ex_lo, ex_hi),
                     "wr_ci": (wr_lo, wr_hi), "total_ci": (tot_lo, tot_hi)}


# =====================================================================
# V4 — expanding walk-forward
# =====================================================================
def v4_expanding(trades: list[dict], emit) -> str:
    splits = [
        (2019, 2020, 2021),
        (2019, 2021, 2022),
        (2019, 2022, 2023),
        (2019, 2023, 2024),
        (2019, 2024, 2025),
    ]
    emit("## V4 EXPANDING WALK-FORWARD")
    emit("")
    emit("| train range | train_n | train_pf | test year | test_n | test_pf | ratio test/train |")
    emit("|---|---|---|---|---|---|---|")
    ratios: list[float] = []
    failed_test = False
    test_pfs: list[float] = []
    for tstart, tend, test_y in splits:
        train = [t for t in trades if tstart <= pd.to_datetime(t["date"]).year <= tend]
        test = [t for t in trades if pd.to_datetime(t["date"]).year == test_y]
        ts = stats(train)
        es = stats(test)
        tr_pf = ts["pf"] if ts["pf"] != float("inf") else 99.0
        te_pf = es["pf"] if es["pf"] != float("inf") else 99.0
        if ts["pf"] in (0.0,) or es["n"] == 0:
            ratio_s = "n/a"
            ratio_val = None
        else:
            ratio_val = te_pf / tr_pf if tr_pf > 0 else None
            if ratio_val is not None:
                ratios.append(ratio_val)
                test_pfs.append(te_pf)
                if te_pf < 0.8 and es["n"] >= 3:
                    failed_test = True
            ratio_s = f"{ratio_val:.2f}" if ratio_val is not None else "n/a"
        emit(f"| {tstart}-{tend} | {ts['n']} | {pf_s(ts['pf'])} | "
             f"{test_y} | {es['n']} | {pf_s(es['pf'])} | {ratio_s} |")
    median_ratio = statistics.median(ratios) if ratios else 0.0
    emit(f"median(ratio test/train): {median_ratio:.2f}")
    if median_ratio > 0.6 and not failed_test:
        verdict = "GENERALIZES"
    elif len(ratios) >= 3 and all(ratios[i + 1] < ratios[i] for i in range(len(ratios) - 1)):
        verdict = "DEGRADES"
    elif len(ratios) >= 2 and statistics.stdev(ratios) > 1.0:
        verdict = "ERRATIC"
    elif median_ratio <= 0.6:
        verdict = "DEGRADES"
    else:
        verdict = "ERRATIC"
    emit(f"verdict: {verdict}")
    emit("")
    return verdict


# =====================================================================
# V5 — drawdown profile
# =====================================================================
def v5_drawdown(trades: list[dict], emit) -> dict:
    sorted_t = sorted(trades, key=lambda t: t["entry_time"])
    rs = [t["r_realized"] for t in sorted_t]
    times = [pd.to_datetime(t["entry_time"]) for t in sorted_t]
    cum: list[float] = []
    c = 0.0
    for r in rs:
        c += r
        cum.append(c)
    # Walk: maintain running peak, detect DD periods
    running_peak = -float("inf")
    running_peak_idx = -1
    in_dd = False
    dd_lowest = 0.0
    dd_lowest_idx = 0
    dd_peak_idx = 0
    dd_records: list[dict] = []
    for i, e in enumerate(cum):
        if e >= running_peak:
            if in_dd:
                duration_days = (times[i] - times[dd_peak_idx]).days
                dd_records.append({
                    "peak_idx": dd_peak_idx,
                    "low_idx": dd_lowest_idx,
                    "low_value": dd_lowest,
                    "recovered_idx": i,
                    "duration_days": duration_days,
                })
                in_dd = False
            running_peak = e
            running_peak_idx = i
        else:
            if not in_dd:
                in_dd = True
                dd_peak_idx = running_peak_idx
                dd_lowest = e - running_peak
                dd_lowest_idx = i
            else:
                drawdown = e - running_peak
                if drawdown < dd_lowest:
                    dd_lowest = drawdown
                    dd_lowest_idx = i
    if in_dd:
        duration_days = (times[-1] - times[dd_peak_idx]).days
        dd_records.append({
            "peak_idx": dd_peak_idx,
            "low_idx": dd_lowest_idx,
            "low_value": dd_lowest,
            "recovered_idx": None,
            "duration_days": duration_days,
        })

    if dd_records:
        max_dd_rec = min(dd_records, key=lambda r: r["low_value"])
        max_dd = max_dd_rec["low_value"]
        longest_dd = max(r["duration_days"] for r in dd_records)
    else:
        max_dd = 0.0
        longest_dd = 0
    count_3R = sum(1 for r in dd_records if abs(r["low_value"]) > 3.0)
    total_R = sum(rs)
    recovery_factor = total_R / abs(max_dd) if max_dd != 0 else float("inf")
    if len(rs) > 1:
        std_r = statistics.stdev(rs)
        sharpe = (statistics.mean(rs) / std_r) if std_r > 0 else 0.0
    else:
        sharpe = 0.0
    emit("## V5 DRAWDOWN PROFILE")
    emit("")
    emit(f"max_DD: {max_dd:.2f}R")
    emit(f"DD duration max: {longest_dd} days")
    emit(f"DDs > 3R: {count_3R}")
    emit(f"recovery_factor (total_R / |max_DD|): {recovery_factor:.2f}")
    emit(f"R-sharpe (mean/std per-trade): {sharpe:.2f}")
    emit("")
    return {
        "cum": cum, "times": times,
        "max_dd": max_dd, "longest_dd_days": longest_dd, "count_dd_3R": count_3R,
        "recovery_factor": recovery_factor, "sharpe": sharpe, "dd_records": dd_records,
    }


# =====================================================================
# V6 — distribution sanity
# =====================================================================
def v6_distribution(trades: list[dict], emit) -> dict:
    rs = [t["r_realized"] for t in trades]
    n = len(rs)
    mean = statistics.mean(rs)
    var = sum((r - mean) ** 2 for r in rs) / n
    std = math.sqrt(var)
    if std > 0:
        skew = sum((r - mean) ** 3 for r in rs) / n / (std ** 3)
        kurt = sum((r - mean) ** 4 for r in rs) / n / (std ** 4) - 3
    else:
        skew, kurt = 0.0, 0.0
    bins: dict = defaultdict(int)
    for r in rs:
        k = int(r // 0.5) * 0.5
        bins[round(k, 2)] += 1
    sorted_keys = sorted(bins.keys())
    bvals = [bins[k] for k in sorted_keys]
    mx = max(bvals) if bvals else 0
    emit("## V6 DISTRIBUTION")
    emit("")
    emit("```")
    for k in sorted_keys:
        bar = "#" * int(bins[k] / mx * 40) if mx else ""
        emit(f"  [{k:+5.1f}, {k + 0.5:+5.1f})  {bins[k]:3d}  {bar}")
    emit("```")
    emit(f"skewness: {skew:+.2f}")
    emit(f"kurtosis: {kurt:+.2f}")
    # modality heuristic
    peaks_idx: list[int] = []
    for i in range(1, len(bvals) - 1):
        if bvals[i] >= bvals[i - 1] and bvals[i] >= bvals[i + 1] and bvals[i] >= 0.2 * mx:
            peaks_idx.append(i)
    # edge peaks
    if bvals and bvals[0] >= 0.2 * mx and (len(bvals) == 1 or bvals[0] > bvals[1]):
        peaks_idx.insert(0, 0)
    if len(bvals) >= 2 and bvals[-1] >= 0.2 * mx and bvals[-1] > bvals[-2]:
        peaks_idx.append(len(bvals) - 1)
    if len(peaks_idx) >= 2:
        i1, i2 = peaks_idx[0], peaks_idx[1]
        trough = min(bvals[i1:i2 + 1])
        lower_peak = min(bvals[i1], bvals[i2])
        if trough < 0.5 * lower_peak:
            modality = "bimodal"
        else:
            modality = "unimodal"
    elif len(peaks_idx) == 1:
        modality = "unimodal"
    else:
        modality = "other"
    emit(f"modality: {modality}")
    emit("")
    return {"skew": skew, "kurt": kurt, "modality": modality}


# =====================================================================
# Synthesis
# =====================================================================
def synthesize(v1: str, v2: str, v3: str, v4: str, emit) -> tuple[str, str]:
    emit("## FINAL SYNTHESIS")
    emit("")
    emit(f"- V1 verdict: {v1}")
    emit(f"- V2 verdict: {v2}")
    emit(f"- V3 verdict: {v3}")
    emit(f"- V4 verdict: {v4}")
    emit("")
    score = 0
    if v1 == "STABLE":
        score += 2
    elif v1 == "STRENGTHENING":
        score += 1
    elif v1 in ("DEGRADING", "CONCENTRATED"):
        score -= 2
    if v2 == "ROBUST":
        score += 2
    elif v2 == "OUTLIER_DRIVEN":
        score -= 1
    elif v2 == "INCONSISTENT":
        score -= 2
    if v3 == "EDGE_CONFIRMED":
        score += 2
    elif v3 == "INDISTINGUISHABLE_FROM_NOISE":
        score -= 2
    if v4 == "GENERALIZES":
        score += 2
    elif v4 == "DEGRADES":
        score -= 1
    elif v4 == "ERRATIC":
        score -= 1
    if score >= 6:
        overall = "ROBUST_EDGE"
        confidence = "HIGH"
    elif score >= 2:
        overall = "FRAGILE_EDGE"
        confidence = "MEDIUM"
    else:
        overall = "OVERFIT_DISGUISED"
        confidence = "MEDIUM" if score >= -2 else "HIGH"
    # Cap: if V3 says noise, can't be ROBUST regardless
    if v3 == "INDISTINGUISHABLE_FROM_NOISE" and overall == "ROBUST_EDGE":
        overall = "FRAGILE_EDGE"
        confidence = "LOW"
    emit(f"overall_grade: {overall}")
    emit(f"confidence: {confidence}")
    emit(f"score: {score}")
    emit("")
    if overall == "ROBUST_EDGE":
        emit("interpretation: ROBUST_EDGE → green-light étape 2 (analyse mécanique pourquoi GBPUSD)")
    elif overall == "FRAGILE_EDGE":
        emit("interpretation: FRAGILE_EDGE → décision needed — proceed avec sizing réduit, ou abandon")
    else:
        emit("interpretation: OVERFIT_DISGUISED → abandon GBPUSD aussi, repenser")
    emit("")
    return overall, confidence


# =====================================================================
# Equity HTML with DD>3R markers
# =====================================================================
def write_equity_html(trades: list[dict], dd_info: dict) -> None:
    sorted_t = sorted(trades, key=lambda t: t["entry_time"])
    cum = dd_info["cum"]
    times = dd_info["times"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=times, y=cum, mode="lines+markers", marker=dict(size=4, color="#1976d2"),
        line=dict(color="#1976d2"), name=f"GBPUSD cum R (n={len(sorted_t)})",
    ))
    # Mark DDs > 3R
    for rec in dd_info["dd_records"]:
        if abs(rec["low_value"]) > 3:
            fig.add_trace(go.Scatter(
                x=[times[rec["peak_idx"]], times[rec["low_idx"]]],
                y=[cum[rec["peak_idx"]], cum[rec["low_idx"]]],
                mode="lines+markers",
                line=dict(color="red", width=2, dash="dash"),
                marker=dict(color="red", size=12, symbol="x"),
                name=f"DD {rec['low_value']:.1f}R ({rec['duration_days']}d)",
            ))
    fig.update_layout(
        title="GBPUSD equity (v3_h3_strict) — cum R with DDs > 3R highlighted",
        xaxis_title="entry time (UTC)", yaxis_title="cumulative R",
        template="plotly_white", height=600, hovermode="x",
    )
    fig.write_html(str(OUT_EQUITY), include_plotlyjs="cdn")


# =====================================================================
# Main
# =====================================================================
def main() -> None:
    print(f"Loading trades from {IN_JSON}...")
    trades = json.loads(IN_JSON.read_text())
    gbp = [t for t in trades if t["pair"] == "GBPUSD"]
    print(f"  total trades={len(trades)}; GBPUSD n={len(gbp)}")
    if not gbp:
        print("  ERROR: no GBPUSD trades found")
        return

    lines: list[str] = []
    def emit(s: str) -> None:
        print(s)
        lines.append(s)

    emit("# GBPUSD Validation — Asia London Sweep v3_h3_strict")
    emit("")
    emit(f"Source: `{IN_JSON.name}`. n={len(gbp)} GBPUSD trades. "
         f"Spec figée v3_h3_strict (rr=1.2, fvg=10, bos=16, fvg_ratio_cap=0.10).")
    emit("")
    overall_s = stats(gbp)
    emit(f"Headline: n={overall_s['n']}, wr={overall_s['wr']:.1f}%, "
         f"pf={pf_s(overall_s['pf'])}, exp_R={overall_s['exp']:+.3f}, "
         f"total_R={overall_s['total_R']:+.2f}.")
    emit("")

    v1 = v1_halves(gbp, emit)
    v2, year_stats = v2_per_year(gbp, emit)
    v3, ci_info = v3_bootstrap(gbp, emit)
    v4 = v4_expanding(gbp, emit)
    dd_info = v5_drawdown(gbp, emit)
    v6_info = v6_distribution(gbp, emit)

    overall, confidence = synthesize(v1, v2, v3, v4, emit)

    # Actionable findings (max 3)
    emit("actionable_findings:")
    findings: list[str] = []
    if v2 == "OUTLIER_DRIVEN":
        best_y = max(year_stats, key=lambda y: year_stats[y]["total_R"])
        share = year_stats[best_y]["total_R"] / overall_s["total_R"] * 100 if overall_s["total_R"] > 0 else 0
        findings.append(f"Year {best_y} carries {share:.0f}% of total_R (outlier) — re-test without it")
    if v3 == "EDGE_CONFIRMED":
        findings.append(f"Bootstrap CI95 exp_R lower={ci_info['exp_ci'][0]:+.3f}R > 0.05 — edge survives resampling")
    elif v3 == "WEAK_SIGNAL":
        findings.append(f"Bootstrap CI95 exp_R lower={ci_info['exp_ci'][0]:+.3f}R barely > 0 — fragile, small-N")
    elif v3 == "INDISTINGUISHABLE_FROM_NOISE":
        findings.append(f"Bootstrap CI95 exp_R lower={ci_info['exp_ci'][0]:+.3f}R ≤ 0 — sample edge could be noise")
    if v1 == "STRENGTHENING":
        findings.append("Edge concentrated H2 (2022+) — regime-specific overfit risk")
    elif v1 == "STABLE":
        findings.append("Edge stable across both halves — temporal robustness check passed")
    elif v1 == "DEGRADING":
        findings.append("Edge degrading H1→H2 — strategy losing efficacy over time")
    if dd_info["count_dd_3R"] >= 3:
        findings.append(f"{dd_info['count_dd_3R']} DDs > 3R — significant streak risk in sizing")
    if v4 == "GENERALIZES":
        findings.append("Expanding walk-forward median test/train > 0.6 — out-of-fold consistency")
    elif v4 == "ERRATIC":
        findings.append("Expanding walk-forward erratic — per-year edge unstable")
    if v6_info["modality"] == "bimodal":
        findings.append("R distribution bimodal (-1R losses vs +TP wins clusters) — TP/SL spec working as designed")
    elif v6_info["modality"] == "other":
        findings.append("R distribution non-canonical (neither bi- nor uni-modal) — investigate")
    findings = findings[:3]
    if findings:
        for f in findings:
            emit(f"  - {f}")
    else:
        emit("  - (no actionable findings — verdicts inconclusive)")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))
    write_equity_html(gbp, dd_info)

    # Deliverable
    if overall == "ROBUST_EDGE":
        next_step = "Étape 2 : mechanical analysis of why GBPUSD wins (sweep cluster timing, BoE liquidity, GBPJPY cross-correlation)"
    elif overall == "FRAGILE_EDGE":
        next_step = "Live paper-trade with 0.5x sizing for 30 trades ; halt if PF < 1.2 mid-run, accept abandon if no improvement"
    else:
        next_step = "Abandon GBPUSD-only thesis ; setup is overfit even after OOS — rework feature set or accept no edge"

    print("\n\ndone")
    print("files: sandbox/validate_gbpusd.py, sandbox/validate_gbpusd_report.md, "
          "sandbox/validate_gbpusd_equity.html")
    print(f"verdicts: V1={v1}, V2={v2}, V3={v3}, V4={v4}")
    print(f"overall_grade: {overall}")
    print(f"next_step: {next_step}")


if __name__ == "__main__":
    main()
