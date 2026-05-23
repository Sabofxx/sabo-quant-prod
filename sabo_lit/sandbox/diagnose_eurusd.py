"""
Sandbox — EURUSD root-cause analysis for Asia London Sweep setup.

Loads `sandbox/asia_london_trades_v2_relax_all.json`, filters pair=EURUSD,
runs 8 stratified hypotheses with contrast vs GBPUSD/USDJPY. Pure analysis;
no backtest modification.

Output:
  - stdout: per-H table + verdict + mechanism + final SYNTHESIS
  - sandbox/diagnose_eurusd_report.md: same content as markdown

Hypotheses:
  H1 Asia range too wide -> trend day, not trap
  H2 Sweep shallow -> noise, not real liquidity hunt
  H3 FVG too narrow vs range -> entry too close to sweep extreme -> SL touched
  H4 H1 context marginal (close to mid)
  H5 Direction bias (shorts vs longs)
  H6 Sweep direction x H1 context combo
  H7 DXY proxy regime (EUR trend up/down/range)
  H8 Sweep hour bias

Ambiguity decisions (commented inline):
  - sweep_depth_pips: extreme distance beyond Asia in pips, sign-corrected
  - fvg_ratio = |fvg_high - fvg_low| / asia_range_pips (both in pips)
  - h1_position computed by recomputing fractal pivots on H1 up to sweep_ts;
    uses trade entry_price (sweep_close not stored). Clamped [0,1].
  - context_extremeness = (h1_pos - 0.5)*2 for short, (0.5 - h1_pos)*2 for long.
    Range [0,1]; higher = stronger context.
  - DXY regime: EURUSD H1 trend over last 120 H1 bars (~5 days). Delta in pips
    > 50 = EUR_up_DXY_down, < -50 = EUR_down_DXY_up, else ranging.
  - Verdict: CONFIRMED if spread(exp_R) between buckets >= 0.4R AND each
    bucket n >= 3. INCONCLUSIVE if any bucket n < 3 (or <2 buckets valid).
    REJECTED otherwise.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pandas as pd


HERE = Path(__file__).parent
IN_JSON = HERE / "asia_london_trades_v2_relax_all.json"
OUT_REPORT = HERE / "diagnose_eurusd_report.md"
DATA_DIR = HERE / "data"

PAIRS = ["EURUSD", "GBPUSD", "USDJPY"]
PIP = {"EURUSD": 0.0001, "GBPUSD": 0.0001, "USDJPY": 0.01}
FILES_H1 = {
    "EURUSD": "eurusd-h1-bid-2019-01-01-2026-01-01.csv",
    "GBPUSD": "gbpusd-h1-bid-2019-01-01-2026-01-01.csv",
    "USDJPY": "usdjpy-h1-bid-2019-01-01-2026-01-01.csv",
}


# =====================================================================
# IO + helpers
# =====================================================================
def load_h1(pair: str) -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / FILES_H1[pair])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    return df


def fractal_pivots(highs: list[float], lows: list[float]):
    hi: list[int] = []
    lo: list[int] = []
    n = len(highs)
    for i in range(2, n - 2):
        if (highs[i] > highs[i - 1] and highs[i] > highs[i - 2]
                and highs[i] > highs[i + 1] and highs[i] > highs[i + 2]):
            hi.append(i)
        if (lows[i] < lows[i - 1] and lows[i] < lows[i - 2]
                and lows[i] < lows[i + 1] and lows[i] < lows[i + 2]):
            lo.append(i)
    return hi, lo


def h1_position(trade: dict, h1_df: pd.DataFrame) -> float | None:
    sweep_ts = pd.to_datetime(trade["sweep_time"])
    h1_recent = h1_df.loc[:sweep_ts].tail(50)
    if len(h1_recent) < 50:
        return None
    highs = h1_recent["high"].tolist()
    lows = h1_recent["low"].tolist()
    hi_idx, lo_idx = fractal_pivots(highs, lows)
    if not hi_idx or not lo_idx:
        return None
    swing_high = highs[hi_idx[-1]]
    swing_low = lows[lo_idx[-1]]
    if swing_high <= swing_low:
        return None
    ref = trade["entry_price"]
    pos = (ref - swing_low) / (swing_high - swing_low)
    return max(0.0, min(1.0, pos))


def dxy_regime(trade: dict, h1_df: pd.DataFrame, pip: float) -> str | None:
    sweep_ts = pd.to_datetime(trade["sweep_time"])
    window = h1_df.loc[:sweep_ts].tail(120)
    if len(window) < 100:
        return None
    last_close = float(window.iloc[-1]["close"])
    first_close = float(window.iloc[0]["close"])
    delta_pips = (last_close - first_close) / pip
    if delta_pips > 50:
        return "EUR_up_DXY_down"
    if delta_pips < -50:
        return "EUR_down_DXY_up"
    return "ranging"


def enrich(trades: list[dict], pip: float, h1_df: pd.DataFrame) -> None:
    """Add diagnostic fields in-place."""
    for t in trades:
        # sweep_depth (always positive, in pips)
        if t["direction"] == "short":
            t["_sweep_depth"] = (t["sweep_extreme"] - t["asia_high"]) / pip
        else:
            t["_sweep_depth"] = (t["asia_low"] - t["sweep_extreme"]) / pip
        # fvg size in pips
        t["_fvg_size"] = abs(t["fvg_zone"][1] - t["fvg_zone"][0]) / pip
        # fvg / asia_range ratio in %
        t["_fvg_ratio_pct"] = (t["_fvg_size"] / t["asia_range_pips"]) * 100 if t["asia_range_pips"] else 0.0
        # H1 position + extremeness
        pos = h1_position(t, h1_df)
        t["_h1_pos"] = pos
        if pos is None:
            t["_ctx_extreme"] = None
        else:
            if t["direction"] == "short":
                t["_ctx_extreme"] = max(0.0, (pos - 0.5) * 2)
            else:
                t["_ctx_extreme"] = max(0.0, (0.5 - pos) * 2)
        t["_regime"] = dxy_regime(t, h1_df, pip)


def stats(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "exp": 0.0}
    rs = [t["r_realized"] for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    wr = 100 * len(wins) / len(rs)
    sum_losses = sum(losses)
    if sum_losses < 0:
        pf = sum(wins) / abs(sum_losses)
    else:
        pf = float("inf") if wins else 0.0
    return {"n": len(rs), "wr": wr, "pf": pf, "exp": sum(rs) / len(rs)}


def _pf_str(pf: float) -> str:
    return f"{pf:.2f}" if pf != float("inf") else "inf"


def table_md(buckets: dict, bucket_order: list[str] | None = None) -> str:
    """buckets: dict[label -> list[trade]]. Returns markdown table string."""
    lines = ["| bucket | n | wr% | pf | exp_R |", "|---|---|---|---|---|"]
    keys = bucket_order if bucket_order is not None else list(buckets.keys())
    for k in keys:
        s = stats(buckets.get(k, []))
        lines.append(f"| {k} | {s['n']} | {s['wr']:.1f} | {_pf_str(s['pf'])} | {s['exp']:+.3f} |")
    return "\n".join(lines)


def verdict(buckets: dict, min_n: int = 3, threshold: float = 0.4):
    """Returns (status, spread, worst_label, best_label)."""
    valid = {k: stats(v) for k, v in buckets.items() if len(v) >= min_n}
    if len(valid) < 2:
        return ("INCONCLUSIVE", 0.0, None, None)
    sorted_b = sorted(valid.items(), key=lambda kv: kv[1]["exp"])
    spread = sorted_b[-1][1]["exp"] - sorted_b[0][1]["exp"]
    if spread >= threshold:
        return ("CONFIRMED", spread, sorted_b[0][0], sorted_b[-1][0])
    return ("REJECTED", spread, sorted_b[0][0], sorted_b[-1][0])


def tertile_buckets(trades: list[dict], field: str, labels=("low", "mid", "hi")) -> tuple[dict, tuple[float, float]]:
    """Split trades into 3 equal-count buckets by a numeric field. Returns (buckets, (b1, b2))."""
    if len(trades) < 3:
        return {labels[0]: trades, labels[1]: [], labels[2]: []}, (0.0, 0.0)
    vals = sorted(t[field] for t in trades)
    n = len(vals)
    b1 = vals[n // 3]
    b2 = vals[2 * n // 3]
    out: dict = {labels[0]: [], labels[1]: [], labels[2]: []}
    for t in trades:
        v = t[field]
        if v < b1:
            out[labels[0]].append(t)
        elif v < b2:
            out[labels[1]].append(t)
        else:
            out[labels[2]].append(t)
    return out, (b1, b2)


# =====================================================================
# Hypothesis runners — each returns (markdown_block, verdict_label, mechanism_phrase, effect_size)
# =====================================================================
def h1_asia_range_too_wide(eu, gb, jp) -> tuple[str, str, str, float]:
    out: list[str] = []
    out.append("## H1 — Asia range too wide = trend day, not trap")
    out.append("")
    out.append("EURUSD breakdown (tertiles on asia_range_pips):")
    eu_b, (b1, b2) = tertile_buckets(eu, "asia_range_pips", ("low", "mid", "hi"))
    labels_eu = [f"low(<{b1:.0f})", f"mid({b1:.0f}-{b2:.0f})", f"hi(>{b2:.0f})"]
    eu_b_disp = {labels_eu[i]: eu_b[k] for i, k in enumerate(("low", "mid", "hi"))}
    out.append(table_md(eu_b_disp, labels_eu))
    out.append("")
    out.append("GBPUSD/USDJPY contrast (tertiles per pair):")
    gb_b, (gb1, gb2) = tertile_buckets(gb, "asia_range_pips", ("low", "mid", "hi"))
    jp_b, (jp1, jp2) = tertile_buckets(jp, "asia_range_pips", ("low", "mid", "hi"))
    labels_gb = [f"low(<{gb1:.0f})", f"mid({gb1:.0f}-{gb2:.0f})", f"hi(>{gb2:.0f})"]
    labels_jp = [f"low(<{jp1:.0f})", f"mid({jp1:.0f}-{jp2:.0f})", f"hi(>{jp2:.0f})"]
    out.append("GBPUSD:")
    out.append(table_md({labels_gb[i]: gb_b[k] for i, k in enumerate(("low", "mid", "hi"))}, labels_gb))
    out.append("USDJPY:")
    out.append(table_md({labels_jp[i]: jp_b[k] for i, k in enumerate(("low", "mid", "hi"))}, labels_jp))
    out.append("")
    status, spread, worst, best = verdict(eu_b)
    if status == "CONFIRMED":
        mech = (f"EURUSD {worst}-bucket carries large negative exp_R vs {best}; "
                f"wide Asia ranges = mean-reversion fails, sweep extends into trend.")
    elif status == "INCONCLUSIVE":
        mech = "EURUSD n per tertile too small for confident split."
    else:
        mech = "Asia range tertile shows no material exp_R spread on EURUSD."
    out.append(f"verdict: {status} (spread {spread:.2f}R)")
    out.append(f"mechanism: {mech}")
    out.append("")
    return ("\n".join(out), status, mech, spread)


def h2_sweep_depth(eu, gb, jp) -> tuple[str, str, str, float]:
    out: list[str] = []
    out.append("## H2 — Sweep depth (shallow = noise, deep = real hunt)")
    out.append("")
    BUCKETS = [(2, 3, "2-3p"), (3, 5, "3-5p"), (5, 10, "5-10p"), (10, 9999, "10+p")]

    def bucketize(trades):
        d: dict = {lbl: [] for _, _, lbl in BUCKETS}
        for t in trades:
            v = t["_sweep_depth"]
            for lo, hi, lbl in BUCKETS:
                if lo <= v < hi:
                    d[lbl].append(t)
                    break
        return d

    labels = [lbl for _, _, lbl in BUCKETS]
    eu_b = bucketize(eu)
    out.append("EURUSD breakdown:")
    out.append(table_md(eu_b, labels))
    out.append("")
    out.append("GBPUSD/USDJPY contrast:")
    out.append("GBPUSD:")
    out.append(table_md(bucketize(gb), labels))
    out.append("USDJPY:")
    out.append(table_md(bucketize(jp), labels))
    out.append("")
    # sweep depth distribution descriptors
    eu_depths = sorted(t["_sweep_depth"] for t in eu)
    gb_depths = sorted(t["_sweep_depth"] for t in gb)
    jp_depths = sorted(t["_sweep_depth"] for t in jp)
    def med(xs): return xs[len(xs) // 2] if xs else 0.0
    def mean(xs): return sum(xs) / len(xs) if xs else 0.0
    out.append("sweep_depth distribution (pips):")
    out.append(f"  EURUSD  median={med(eu_depths):.1f}  mean={mean(eu_depths):.1f}  max={eu_depths[-1] if eu_depths else 0:.1f}")
    out.append(f"  GBPUSD  median={med(gb_depths):.1f}  mean={mean(gb_depths):.1f}  max={gb_depths[-1] if gb_depths else 0:.1f}")
    out.append(f"  USDJPY  median={med(jp_depths):.1f}  mean={mean(jp_depths):.1f}  max={jp_depths[-1] if jp_depths else 0:.1f}")
    out.append("")
    status, spread, worst, best = verdict(eu_b)
    if status == "CONFIRMED":
        mech = (f"EURUSD {worst} sweeps lose, {best} sweeps win — depth predicts trap vs noise.")
    elif status == "INCONCLUSIVE":
        mech = "EURUSD n per depth-bucket too small."
    else:
        mech = "Sweep depth not predictive on EURUSD."
    out.append(f"verdict: {status} (spread {spread:.2f}R)")
    out.append(f"mechanism: {mech}")
    out.append("")
    return ("\n".join(out), status, mech, spread)


def h3_fvg_ratio(eu, gb, jp) -> tuple[str, str, str, float]:
    out: list[str] = []
    out.append("## H3 — FVG depth ratio vs Asia range (entry placement quality)")
    out.append("")
    BUCKETS = [(0, 5, "<5%"), (5, 10, "5-10%"), (10, 20, "10-20%"), (20, 9999, "20+%")]

    def bucketize(trades):
        d: dict = {lbl: [] for _, _, lbl in BUCKETS}
        for t in trades:
            v = t["_fvg_ratio_pct"]
            for lo, hi, lbl in BUCKETS:
                if lo <= v < hi:
                    d[lbl].append(t)
                    break
        return d

    labels = [lbl for _, _, lbl in BUCKETS]
    eu_b = bucketize(eu)
    out.append("EURUSD breakdown:")
    out.append(table_md(eu_b, labels))
    out.append("")
    out.append("GBPUSD/USDJPY contrast:")
    out.append("GBPUSD:")
    out.append(table_md(bucketize(gb), labels))
    out.append("USDJPY:")
    out.append(table_md(bucketize(jp), labels))
    out.append("")
    status, spread, worst, best = verdict(eu_b)
    if status == "CONFIRMED":
        mech = (f"EURUSD entries with FVG ratio {worst} of Asia range underperform; "
                f"too-small FVG places entry near sweep extreme, SL too close.")
    elif status == "INCONCLUSIVE":
        mech = "EURUSD n per FVG-ratio bucket too small."
    else:
        mech = "FVG/range ratio not predictive on EURUSD."
    out.append(f"verdict: {status} (spread {spread:.2f}R)")
    out.append(f"mechanism: {mech}")
    out.append("")
    return ("\n".join(out), status, mech, spread)


def h4_h1_context_marginal(eu, gb, jp) -> tuple[str, str, str, float]:
    out: list[str] = []
    out.append("## H4 — H1 context extremeness (marginal vs strong)")
    out.append("")
    out.append("context_extremeness in [0,1]; 0 = entry near mid-band (marginal), 1 = at extreme (strong)")
    out.append("")
    BUCKETS = [(0, 0.2, "weak[0-0.2)"), (0.2, 0.5, "mid[0.2-0.5)"), (0.5, 1.01, "strong[0.5-1])")]

    def bucketize(trades):
        d: dict = {lbl: [] for _, _, lbl in BUCKETS}
        for t in trades:
            v = t.get("_ctx_extreme")
            if v is None:
                continue
            for lo, hi, lbl in BUCKETS:
                if lo <= v < hi:
                    d[lbl].append(t)
                    break
        return d

    labels = [lbl for _, _, lbl in BUCKETS]
    eu_b = bucketize(eu)
    out.append("EURUSD breakdown:")
    out.append(table_md(eu_b, labels))
    out.append("")
    out.append("GBPUSD/USDJPY contrast:")
    out.append("GBPUSD:")
    out.append(table_md(bucketize(gb), labels))
    out.append("USDJPY:")
    out.append(table_md(bucketize(jp), labels))
    out.append("")
    status, spread, worst, best = verdict(eu_b)
    if status == "CONFIRMED":
        mech = (f"EURUSD '{worst}' context lose vs '{best}'; "
                f"backtest H1 filter (just upper/lower half) is too permissive.")
    elif status == "INCONCLUSIVE":
        mech = "EURUSD n per context-strength bucket too small."
    else:
        mech = "H1 context extremeness not predictive on EURUSD."
    out.append(f"verdict: {status} (spread {spread:.2f}R)")
    out.append(f"mechanism: {mech}")
    out.append("")
    return ("\n".join(out), status, mech, spread)


def h5_direction_bias(eu, gb, jp) -> tuple[str, str, str, float]:
    out: list[str] = []
    out.append("## H5 — Direction bias (shorts vs longs)")
    out.append("")

    def split(trades):
        return {"short": [t for t in trades if t["direction"] == "short"],
                "long": [t for t in trades if t["direction"] == "long"]}

    eu_b = split(eu)
    out.append("EURUSD breakdown:")
    out.append(table_md(eu_b, ["short", "long"]))
    out.append("")
    out.append("GBPUSD/USDJPY contrast:")
    out.append("GBPUSD:")
    out.append(table_md(split(gb), ["short", "long"]))
    out.append("USDJPY:")
    out.append(table_md(split(jp), ["short", "long"]))
    out.append("")
    # Year breakdown of EURUSD shorts vs longs
    out.append("EURUSD per-year direction breakdown:")
    out.append("| year | n_short | exp_short | n_long | exp_long |")
    out.append("|---|---|---|---|---|")
    by_year: dict = defaultdict(lambda: {"short": [], "long": []})
    for t in eu:
        y = pd.to_datetime(t["date"]).year
        by_year[y][t["direction"]].append(t)
    for y in sorted(by_year.keys()):
        ss = stats(by_year[y]["short"])
        ls = stats(by_year[y]["long"])
        out.append(f"| {y} | {ss['n']} | {ss['exp']:+.3f} | {ls['n']} | {ls['exp']:+.3f} |")
    out.append("")
    status, spread, worst, best = verdict(eu_b)
    if status == "CONFIRMED":
        mech = (f"EURUSD '{worst}' direction loses vs '{best}'; "
                f"setup carries structural directional bias on this pair.")
    elif status == "INCONCLUSIVE":
        mech = "EURUSD n per direction too small."
    else:
        mech = "No directional bias on EURUSD (both sides perform similarly)."
    out.append(f"verdict: {status} (spread {spread:.2f}R)")
    out.append(f"mechanism: {mech}")
    out.append("")
    return ("\n".join(out), status, mech, spread)


def h6_direction_x_context(eu, gb, jp) -> tuple[str, str, str, float]:
    out: list[str] = []
    out.append("## H6 — Sweep direction × H1 context (2×2 matrix)")
    out.append("")
    out.append("Context_strong = ctx_extreme >= 0.5 ; marginal = ctx_extreme < 0.5")
    out.append("")
    def matrix(trades):
        m: dict = {"short_strong": [], "short_marginal": [],
                   "long_strong": [], "long_marginal": []}
        for t in trades:
            v = t.get("_ctx_extreme")
            if v is None:
                continue
            band = "strong" if v >= 0.5 else "marginal"
            m[f"{t['direction']}_{band}"].append(t)
        return m

    labels = ["short_strong", "short_marginal", "long_strong", "long_marginal"]
    eu_b = matrix(eu)
    out.append("EURUSD breakdown:")
    out.append(table_md(eu_b, labels))
    out.append("")
    out.append("GBPUSD/USDJPY contrast:")
    out.append("GBPUSD:")
    out.append(table_md(matrix(gb), labels))
    out.append("USDJPY:")
    out.append(table_md(matrix(jp), labels))
    out.append("")
    status, spread, worst, best = verdict(eu_b)
    if status == "CONFIRMED":
        mech = (f"EURUSD '{worst}' combo dominates losses; specific direction×context cell is the cancer.")
    elif status == "INCONCLUSIVE":
        mech = "EURUSD cells too small for direction×context verdict."
    else:
        mech = "No specific direction×context combination dominates EURUSD losses."
    out.append(f"verdict: {status} (spread {spread:.2f}R)")
    out.append(f"mechanism: {mech}")
    out.append("")
    return ("\n".join(out), status, mech, spread)


def h7_dxy_regime(eu, gb, jp) -> tuple[str, str, str, float]:
    out: list[str] = []
    out.append("## H7 — DXY proxy regime (EUR trend over last 120 H1 bars / ~5 days)")
    out.append("")
    out.append("EUR_up_DXY_down: delta_EUR > +50p over 120 H1 ; EUR_down_DXY_up: < -50p ; ranging: else")
    out.append("")

    def split(trades):
        d: dict = defaultdict(list)
        for t in trades:
            r = t.get("_regime")
            if r is not None:
                d[r].append(t)
        return d

    labels = ["EUR_up_DXY_down", "ranging", "EUR_down_DXY_up"]
    eu_b = split(eu)
    out.append("EURUSD breakdown:")
    out.append(table_md(eu_b, labels))
    out.append("")
    out.append("GBPUSD/USDJPY contrast:")
    out.append("GBPUSD:")
    out.append(table_md(split(gb), labels))
    out.append("USDJPY:")
    out.append(table_md(split(jp), labels))
    out.append("")
    # Cross with direction for EURUSD
    out.append("EURUSD regime × direction cross:")
    out.append("| regime | dir | n | wr% | exp_R |")
    out.append("|---|---|---|---|---|")
    cross: dict = defaultdict(list)
    for t in eu:
        r = t.get("_regime")
        if r is not None:
            cross[(r, t["direction"])].append(t)
    for r in labels:
        for d in ("short", "long"):
            s = stats(cross.get((r, d), []))
            out.append(f"| {r} | {d} | {s['n']} | {s['wr']:.1f} | {s['exp']:+.3f} |")
    out.append("")
    status, spread, worst, best = verdict(eu_b)
    if status == "CONFIRMED":
        mech = (f"EURUSD setup works in '{best}' regime, fails in '{worst}'; "
                f"setup is regime-dependent (not direction-agnostic).")
    elif status == "INCONCLUSIVE":
        mech = "EURUSD n per regime too small."
    else:
        mech = "No regime sensitivity detected on EURUSD."
    out.append(f"verdict: {status} (spread {spread:.2f}R)")
    out.append(f"mechanism: {mech}")
    out.append("")
    return ("\n".join(out), status, mech, spread)


def h8_sweep_hour(eu, gb, jp) -> tuple[str, str, str, float]:
    out: list[str] = []
    out.append("## H8 — Sweep hour (UTC) bias")
    out.append("")

    def split(trades):
        d: dict = defaultdict(list)
        for t in trades:
            h = pd.to_datetime(t["sweep_time"]).hour
            d[h].append(t)
        return d

    eu_b = split(eu)
    # labels = the actual sorted hours present
    labels = sorted(eu_b.keys())
    labels_str = [str(h) for h in labels]
    eu_disp = {str(h): eu_b[h] for h in labels}
    out.append("EURUSD breakdown:")
    out.append(table_md(eu_disp, labels_str))
    out.append("")
    out.append("GBPUSD/USDJPY contrast:")
    gb_b = split(gb)
    jp_b = split(jp)
    all_hours = sorted(set(list(eu_b) + list(gb_b) + list(jp_b)))
    all_hours_str = [str(h) for h in all_hours]
    out.append("GBPUSD:")
    out.append(table_md({str(h): gb_b[h] for h in all_hours if h in gb_b}, [str(h) for h in all_hours if h in gb_b]))
    out.append("USDJPY:")
    out.append(table_md({str(h): jp_b[h] for h in all_hours if h in jp_b}, [str(h) for h in all_hours if h in jp_b]))
    out.append("")
    # rebuild eu_b with string keys for verdict
    eu_b_str = {str(h): eu_b[h] for h in eu_b}
    status, spread, worst, best = verdict(eu_b_str)
    if status == "CONFIRMED":
        mech = (f"EURUSD sweep hour {worst} loses vs {best}; specific London sub-session matters.")
    elif status == "INCONCLUSIVE":
        mech = "EURUSD n per sweep hour too small."
    else:
        mech = "Sweep hour not predictive on EURUSD."
    _ = all_hours_str  # noqa: F841 (kept for symmetry with other H funcs)
    out.append(f"verdict: {status} (spread {spread:.2f}R)")
    out.append(f"mechanism: {mech}")
    out.append("")
    return ("\n".join(out), status, mech, spread)


# =====================================================================
# Main
# =====================================================================
def main() -> None:
    print(f"Loading trades from {IN_JSON}...")
    trades = json.loads(IN_JSON.read_text())
    by_pair: dict = defaultdict(list)
    for t in trades:
        by_pair[t["pair"]].append(t)
    eu = by_pair["EURUSD"]
    gb = by_pair["GBPUSD"]
    jp = by_pair["USDJPY"]
    print(f"  EURUSD: {len(eu)}  GBPUSD: {len(gb)}  USDJPY: {len(jp)}")

    print("Loading H1 data + enriching trades...")
    h1_by_pair = {p: load_h1(p) for p in PAIRS}
    enrich(eu, PIP["EURUSD"], h1_by_pair["EURUSD"])
    enrich(gb, PIP["GBPUSD"], h1_by_pair["GBPUSD"])
    enrich(jp, PIP["USDJPY"], h1_by_pair["USDJPY"])

    sections: list[str] = []
    sections.append("# EURUSD Root Cause Diagnostic — Asia London Sweep (relax_all)\n")
    sections.append(f"Source: `{IN_JSON.name}`. EURUSD n={len(eu)}, GBPUSD n={len(gb)}, USDJPY n={len(jp)}.\n")
    sections.append(f"Baseline EURUSD: {stats(eu)}.\n")

    results: list[tuple[str, str, str, float]] = []
    for fn in (h1_asia_range_too_wide, h2_sweep_depth, h3_fvg_ratio,
               h4_h1_context_marginal, h5_direction_bias, h6_direction_x_context,
               h7_dxy_regime, h8_sweep_hour):
        block, status, mech, spread = fn(eu, gb, jp)
        sections.append(block)
        results.append((fn.__name__, status, mech, spread))

    # Synthesis
    confirmed = [r for r in results if r[1] == "CONFIRMED"]
    confirmed.sort(key=lambda r: -r[3])
    inconclusive = [r for r in results if r[1] == "INCONCLUSIVE"]
    rejected = [r for r in results if r[1] == "REJECTED"]

    synth: list[str] = []
    synth.append("## SYNTHESIS")
    synth.append("")
    synth.append("Hypothèses confirmées (ranked by effect size):")
    if not confirmed:
        synth.append("  (none)")
    else:
        for i, (name, _, mech, spread) in enumerate(confirmed, 1):
            h_id = name.split("_")[0].upper()
            synth.append(f"  {i}. {h_id} ({name}): spread={spread:.2f}R — {mech}")
    synth.append("")
    synth.append("Hypothèses inconclusives (n trop faible):")
    if not inconclusive:
        synth.append("  (none)")
    else:
        for name, _, _, _ in inconclusive:
            synth.append(f"  - {name.split('_')[0].upper()} ({name})")
    synth.append("")
    synth.append("Hypothèses rejetées:")
    if not rejected:
        synth.append("  (none)")
    else:
        for name, _, _, spread in rejected:
            synth.append(f"  - {name.split('_')[0].upper()} ({name}): spread={spread:.2f}R")
    synth.append("")
    if confirmed:
        dominant_name, _, dominant_mech, dominant_spread = confirmed[0]
        synth.append(f"Mécanisme dominant: {dominant_mech} (effect size {dominant_spread:.2f}R)")
    else:
        dominant_name, dominant_mech, dominant_spread = "none", "no hypothesis confirmed at threshold", 0.0
        synth.append(f"Mécanisme dominant: {dominant_mech}")
    synth.append("")
    synth.append("Implications:")
    if confirmed:
        synth.append(f"  - EURUSD: apply filter from top confirmed hypothesis ({dominant_name})")
        # Generic transfer check: if INCONCLUSIVE for contrast or REJECTED but visible pattern, flag
        synth.append("  - GBPUSD/JPY: contrast tables above show if filter transfers ; usually edge already")
        synth.append("    healthy on GBPUSD so transfer may shrink N without gain")
        synth.append("  - Setup global: keep as-is for GBPUSD/JPY, gate EURUSD with new filter")
    else:
        synth.append("  - EURUSD: drop pair from setup ; no clean filter isolates losing subset")
        synth.append("  - GBPUSD/JPY: keep unchanged")
        synth.append("  - Setup global: no modification")
    synth.append("")
    # Filter proposed: from top confirmed mechanism
    synth.append("Filter proposed:")
    if not confirmed:
        synth.append("  none — no significant subset isolated ; recommend dropping EURUSD")
        proposed_filter = "DROP EURUSD"
    else:
        top_name = confirmed[0][0]
        # build operational spec per H
        if top_name == "h1_asia_range_too_wide":
            proposed_filter = "EURUSD only: skip if asia_range_pips > tertile_3 cutoff"
        elif top_name == "h2_sweep_depth":
            proposed_filter = "EURUSD only: skip if sweep_depth < N pips (see worst bucket in H2)"
        elif top_name == "h3_fvg_ratio":
            proposed_filter = "EURUSD only: skip if FVG/asia_range ratio outside winning bucket"
        elif top_name == "h4_h1_context_marginal":
            proposed_filter = "EURUSD only: require ctx_extreme >= 0.5 (drop marginal context trades)"
        elif top_name == "h5_direction_bias":
            proposed_filter = "EURUSD only: trade one direction only (winning side per H5 table)"
        elif top_name == "h6_direction_x_context":
            proposed_filter = "EURUSD only: trade only winning direction×context cell from H6"
        elif top_name == "h7_dxy_regime":
            proposed_filter = "EURUSD only: trade only in winning DXY regime per H7"
        elif top_name == "h8_sweep_hour":
            proposed_filter = "EURUSD only: restrict sweep hour to winning bucket per H8"
        else:
            proposed_filter = "see top hypothesis details"
        synth.append(f"  {proposed_filter}")
    synth.append("")
    sections.append("\n".join(synth))

    # Print everything + capture
    full_report = "\n".join(sections)
    print(full_report)

    # Persist
    OUT_REPORT.write_text(full_report)

    # Deliverable footer
    confirmed_ids = [r[0].split("_")[0].upper() for r in confirmed]
    print("\n\ndone")
    print(f"files: {Path('sandbox') / 'diagnose_eurusd.py'}, "
          f"{Path('sandbox') / 'diagnose_eurusd_report.md'}")
    print(f"confirmed_hypotheses: {', '.join(confirmed_ids) if confirmed_ids else 'none'}")
    print(f"dominant_mechanism: {dominant_mech}")
    print(f"proposed_filter: {proposed_filter}")


if __name__ == "__main__":
    main()
