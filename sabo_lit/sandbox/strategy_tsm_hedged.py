"""
Sandbox — Time-Series Momentum (TSM) cross-pair daily, USD-basket beta hedged.

Strategy: each pair daily signal = sign(past 21d cumulative return). Position
equal-weight nominal (±1/6 per pair). Rebalance daily.

Hedge: regress unhedged daily P&L on USD-basket return (in-sample beta only),
subtract beta x USD_basket from daily P&L to produce USD-neutral alpha.

Academic backing: Moskowitz, Ooi, Pedersen (2012) — "Time Series Momentum".
Robust across 58 instruments, 30+ years.

Validation stack:
  - IS 2019-2023, OOS 2024-2025 (strict chronological split)
  - Random baseline: 1000 iterations of random ±1 signals per pair per day
  - Bootstrap 1000 resamples on daily P&L → Sharpe CI95
  - Per-year breakdown

Hard verdicts:
  ROBUST   : OOS Sharpe > 0.5 AND > random baseline 95p AND bootstrap CI low > 0
  WEAK     : OOS Sharpe > 0 but fails one of above
  DEAD     : OOS Sharpe <= 0 OR random baseline beats actual

Ambiguity decisions (inline):
  - "Day" = UTC calendar day. Close = last M5 close in that day.
  - Forward-fill missing daily close ?  No — drop NaN, momentum signal needs
    valid returns. Weekend gaps handled by skipping.
  - Daily return = pct_change of close-to-close
  - Lookback 21 days = ~1 calendar month (academic convention; not optimized)
  - Position sizing : equal-weight nominal, no vol-targeting (keep simple ;
    refine if results positive)
  - Costs : 0 here ; spread/commission added in v2 if validated
  - USD basket : equal-weight mean of USD-direction-adjusted pair returns
  - Hedge beta : OLS on IS only, applied to OOS (no peek-ahead)
"""
from __future__ import annotations

import json
import math
import random
import statistics
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


HERE = Path(__file__).parent
DATA = HERE / "data"
OUT_REPORT = HERE / "strategy_tsm_hedged_report.md"
OUT_EQUITY = HERE / "strategy_tsm_hedged_equity.html"
OUT_RETURNS = HERE / "strategy_tsm_hedged_returns.csv"
OUT_METRICS = HERE / "strategy_tsm_hedged_metrics.json"

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD"]

# +1 = long pair => long USD ; -1 = long pair => short USD
USD_DIR = {
    "EURUSD": -1, "GBPUSD": -1, "AUDUSD": -1, "NZDUSD": -1,
    "USDJPY": +1, "USDCAD": +1,
}

FILES_M5 = {
    "EURUSD": "eurusd-m5-bid-2019-01-01-2026-01-01.csv",
    "GBPUSD": "gbpusd-m5-bid-2019-01-01-2026-01-01.csv",
    "USDJPY": "usdjpy-m5-bid-2019-01-01-2026-01-01.csv",
    "AUDUSD": "audusd-m5-bid-2019-01-01-2026-01-01.csv",
    "NZDUSD": "nzdusd-m5-bid-2019-01-01-2026-01-01.csv",
    "USDCAD": "usdcad-m5-bid-2019-01-01-2026-01-01.csv",
}

LOOKBACK = 21
TRADING_DAYS = 252
SEED = 42
IS_START = pd.Timestamp("2019-01-01", tz="UTC")
IS_END = pd.Timestamp("2023-12-31 23:59:59", tz="UTC")
OOS_START = pd.Timestamp("2024-01-01", tz="UTC")
OOS_END = pd.Timestamp("2025-12-31 23:59:59", tz="UTC")
N_RANDOM = 1000
N_BOOT = 1000


# =====================================================================
# IO
# =====================================================================
def load_m5(pair: str) -> pd.DataFrame:
    df = pd.read_csv(DATA / FILES_M5[pair])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    return df


def to_daily_close(m5: pd.DataFrame) -> pd.Series:
    """Last close per UTC calendar day. Drops empty bins (weekends)."""
    return m5["close"].resample("1D").last().dropna()


# =====================================================================
# Signal + portfolio
# =====================================================================
def tsm_signal(returns: pd.Series, lookback: int) -> pd.Series:
    """+1 if cum past-lookback return > 0, -1 if < 0, else 0.
    Signal shifted forward 1 day to avoid look-ahead at execution."""
    cum = (1.0 + returns).rolling(lookback).apply(lambda x: x.prod() - 1.0, raw=True)
    sig = pd.Series(0.0, index=cum.index)
    sig[cum > 0] = 1.0
    sig[cum < 0] = -1.0
    return sig.shift(1)


def build_returns_df(closes_by_pair: dict) -> pd.DataFrame:
    """Wide DataFrame of daily returns aligned on intersection of indices."""
    rets = {p: closes_by_pair[p].pct_change() for p in PAIRS}
    df = pd.DataFrame(rets).dropna()
    return df


def build_signals_df(returns_df: pd.DataFrame, lookback: int) -> pd.DataFrame:
    sig = {p: tsm_signal(returns_df[p], lookback) for p in PAIRS}
    return pd.DataFrame(sig).dropna()


def portfolio_pl(signals: pd.DataFrame, returns: pd.DataFrame) -> pd.Series:
    """Equal-weight nominal. Per-day P&L = mean across pairs of (signal × return)."""
    common = signals.index.intersection(returns.index)
    sigs = signals.loc[common]
    rets = returns.loc[common]
    return (sigs * rets).mean(axis=1)


def usd_basket_returns(returns: pd.DataFrame) -> pd.Series:
    """USD basket daily return = mean of USD-direction-adjusted pair returns."""
    parts = pd.DataFrame({p: USD_DIR[p] * returns[p] for p in PAIRS})
    return parts.mean(axis=1)


def fit_hedge_beta(pl_unhedged: pd.Series, usd_basket: pd.Series) -> float:
    common = pl_unhedged.index.intersection(usd_basket.index)
    x = usd_basket.loc[common]
    y = pl_unhedged.loc[common]
    var_x = x.var()
    if var_x == 0 or pd.isna(var_x):
        return 0.0
    cov_xy = ((x - x.mean()) * (y - y.mean())).mean()
    return float(cov_xy / var_x)


def hedged_pl(pl_unhedged: pd.Series, usd_basket: pd.Series, beta: float) -> pd.Series:
    common = pl_unhedged.index.intersection(usd_basket.index)
    return pl_unhedged.loc[common] - beta * usd_basket.loc[common]


# =====================================================================
# Metrics
# =====================================================================
def metrics(pl: pd.Series) -> dict:
    pl = pl.dropna()
    if len(pl) < 2:
        return {"n": 0, "sharpe": 0.0, "ann_return": 0.0, "ann_vol": 0.0,
                "max_dd": 0.0, "wr": 0.0, "total_R_units": 0.0,
                "calmar": 0.0, "skew": 0.0, "kurt": 0.0}
    mean = float(pl.mean())
    std = float(pl.std())
    sharpe = (mean / std) * math.sqrt(TRADING_DAYS) if std > 0 else 0.0
    ann_return = mean * TRADING_DAYS
    ann_vol = std * math.sqrt(TRADING_DAYS)
    cum = pl.cumsum()
    peak = cum.cummax()
    dd = cum - peak
    max_dd = float(dd.min())
    wr = float((pl > 0).mean() * 100)
    total = float(pl.sum())
    calmar = (ann_return / abs(max_dd)) if max_dd < 0 else float("inf")
    # Fisher skew / excess kurt
    if std > 0:
        z = (pl - mean) / std
        skew = float((z ** 3).mean())
        kurt = float((z ** 4).mean() - 3.0)
    else:
        skew, kurt = 0.0, 0.0
    return {"n": len(pl), "sharpe": sharpe, "ann_return": ann_return,
            "ann_vol": ann_vol, "max_dd": max_dd, "wr": wr,
            "total_R_units": total, "calmar": calmar,
            "skew": skew, "kurt": kurt}


def fmt_metrics(m: dict) -> str:
    return (f"n={m['n']}  Sharpe={m['sharpe']:+.2f}  "
            f"ann_ret={m['ann_return']*100:+.2f}%  ann_vol={m['ann_vol']*100:.2f}%  "
            f"maxDD={m['max_dd']*100:+.2f}%  wr={m['wr']:.1f}%  "
            f"calmar={m['calmar']:.2f}  skew={m['skew']:+.2f}  kurt={m['kurt']:+.2f}")


# =====================================================================
# Random baseline
# =====================================================================
def random_baseline_sharpes(returns_df: pd.DataFrame, n_iter: int, seed: int) -> list[float]:
    """Random ±1 signal per pair per day. Returns list of n_iter Sharpe values."""
    rng = random.Random(seed)
    n_days = len(returns_df)
    n_pairs = len(PAIRS)
    sharpes: list[float] = []
    for _ in range(n_iter):
        sigs_arr = [[rng.choice([-1.0, 1.0]) for _ in range(n_pairs)] for _ in range(n_days)]
        sigs_df = pd.DataFrame(sigs_arr, index=returns_df.index, columns=PAIRS)
        pl = (sigs_df * returns_df).mean(axis=1)
        m = metrics(pl)
        sharpes.append(m["sharpe"])
    sharpes.sort()
    return sharpes


# =====================================================================
# Bootstrap
# =====================================================================
def bootstrap_sharpe(pl: pd.Series, n_resample: int, seed: int) -> dict:
    rng = random.Random(seed)
    arr = pl.dropna().to_list()
    n = len(arr)
    if n < 2:
        return {"ci_low": 0.0, "ci_high": 0.0, "median": 0.0}
    sharpes: list[float] = []
    for _ in range(n_resample):
        sample = [arr[rng.randrange(n)] for _ in range(n)]
        m = sum(sample) / n
        var = sum((x - m) ** 2 for x in sample) / (n - 1)
        std = math.sqrt(var)
        s = (m / std) * math.sqrt(TRADING_DAYS) if std > 0 else 0.0
        sharpes.append(s)
    sharpes.sort()
    lo = sharpes[int(n_resample * 0.025)]
    hi = sharpes[int(n_resample * 0.975) - 1]
    med = sharpes[n_resample // 2]
    return {"ci_low": lo, "ci_high": hi, "median": med}


# =====================================================================
# Per-year breakdown
# =====================================================================
def per_year_table(pl: pd.Series) -> list[tuple]:
    out: list[tuple] = []
    for y in range(2019, 2026):
        sub = pl[pl.index.year == y]
        if len(sub) < 5:
            continue
        m = metrics(sub)
        out.append((y, m))
    return out


# =====================================================================
# Equity HTML
# =====================================================================
def write_equity(pl_unh: pd.Series, pl_hed: pd.Series, usd_b: pd.Series,
                 is_end: pd.Timestamp) -> None:
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                        subplot_titles=("Unhedged TSM cum return",
                                        "Hedged TSM cum return (USD-neutral)",
                                        "USD basket cum return"),
                        vertical_spacing=0.06)
    fig.add_trace(go.Scatter(x=pl_unh.index, y=pl_unh.cumsum(),
                             mode="lines", name="unhedged", line=dict(color="#1976d2")),
                  row=1, col=1)
    fig.add_trace(go.Scatter(x=pl_hed.index, y=pl_hed.cumsum(),
                             mode="lines", name="hedged", line=dict(color="#2e7d32")),
                  row=2, col=1)
    fig.add_trace(go.Scatter(x=usd_b.index, y=usd_b.cumsum(),
                             mode="lines", name="USD basket", line=dict(color="#888888")),
                  row=3, col=1)
    # IS/OOS split visible via subplot title note instead of vline
    _ = is_end
    fig.update_layout(
        title="TSM cross-pair daily — hedged vs unhedged vs USD basket",
        template="plotly_white", height=1100, hovermode="x",
    )
    fig.update_yaxes(title_text="cum return", row=1, col=1)
    fig.update_yaxes(title_text="cum return", row=2, col=1)
    fig.update_yaxes(title_text="cum return", row=3, col=1)
    fig.update_xaxes(title_text="date", row=3, col=1)
    fig.write_html(str(OUT_EQUITY), include_plotlyjs="cdn")


# =====================================================================
# Main
# =====================================================================
def main() -> None:
    print("Loading M5 data + resampling to daily close...")
    closes: dict = {}
    for p in PAIRS:
        m5 = load_m5(p)
        closes[p] = to_daily_close(m5)
        print(f"  {p}: M5={len(m5)}  daily_close_pts={len(closes[p])}")
    rets = build_returns_df(closes)
    print(f"Returns df: {len(rets)} aligned days × {len(PAIRS)} pairs ; "
          f"range {rets.index[0].date()} → {rets.index[-1].date()}")

    sigs = build_signals_df(rets, LOOKBACK)
    pl_unh_full = portfolio_pl(sigs, rets)
    usd_b_full = usd_basket_returns(rets).loc[pl_unh_full.index]

    # Split IS / OOS
    is_mask = (pl_unh_full.index >= IS_START) & (pl_unh_full.index <= IS_END)
    oos_mask = (pl_unh_full.index >= OOS_START) & (pl_unh_full.index <= OOS_END)
    pl_unh_is = pl_unh_full[is_mask]
    pl_unh_oos = pl_unh_full[oos_mask]
    usd_b_is = usd_b_full[is_mask]
    usd_b_oos = usd_b_full[oos_mask]

    # Fit hedge beta on IS, apply to both
    beta = fit_hedge_beta(pl_unh_is, usd_b_is)
    pl_hed_is = hedged_pl(pl_unh_is, usd_b_is, beta)
    pl_hed_oos = hedged_pl(pl_unh_oos, usd_b_oos, beta)
    pl_hed_full = hedged_pl(pl_unh_full, usd_b_full, beta)

    # Random baseline (full sample)
    print(f"\nRandom baseline: {N_RANDOM} iterations of random ±1 signals...")
    rand_sharpes = random_baseline_sharpes(rets.loc[pl_unh_full.index], N_RANDOM, SEED)
    rand_med = rand_sharpes[N_RANDOM // 2]
    rand_p95 = rand_sharpes[int(N_RANDOM * 0.95)]
    rand_p5 = rand_sharpes[int(N_RANDOM * 0.05)]
    print(f"  random Sharpe distribution: median={rand_med:+.2f} "
          f"p5={rand_p5:+.2f} p95={rand_p95:+.2f}")

    # Bootstrap CI on Sharpe — hedged OOS
    print(f"\nBootstrap CI (n_resample={N_BOOT}) on hedged OOS daily P&L...")
    boot_oos = bootstrap_sharpe(pl_hed_oos, N_BOOT, SEED)
    print(f"  hedged OOS Sharpe CI95: [{boot_oos['ci_low']:+.2f}, {boot_oos['ci_high']:+.2f}] "
          f"median={boot_oos['median']:+.2f}")
    boot_full = bootstrap_sharpe(pl_hed_full, N_BOOT, SEED)

    # Compute metrics
    m_unh_is = metrics(pl_unh_is)
    m_unh_oos = metrics(pl_unh_oos)
    m_hed_is = metrics(pl_hed_is)
    m_hed_oos = metrics(pl_hed_oos)
    m_unh_full = metrics(pl_unh_full)
    m_hed_full = metrics(pl_hed_full)
    m_usd_full = metrics(usd_b_full)

    # ----- Report markdown -----
    lines: list[str] = []
    def emit(s: str) -> None:
        print(s)
        lines.append(s)

    emit("# Strategy TSM Cross-Pair Daily, USD-basket Beta Hedged")
    emit("")
    emit(f"Pairs: {', '.join(PAIRS)}")
    emit(f"Lookback: {LOOKBACK} days (~1 month). Position: equal-weight ±1/6 per pair. "
         f"Rebalance daily.")
    emit(f"Data: {rets.index[0].date()} → {rets.index[-1].date()} "
         f"({len(rets)} aligned trading days × {len(PAIRS)} pairs).")
    emit("IS: 2019-01-01 → 2023-12-31 | OOS: 2024-01-01 → 2025-12-31")
    emit(f"Hedge beta (fitted on IS): **{beta:+.3f}**")
    emit("")

    emit("## METRICS — IS (2019-2023)")
    emit(f"  Unhedged: {fmt_metrics(m_unh_is)}")
    emit(f"  Hedged  : {fmt_metrics(m_hed_is)}")
    emit("")

    emit("## METRICS — OOS (2024-2025)")
    emit(f"  Unhedged: {fmt_metrics(m_unh_oos)}")
    emit(f"  Hedged  : {fmt_metrics(m_hed_oos)}")
    emit("")

    emit("## METRICS — Full sample")
    emit(f"  Unhedged   : {fmt_metrics(m_unh_full)}")
    emit(f"  Hedged     : {fmt_metrics(m_hed_full)}")
    emit(f"  USD basket : {fmt_metrics(m_usd_full)}  (passive long-USD)")
    emit("")

    emit("## RANDOM BASELINE (1000 iterations)")
    emit("  random Sharpe distribution (full sample, equal-weight ±1 random per pair per day):")
    emit(f"    p5={rand_p5:+.2f}  median={rand_med:+.2f}  p95={rand_p95:+.2f}")
    emit(f"  Actual hedged FULL Sharpe={m_hed_full['sharpe']:+.2f}  → "
         f"{'BEATS' if m_hed_full['sharpe'] > rand_p95 else 'FAILS'} 95p random baseline")
    emit(f"  Actual hedged OOS Sharpe={m_hed_oos['sharpe']:+.2f}  → "
         f"{'BEATS' if m_hed_oos['sharpe'] > rand_p95 else 'FAILS'} 95p random baseline (same dist)")
    emit("")

    emit("## BOOTSTRAP CI95 — Hedged Sharpe")
    emit(f"  OOS  : [{boot_oos['ci_low']:+.2f}, {boot_oos['ci_high']:+.2f}] "
         f"median={boot_oos['median']:+.2f}")
    emit(f"  FULL : [{boot_full['ci_low']:+.2f}, {boot_full['ci_high']:+.2f}] "
         f"median={boot_full['median']:+.2f}")
    emit("")

    emit("## PER-YEAR HEDGED")
    emit("| year | n_days | Sharpe | ann_ret% | maxDD% | wr% |")
    emit("|---|---|---|---|---|---|")
    for y, m in per_year_table(pl_hed_full):
        emit(f"| {y} | {m['n']} | {m['sharpe']:+.2f} | {m['ann_return']*100:+.2f} | "
             f"{m['max_dd']*100:+.2f} | {m['wr']:.1f} |")
    emit("")

    emit("## PER-YEAR UNHEDGED (for comparison)")
    emit("| year | n_days | Sharpe | ann_ret% | maxDD% | wr% |")
    emit("|---|---|---|---|---|---|")
    for y, m in per_year_table(pl_unh_full):
        emit(f"| {y} | {m['n']} | {m['sharpe']:+.2f} | {m['ann_return']*100:+.2f} | "
             f"{m['max_dd']*100:+.2f} | {m['wr']:.1f} |")
    emit("")

    # ----- Verdict -----
    beats_random = m_hed_oos["sharpe"] > rand_p95
    sharpe_floor = m_hed_oos["sharpe"] > 0.5
    ci_positive = boot_oos["ci_low"] > 0
    if beats_random and sharpe_floor and ci_positive:
        verdict = "ROBUST"
    elif m_hed_oos["sharpe"] > 0:
        verdict = "WEAK"
    else:
        verdict = "DEAD"

    emit("## VERDICT")
    emit(f"  OOS hedged Sharpe={m_hed_oos['sharpe']:+.2f}  "
         f"(> 0.5 ? {'YES' if sharpe_floor else 'NO'})")
    emit(f"  OOS hedged Sharpe > random 95p ({rand_p95:+.2f}) ? "
         f"{'YES' if beats_random else 'NO'}")
    emit(f"  OOS bootstrap CI low ({boot_oos['ci_low']:+.2f}) > 0 ? "
         f"{'YES' if ci_positive else 'NO'}")
    emit("")
    emit(f"  FINAL VERDICT: **{verdict}**")
    emit("")

    if verdict == "ROBUST":
        next_step = ("Add costs (2bps spread + 0.5bps commission per pair-day rebalance), "
                     "re-validate. If still > Sharpe 0.4 OOS net of costs: paper-trade live "
                     "30 days starting next week. Then extend universe (add JPY-crosses, "
                     "EM FX, indices).")
    elif verdict == "WEAK":
        next_step = ("Try variants: (a) lookback 63d & 126d (sweep), (b) vol-target sizing, "
                     "(c) trend filter (only trade if all 3 lookbacks agree). If none "
                     "achieves ROBUST after gating, drop TSM-on-6-FX-pairs.")
    else:
        next_step = ("TSM dead on 6 FX pairs. Pivot to (a) cross-sectional momentum "
                     "(rank pairs, long top/short bottom), (b) carry proxy via interest-rate "
                     "differential, (c) accept FX day-trading not viable, switch asset class "
                     "(crypto / equities cross-section).")
    emit(f"  next_step: {next_step}")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))

    # Persist daily P&L CSV
    out_df = pd.DataFrame({
        "unhedged": pl_unh_full,
        "hedged": pl_hed_full,
        "usd_basket": usd_b_full,
    })
    out_df.to_csv(OUT_RETURNS)

    # Persist metrics JSON
    OUT_METRICS.write_text(json.dumps({
        "hedge_beta": beta,
        "IS": {"unhedged": m_unh_is, "hedged": m_hed_is},
        "OOS": {"unhedged": m_unh_oos, "hedged": m_hed_oos},
        "FULL": {"unhedged": m_unh_full, "hedged": m_hed_full,
                 "usd_basket": m_usd_full},
        "random_baseline_full": {
            "p5": rand_p5, "median": rand_med, "p95": rand_p95,
        },
        "bootstrap_oos": boot_oos,
        "bootstrap_full": boot_full,
        "verdict": verdict,
    }, indent=2, default=str))

    # Write equity HTML
    write_equity(pl_unh_full, pl_hed_full, usd_b_full, IS_END)

    # ----- Deliverable -----
    print("\n\ndone")
    print(f"files: {OUT_REPORT.name}, {OUT_EQUITY.name}, {OUT_RETURNS.name}, "
          f"{OUT_METRICS.name}")
    print(f"hedge_beta: {beta:+.3f}")
    print(f"IS hedged Sharpe = {m_hed_is['sharpe']:+.2f}")
    print(f"OOS hedged Sharpe = {m_hed_oos['sharpe']:+.2f}  "
          f"(random 95p = {rand_p95:+.2f})")
    print(f"OOS bootstrap CI95 = [{boot_oos['ci_low']:+.2f}, {boot_oos['ci_high']:+.2f}]")
    print(f"verdict: {verdict}")
    # silence unused statistics import
    _ = statistics


if __name__ == "__main__":
    main()
