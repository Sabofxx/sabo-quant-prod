"""
Sandbox — multi-strategy comparator on 6 FX pairs daily.

Tests N candidate strategies simultaneously, applies USD-basket beta hedge
(IS-fitted) to each, runs same validation stack (IS/OOS split, bootstrap CI,
random baseline). Picks winner if any beats random 95p + CI_low > 0 + Sharpe > 0.5.

Strategies compared:
  - TSM_21 / TSM_63 / TSM_126 : Time-Series Momentum, lookback 21/63/126d
  - CSM_21 / CSM_63           : Cross-Sectional Momentum, rank, long top 2 / short bottom 2
  - MR_1d                     : 1-day mean reversion (sign-flip of prev day return)
  - MR_5d                     : 5-day cumulative reversion
  - TSM_63_VOL                : TSM 63d with inverse-vol position sizing (20d vol)

For each : IS / OOS hedged Sharpe + bootstrap CI95 low + verdict.

Ambiguity (inline):
  - CSM rank: ties broken alphabetically by pair name (stable)
  - CSM middle 2 pairs get 0 weight
  - MR reversal sign convention: if past N-day return > 0 → short, vice versa
  - Vol-target sizing: signal × (target_vol / realized_20d_vol) capped at 3x leverage
  - Same hedge protocol as v1: USD basket beta on IS, applied OOS
  - Random baseline same per strategy: 200 iter (lighter than v1 since 8x strats)
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go


HERE = Path(__file__).parent
DATA = HERE / "data"
OUT_REPORT = HERE / "strategy_compare_report.md"
OUT_METRICS = HERE / "strategy_compare_metrics.json"
OUT_EQUITY = HERE / "strategy_compare_equity.html"

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD"]
USD_DIR = {
    "EURUSD": -1, "GBPUSD": -1, "AUDUSD": -1, "NZDUSD": -1,
    "USDJPY": +1, "USDCAD": +1,
}
FILES_M5 = {p: f"{p.lower()}-m5-bid-2019-01-01-2026-01-01.csv" for p in PAIRS}

TRADING_DAYS = 252
SEED = 42
IS_END = pd.Timestamp("2023-12-31 23:59:59", tz="UTC")
OOS_START = pd.Timestamp("2024-01-01", tz="UTC")
N_RANDOM = 200
N_BOOT = 1000
TARGET_VOL = 0.10  # 10% annualized
VOL_CAP = 3.0


# =====================================================================
# IO
# =====================================================================
def load_m5(pair: str) -> pd.DataFrame:
    df = pd.read_csv(DATA / FILES_M5[pair])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    return df[~df.index.duplicated(keep="first")]


def daily_closes() -> dict:
    out = {}
    for p in PAIRS:
        out[p] = load_m5(p)["close"].resample("1D").last().dropna()
    return out


def build_returns(closes: dict) -> pd.DataFrame:
    return pd.DataFrame({p: closes[p].pct_change() for p in PAIRS}).dropna()


# =====================================================================
# Signal generators -> wide DataFrame of signals (shifted +1)
# =====================================================================
def sig_tsm(returns: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """+1 / -1 / 0 per pair per day based on past-lookback cumulative return sign.
    Equal weight ±1/n_pairs."""
    cum = (1.0 + returns).rolling(lookback).apply(lambda x: x.prod() - 1.0, raw=True)
    sig = (cum > 0).astype(float) - (cum < 0).astype(float)
    sig = sig / len(PAIRS)
    return sig.shift(1).dropna(how="all")


def sig_csm(returns: pd.DataFrame, lookback: int, top_n: int = 2) -> pd.DataFrame:
    """Rank pairs by past lookback return ; long top_n / short bottom_n ; rest=0.
    Equal weight ±1/(2*top_n)."""
    cum = (1.0 + returns).rolling(lookback).apply(lambda x: x.prod() - 1.0, raw=True)
    ranks = cum.rank(axis=1, method="first")  # 1 = lowest, n = highest
    n_pairs = len(PAIRS)
    weight = 1.0 / (2 * top_n)
    sig = pd.DataFrame(0.0, index=cum.index, columns=PAIRS)
    sig[ranks > n_pairs - top_n] = +weight  # top_n = long
    sig[ranks <= top_n] = -weight           # bottom_n = short
    return sig.shift(1).dropna(how="all")


def sig_mr(returns: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """Mean reversion: short if past-lookback cum > 0, long if < 0. Equal weight."""
    cum = (1.0 + returns).rolling(lookback).apply(lambda x: x.prod() - 1.0, raw=True)
    sig = (cum < 0).astype(float) - (cum > 0).astype(float)
    sig = sig / len(PAIRS)
    return sig.shift(1).dropna(how="all")


def sig_tsm_voltargeted(returns: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """TSM but with inverse-vol weighting per pair, target portfolio vol = TARGET_VOL annual."""
    cum = (1.0 + returns).rolling(lookback).apply(lambda x: x.prod() - 1.0, raw=True)
    sign = (cum > 0).astype(float) - (cum < 0).astype(float)
    # 20-day realized vol per pair, annualized
    rv = returns.rolling(20).std() * math.sqrt(TRADING_DAYS)
    # weight = sign * (target_vol / pair_vol), per pair
    raw_weight = sign * (TARGET_VOL / rv.replace(0, float("nan")))
    raw_weight = raw_weight.fillna(0.0)
    # cap leverage per pair
    capped = raw_weight.clip(-VOL_CAP, VOL_CAP)
    # normalize by n_pairs (equal-budget contribution)
    sig = capped / len(PAIRS)
    return sig.shift(1).dropna(how="all")


# =====================================================================
# Portfolio P&L + hedge
# =====================================================================
def portfolio_pl(signals: pd.DataFrame, returns: pd.DataFrame) -> pd.Series:
    common = signals.index.intersection(returns.index)
    return (signals.loc[common] * returns.loc[common]).sum(axis=1)


def usd_basket(returns: pd.DataFrame) -> pd.Series:
    parts = pd.DataFrame({p: USD_DIR[p] * returns[p] for p in PAIRS})
    return parts.mean(axis=1)


def fit_beta(pl: pd.Series, basket: pd.Series) -> float:
    common = pl.index.intersection(basket.index)
    x = basket.loc[common]
    y = pl.loc[common]
    vx = x.var()
    if vx == 0 or pd.isna(vx):
        return 0.0
    cov = ((x - x.mean()) * (y - y.mean())).mean()
    return float(cov / vx)


def hedge(pl: pd.Series, basket: pd.Series, beta: float) -> pd.Series:
    common = pl.index.intersection(basket.index)
    return pl.loc[common] - beta * basket.loc[common]


# =====================================================================
# Metrics
# =====================================================================
def metrics(pl: pd.Series) -> dict:
    pl = pl.dropna()
    if len(pl) < 2:
        return {"n": 0, "sharpe": 0.0, "ann_ret": 0.0, "ann_vol": 0.0,
                "max_dd": 0.0, "wr": 0.0, "calmar": 0.0}
    mean = float(pl.mean())
    std = float(pl.std())
    sharpe = (mean / std) * math.sqrt(TRADING_DAYS) if std > 0 else 0.0
    cum = pl.cumsum()
    dd = (cum - cum.cummax()).min()
    return {"n": len(pl), "sharpe": sharpe, "ann_ret": mean * TRADING_DAYS,
            "ann_vol": std * math.sqrt(TRADING_DAYS), "max_dd": float(dd),
            "wr": float((pl > 0).mean() * 100),
            "calmar": (mean * TRADING_DAYS) / abs(dd) if dd < 0 else float("inf")}


def bootstrap_sharpe_ci(pl: pd.Series, n_resample: int, seed: int) -> tuple[float, float, float]:
    rng = random.Random(seed)
    arr = pl.dropna().to_list()
    n = len(arr)
    if n < 2:
        return 0.0, 0.0, 0.0
    out: list[float] = []
    for _ in range(n_resample):
        sample = [arr[rng.randrange(n)] for _ in range(n)]
        m = sum(sample) / n
        var = sum((x - m) ** 2 for x in sample) / (n - 1)
        std = math.sqrt(var)
        s = (m / std) * math.sqrt(TRADING_DAYS) if std > 0 else 0.0
        out.append(s)
    out.sort()
    return out[int(n_resample * 0.025)], out[n_resample // 2], out[int(n_resample * 0.975) - 1]


def random_baseline_p95(returns: pd.DataFrame, n_iter: int, seed: int,
                        sig_template: pd.DataFrame) -> tuple[float, float, float]:
    """Generate random signal df with same shape/values as sig_template's non-zero set,
    compute portfolio P&L, return (p5, median, p95) of Sharpe distribution.
    Approach: for each day, replicate signal sparsity (how many longs/shorts)
    but randomize which pairs."""
    rng = random.Random(seed)
    idx = sig_template.index.intersection(returns.index)
    rets = returns.loc[idx]
    sigs_template = sig_template.loc[idx]
    sharpes: list[float] = []
    for _ in range(n_iter):
        sig_matrix = []
        for ts in idx:
            row = sigs_template.loc[ts].values
            shuffled = list(row)
            rng.shuffle(shuffled)
            sig_matrix.append(shuffled)
        sig_df = pd.DataFrame(sig_matrix, index=idx, columns=PAIRS)
        pl = (sig_df * rets).sum(axis=1)
        m = metrics(pl)
        sharpes.append(m["sharpe"])
    sharpes.sort()
    return (sharpes[int(n_iter * 0.05)], sharpes[n_iter // 2],
            sharpes[int(n_iter * 0.95)])


# =====================================================================
# Strategy harness
# =====================================================================
def run_strategy(name: str, sig_df: pd.DataFrame, returns: pd.DataFrame,
                 basket: pd.Series) -> dict:
    pl_unh = portfolio_pl(sig_df, returns)
    is_mask = pl_unh.index <= IS_END
    pl_is = pl_unh[is_mask]
    basket_is = basket.loc[pl_is.index]
    beta = fit_beta(pl_is, basket_is)
    pl_h = hedge(pl_unh, basket, beta)
    pl_h_is = pl_h[pl_h.index <= IS_END]
    pl_h_oos = pl_h[pl_h.index >= OOS_START]
    m_is = metrics(pl_h_is)
    m_oos = metrics(pl_h_oos)
    m_full = metrics(pl_h)
    ci_lo_oos, ci_med_oos, ci_hi_oos = bootstrap_sharpe_ci(pl_h_oos, N_BOOT, SEED)
    rand_p5, rand_med, rand_p95 = random_baseline_p95(returns, N_RANDOM, SEED + hash(name) % 1000, sig_df)
    # Verdict
    beats_random = m_oos["sharpe"] > rand_p95
    sharpe_ok = m_oos["sharpe"] > 0.5
    ci_ok = ci_lo_oos > 0
    if beats_random and sharpe_ok and ci_ok:
        verdict = "ROBUST"
    elif m_oos["sharpe"] > 0:
        verdict = "WEAK"
    else:
        verdict = "DEAD"
    return {
        "name": name, "beta": beta,
        "is": m_is, "oos": m_oos, "full": m_full,
        "ci_oos": (ci_lo_oos, ci_med_oos, ci_hi_oos),
        "random_p5p95": (rand_p5, rand_med, rand_p95),
        "verdict": verdict,
        "pl_h_full": pl_h,
    }


# =====================================================================
# Main
# =====================================================================
def main() -> None:
    print("Loading + resampling 6 pairs to daily close...")
    closes = daily_closes()
    rets = build_returns(closes)
    basket = usd_basket(rets)
    print(f"  {len(rets)} aligned days, {rets.index[0].date()} → {rets.index[-1].date()}")

    print("\nGenerating signals for 8 strategies...")
    SPECS = {
        "TSM_21":      sig_tsm(rets, 21),
        "TSM_63":      sig_tsm(rets, 63),
        "TSM_126":     sig_tsm(rets, 126),
        "CSM_21":      sig_csm(rets, 21, top_n=2),
        "CSM_63":      sig_csm(rets, 63, top_n=2),
        "MR_1d":       sig_mr(rets, 1),
        "MR_5d":       sig_mr(rets, 5),
        "TSM_63_VOL":  sig_tsm_voltargeted(rets, 63),
    }

    results: dict = {}
    for name, sig_df in SPECS.items():
        print(f"  Running {name}...")
        r = run_strategy(name, sig_df, rets, basket)
        results[name] = r
        print(f"    {name}: IS Sh={r['is']['sharpe']:+.2f} | "
              f"OOS Sh={r['oos']['sharpe']:+.2f} | "
              f"rand p95={r['random_p5p95'][2]:+.2f} | "
              f"CI95=[{r['ci_oos'][0]:+.2f}, {r['ci_oos'][2]:+.2f}] | "
              f"verdict={r['verdict']}")

    # ----- Comparative table -----
    lines: list[str] = []
    def emit(s: str) -> None:
        print(s)
        lines.append(s)

    emit("# Strategy Comparator — 6 FX pairs daily, USD-basket hedge")
    emit("")
    emit(f"IS: 2019 → 2023 | OOS: 2024 → 2025 (incl. partial). Aligned days: {len(rets)}")
    emit("")
    emit("## COMPARATIVE (hedged Sharpe, vs random baseline 95p)")
    emit("| strategy   | IS Sh | OOS Sh | OOS ann_ret% | OOS maxDD% | "
         "rand p95 | bootstrap OOS CI95 | verdict |")
    emit("|---|---|---|---|---|---|---|---|")
    for name, r in results.items():
        emit(f"| {name:<10} | {r['is']['sharpe']:+.2f} | {r['oos']['sharpe']:+.2f} | "
             f"{r['oos']['ann_ret']*100:+.2f} | {r['oos']['max_dd']*100:+.2f} | "
             f"{r['random_p5p95'][2]:+.2f} | "
             f"[{r['ci_oos'][0]:+.2f}, {r['ci_oos'][2]:+.2f}] | {r['verdict']} |")
    emit("")

    # ----- Detail per strategy -----
    for name, r in results.items():
        emit(f"## {name} — full-sample details")
        emit(f"  hedge_beta (IS): {r['beta']:+.3f}")
        emit(f"  IS  hedged: Sharpe={r['is']['sharpe']:+.2f} ann_ret={r['is']['ann_ret']*100:+.2f}% "
             f"vol={r['is']['ann_vol']*100:.2f}% maxDD={r['is']['max_dd']*100:+.2f}% wr={r['is']['wr']:.1f}%")
        emit(f"  OOS hedged: Sharpe={r['oos']['sharpe']:+.2f} ann_ret={r['oos']['ann_ret']*100:+.2f}% "
             f"vol={r['oos']['ann_vol']*100:.2f}% maxDD={r['oos']['max_dd']*100:+.2f}% wr={r['oos']['wr']:.1f}%")
        emit(f"  FULL hedged: Sharpe={r['full']['sharpe']:+.2f} ann_ret={r['full']['ann_ret']*100:+.2f}% "
             f"calmar={r['full']['calmar']:.2f}")
        emit(f"  bootstrap OOS Sharpe CI95: [{r['ci_oos'][0]:+.2f}, {r['ci_oos'][2]:+.2f}] "
             f"median={r['ci_oos'][1]:+.2f}")
        emit(f"  random baseline OOS-shape: p5={r['random_p5p95'][0]:+.2f} "
             f"med={r['random_p5p95'][1]:+.2f} p95={r['random_p5p95'][2]:+.2f}")
        emit("")

    # ----- Winner -----
    robust = [n for n, r in results.items() if r["verdict"] == "ROBUST"]
    weak = [n for n, r in results.items() if r["verdict"] == "WEAK"]
    dead = [n for n, r in results.items() if r["verdict"] == "DEAD"]
    emit("## SUMMARY")
    emit(f"  ROBUST: {robust if robust else '(none)'}")
    emit(f"  WEAK  : {weak if weak else '(none)'}")
    emit(f"  DEAD  : {dead if dead else '(none)'}")
    emit("")

    if robust:
        winner = max(robust, key=lambda n: results[n]["oos"]["sharpe"])
        emit(f"  WINNER: **{winner}** (OOS Sharpe {results[winner]['oos']['sharpe']:+.2f})")
        next_step = (f"Advance {winner} : add costs (2bps spread + 0.5bps comm), "
                     f"re-validate. If Sharpe > 0.4 net of costs → paper-trade 30 days, "
                     f"then extend universe (G10 + EM FX).")
    elif weak:
        best_weak = max(weak, key=lambda n: results[n]["oos"]["sharpe"])
        emit(f"  Best WEAK: {best_weak} (OOS Sharpe {results[best_weak]['oos']['sharpe']:+.2f})")
        next_step = (f"No robust spec found. Try (a) gating multi-lookback agreement, "
                     f"(b) regime conditional (trade only when vol-of-vol low), "
                     f"(c) accept FX-daily-on-6-pairs not enough universe. Best candidate "
                     f"= {best_weak} (still weak).")
    else:
        emit("  ALL DEAD.")
        next_step = ("All 8 specs DEAD on 6 FX pairs. Universe too narrow. "
                     "Pivot: (a) expand universe (crypto majors, equities cross-section), "
                     "(b) try volatility risk premium (needs options data), "
                     "(c) accept no edge with current data.")
    emit(f"  next_step: {next_step}")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))

    # Persist metrics JSON
    persist = {}
    for name, r in results.items():
        persist[name] = {
            "beta": r["beta"], "verdict": r["verdict"],
            "is": r["is"], "oos": r["oos"], "full": r["full"],
            "ci_oos": list(r["ci_oos"]),
            "random_p5p95": list(r["random_p5p95"]),
        }
    OUT_METRICS.write_text(json.dumps(persist, indent=2, default=str))

    # Equity overlay (cum P&L)
    fig = go.Figure()
    for name, r in results.items():
        cum = r["pl_h_full"].cumsum()
        fig.add_trace(go.Scatter(x=cum.index, y=cum.values, mode="lines", name=name))
    fig.update_layout(title="Strategy comparator — hedged cumulative returns",
                      template="plotly_white", height=700, hovermode="x",
                      xaxis_title="date", yaxis_title="cum return")
    fig.write_html(str(OUT_EQUITY), include_plotlyjs="cdn")

    # ----- Deliverable -----
    print("\n\ndone")
    print(f"files: {OUT_REPORT.name}, {OUT_METRICS.name}, {OUT_EQUITY.name}")
    print("verdicts: " + ", ".join(f"{n}={r['verdict']}" for n, r in results.items()))
    if robust:
        winner = max(robust, key=lambda n: results[n]["oos"]["sharpe"])
        print(f"winner: {winner}  OOS_Sharpe={results[winner]['oos']['sharpe']:+.2f}")
    elif weak:
        best_weak = max(weak, key=lambda n: results[n]["oos"]["sharpe"])
        print(f"winner: NONE  best_weak={best_weak} OOS_Sharpe={results[best_weak]['oos']['sharpe']:+.2f}")
    else:
        print("winner: NONE  all_dead")


if __name__ == "__main__":
    main()
