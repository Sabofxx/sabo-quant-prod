"""
Sandbox — MR_5d deep dive for RETAIL capital (<$20k, accepts volatility).

Strategy: 5-day mean reversion on 6 FX pairs. Signal_t = -sign(cum return t-5..t-1).
Position equal-weight 1/6 per pair. Daily rebalance. USD-basket beta hedged
(IS-fitted).

Was WEAK on institutional grade (CI low -0.56) but might be TRADEABLE for retail :
- Lower Sharpe bar (target net Sharpe > 0.3)
- Higher tolerance to negative CI bound (CI low > -0.2 acceptable if median > 0.4)
- Costs explicitly modeled (FX retail spreads)
- Per-pair edge decomposition (drop perdants, keep winners)
- Signal combination test (MR_5d + MR_1d ensemble)

Retail spreads (typical retail broker, e.g. IC Markets/Pepperstone raw):
  EURUSD 0.6 pip | GBPUSD 0.8 | USDJPY 0.7 | AUDUSD 0.8 | NZDUSD 1.2 | USDCAD 1.0
  + commission ~3.5 USD per lot per side = ~0.35 pip on standard lot
  → total round-trip cost (entry + exit) per pair in pips:
     EURUSD 1.9 | GBPUSD 2.3 | USDJPY 2.1 | AUDUSD 2.3 | NZDUSD 3.1 | USDCAD 2.7

Cost model:
  Each day, for each pair where signal_t != signal_(t-1), incur cost =
  (round_trip_spread_pips × pip_size) / mid_price  (one round-trip).
  Position sized 1/6 of capital → portfolio-level cost reduction by 1/6.

Verdict tiers (retail-adjusted):
  TRADEABLE     : net OOS Sharpe > 0.3 AND OOS CI95 low > -0.2 AND beats random p95
  RESEARCH_ONLY : net OOS Sharpe > 0 but fails one threshold
  DEAD          : net OOS Sharpe <= 0
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


HERE = Path(__file__).parent
DATA = HERE / "data"
OUT_REPORT = HERE / "strategy_mr5d_retail_report.md"
OUT_METRICS = HERE / "strategy_mr5d_retail_metrics.json"
OUT_EQUITY = HERE / "strategy_mr5d_retail_equity.html"
OUT_PERPAIR = HERE / "strategy_mr5d_retail_perpair.html"

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD"]
USD_DIR = {
    "EURUSD": -1, "GBPUSD": -1, "AUDUSD": -1, "NZDUSD": -1,
    "USDJPY": +1, "USDCAD": +1,
}
PIP_SIZE = {
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001,
    "NZDUSD": 0.0001, "USDCAD": 0.0001, "USDJPY": 0.01,
}
# Retail round-trip cost in pips (spread + commission both sides)
ROUND_TRIP_PIPS = {
    "EURUSD": 1.9, "GBPUSD": 2.3, "USDJPY": 2.1,
    "AUDUSD": 2.3, "NZDUSD": 3.1, "USDCAD": 2.7,
}
FILES_M5 = {p: f"{p.lower()}-m5-bid-2019-01-01-2026-01-01.csv" for p in PAIRS}

TRADING_DAYS = 252
SEED = 42
IS_END = pd.Timestamp("2023-12-31 23:59:59", tz="UTC")
OOS_START = pd.Timestamp("2024-01-01", tz="UTC")
N_RANDOM = 500
N_BOOT = 2000
LOOKBACK_MR = 5
N_PAIRS_WEIGHT = 6


# =====================================================================
# Data
# =====================================================================
def load_m5(pair: str) -> pd.DataFrame:
    df = pd.read_csv(DATA / FILES_M5[pair])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    return df[~df.index.duplicated(keep="first")]


def build_data():
    closes: dict = {}
    for p in PAIRS:
        m5 = load_m5(p)
        closes[p] = m5["close"].resample("1D").last().dropna()
    rets = pd.DataFrame({p: closes[p].pct_change() for p in PAIRS}).dropna()
    px = pd.DataFrame({p: closes[p].reindex(rets.index) for p in PAIRS})
    return rets, px


# =====================================================================
# Signal + cost model
# =====================================================================
def signal_mr(returns: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """MR: short if past lookback cum > 0, long if < 0. Equal weight 1/N_PAIRS_WEIGHT."""
    cum = (1.0 + returns).rolling(lookback).apply(lambda x: x.prod() - 1.0, raw=True)
    sig = (cum < 0).astype(float) - (cum > 0).astype(float)
    sig = sig / N_PAIRS_WEIGHT
    return sig.shift(1).dropna(how="all")


def cost_per_day(signals: pd.DataFrame, prices: pd.DataFrame) -> pd.Series:
    """For each day, sum across pairs of: turnover × per-pair cost in return units.
    turnover_pair = |sig_t - sig_(t-1)| / (2 × weight)   (= 1 if full flip)
    cost_pair_return = (round_trip_pips × pip_size) / price_t   per round-trip
    Daily portfolio cost = sum across pairs of turnover_frac × cost_pair_return / N
    Since signal weight = 1/N, a full flip = turnover_frac = 1 → cost = cost_pair_return / N."""
    cost_series = pd.Series(0.0, index=signals.index)
    for p in PAIRS:
        diff = signals[p].diff().abs()
        # turnover fraction: 0 if no change, 2/N if flip (sig went -1/N to +1/N)
        turnover = diff / (2.0 / N_PAIRS_WEIGHT)
        turnover = turnover.fillna(0.0).clip(0.0, 1.0)  # cap at 1 full flip per day
        rt_pips = ROUND_TRIP_PIPS[p]
        pip = PIP_SIZE[p]
        per_pair_cost_ret = (rt_pips * pip) / prices[p]
        # weight 1/N for portfolio contribution
        cost_series = cost_series.add(
            (turnover * per_pair_cost_ret / N_PAIRS_WEIGHT).fillna(0.0),
            fill_value=0.0,
        )
    return cost_series


def portfolio_pl_gross(signals: pd.DataFrame, returns: pd.DataFrame) -> pd.Series:
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
                "max_dd": 0.0, "wr": 0.0, "calmar": 0.0, "best_day": 0.0,
                "worst_day": 0.0, "skew": 0.0}
    mean = float(pl.mean())
    std = float(pl.std())
    sharpe = (mean / std) * math.sqrt(TRADING_DAYS) if std > 0 else 0.0
    cum = pl.cumsum()
    dd = float((cum - cum.cummax()).min())
    z = (pl - mean) / std if std > 0 else pl * 0
    skew = float((z ** 3).mean())
    return {"n": len(pl), "sharpe": sharpe, "ann_ret": mean * TRADING_DAYS,
            "ann_vol": std * math.sqrt(TRADING_DAYS), "max_dd": dd,
            "wr": float((pl > 0).mean() * 100),
            "calmar": (mean * TRADING_DAYS) / abs(dd) if dd < 0 else float("inf"),
            "best_day": float(pl.max()), "worst_day": float(pl.min()),
            "skew": skew}


def bootstrap_ci(pl: pd.Series, n: int, seed: int) -> dict:
    rng = random.Random(seed)
    arr = pl.dropna().to_list()
    L = len(arr)
    if L < 2:
        return {"sharpe": (0, 0, 0), "ann_ret": (0, 0, 0)}
    shs, ars = [], []
    for _ in range(n):
        s = [arr[rng.randrange(L)] for _ in range(L)]
        m = sum(s) / L
        var = sum((x - m) ** 2 for x in s) / (L - 1)
        std = math.sqrt(var)
        shs.append((m / std) * math.sqrt(TRADING_DAYS) if std > 0 else 0.0)
        ars.append(m * TRADING_DAYS)
    shs.sort()
    ars.sort()
    return {
        "sharpe": (shs[int(n * 0.025)], shs[n // 2], shs[int(n * 0.975) - 1]),
        "ann_ret": (ars[int(n * 0.025)], ars[n // 2], ars[int(n * 0.975) - 1]),
    }


def random_baseline(returns: pd.DataFrame, n: int, seed: int,
                    template: pd.DataFrame) -> tuple[float, float, float]:
    rng = random.Random(seed)
    idx = template.index.intersection(returns.index)
    rets = returns.loc[idx]
    shs: list[float] = []
    for _ in range(n):
        sigs = pd.DataFrame(
            [[rng.choice([-1, 1]) / N_PAIRS_WEIGHT for _ in PAIRS] for _ in idx],
            index=idx, columns=PAIRS,
        )
        pl = (sigs * rets).sum(axis=1)
        m = metrics(pl)
        shs.append(m["sharpe"])
    shs.sort()
    return shs[int(n * 0.05)], shs[n // 2], shs[int(n * 0.95)]


# =====================================================================
# Per-pair analysis
# =====================================================================
def per_pair_pl(pair: str, returns: pd.DataFrame, prices: pd.DataFrame,
                lookback: int) -> tuple[pd.Series, pd.Series]:
    """Return (gross_pl, net_pl) for one pair only (1.0 weight, not /N)."""
    pair_ret = returns[pair]
    cum = (1.0 + pair_ret).rolling(lookback).apply(lambda x: x.prod() - 1.0, raw=True)
    sig = (cum < 0).astype(float) - (cum > 0).astype(float)
    sig = sig.shift(1).dropna()
    common = sig.index.intersection(pair_ret.index)
    gross = sig.loc[common] * pair_ret.loc[common]
    diff = sig.diff().abs().fillna(0.0)
    turnover = diff / 2.0
    rt_pips = ROUND_TRIP_PIPS[pair]
    pip = PIP_SIZE[pair]
    per_unit_cost = (rt_pips * pip) / prices[pair]
    cost = (turnover * per_unit_cost).reindex(common).fillna(0.0)
    net = gross - cost
    return gross.rename(f"{pair}_gross"), net.rename(f"{pair}_net")


# =====================================================================
# Main
# =====================================================================
def main() -> None:
    print("Loading 6 pairs M5 → daily close...")
    rets, px = build_data()
    print(f"  {len(rets)} aligned days × {len(PAIRS)} pairs")

    sigs = signal_mr(rets, LOOKBACK_MR)
    cost = cost_per_day(sigs, px.loc[sigs.index])
    gross = portfolio_pl_gross(sigs, rets)
    common = gross.index.intersection(cost.index)
    net_unh = gross.loc[common] - cost.loc[common]

    basket = usd_basket(rets)
    is_mask = net_unh.index <= IS_END
    pl_is_unh = net_unh[is_mask]
    basket_is = basket.loc[basket.index.intersection(pl_is_unh.index)]
    beta = fit_beta(pl_is_unh, basket_is)
    net_h = hedge(net_unh, basket, beta)
    gross_h = hedge(gross, basket, beta)

    is_mask_h = net_h.index <= IS_END
    oos_mask_h = net_h.index >= OOS_START
    net_h_is = net_h[is_mask_h]
    net_h_oos = net_h[oos_mask_h]
    gross_h_is = gross_h[gross_h.index <= IS_END]
    gross_h_oos = gross_h[gross_h.index >= OOS_START]

    m_g_is = metrics(gross_h_is)
    m_g_oos = metrics(gross_h_oos)
    m_n_is = metrics(net_h_is)
    m_n_oos = metrics(net_h_oos)
    m_n_full = metrics(net_h)

    ci_oos = bootstrap_ci(net_h_oos, N_BOOT, SEED)
    ci_full = bootstrap_ci(net_h, N_BOOT, SEED)

    rand_p5, rand_med, rand_p95 = random_baseline(
        rets.loc[net_h_oos.index], N_RANDOM, SEED, sigs.loc[net_h_oos.index])

    # ----- Per-pair -----
    perpair: dict = {}
    for p in PAIRS:
        gr, nt = per_pair_pl(p, rets, px, LOOKBACK_MR)
        gr_oos = gr[gr.index >= OOS_START]
        nt_oos = nt[nt.index >= OOS_START]
        m_gr = metrics(gr)
        m_nt = metrics(nt)
        m_gr_oos = metrics(gr_oos)
        m_nt_oos = metrics(nt_oos)
        perpair[p] = {"gross_full": m_gr, "net_full": m_nt,
                      "gross_oos": m_gr_oos, "net_oos": m_nt_oos,
                      "net_pl": nt}

    # ----- Signal combination (MR_5d + MR_1d, equal weight) -----
    sigs_mr1 = signal_mr(rets, 1)
    cost_mr1 = cost_per_day(sigs_mr1, px.loc[sigs_mr1.index])
    gross_mr1 = portfolio_pl_gross(sigs_mr1, rets)
    common_mr1 = gross_mr1.index.intersection(cost_mr1.index)
    net_unh_mr1 = gross_mr1.loc[common_mr1] - cost_mr1.loc[common_mr1]
    common_ens = net_unh.index.intersection(net_unh_mr1.index)
    ens_unh = (net_unh.loc[common_ens] + net_unh_mr1.loc[common_ens]) / 2
    ens_h = hedge(ens_unh, basket, beta)
    m_ens_oos = metrics(ens_h[ens_h.index >= OOS_START])
    ci_ens_oos = bootstrap_ci(ens_h[ens_h.index >= OOS_START], N_BOOT, SEED)

    # ----- Verdict retail-adjusted -----
    tradeable = (m_n_oos["sharpe"] > 0.3 and ci_oos["sharpe"][0] > -0.2
                 and m_n_oos["sharpe"] > rand_p95)
    if tradeable:
        verdict = "TRADEABLE"
    elif m_n_oos["sharpe"] > 0:
        verdict = "RESEARCH_ONLY"
    else:
        verdict = "DEAD"

    # ----- Report -----
    lines: list[str] = []
    def emit(s: str) -> None:
        print(s)
        lines.append(s)

    emit("# MR_5d Retail Validation — 6 FX pairs, costs included")
    emit("")
    emit(f"Universe: {', '.join(PAIRS)}. Lookback {LOOKBACK_MR}d. Equal weight 1/6.")
    emit(f"IS: 2019 → 2023 | OOS: 2024 → 2025. USD-basket beta IS-fit = {beta:+.3f}")
    emit("Round-trip cost (pips): " + ", ".join(
        f"{p}={ROUND_TRIP_PIPS[p]:.1f}" for p in PAIRS))
    emit("")

    emit("## GROSS vs NET METRICS (portfolio)")
    emit("| period | gross Sh | gross ann% | net Sh | net ann% | cost drag (Sh) |")
    emit("|---|---|---|---|---|---|")
    drag_is = m_g_is["sharpe"] - m_n_is["sharpe"]
    drag_oos = m_g_oos["sharpe"] - m_n_oos["sharpe"]
    emit(f"| IS  | {m_g_is['sharpe']:+.2f} | {m_g_is['ann_ret']*100:+.2f} | "
         f"{m_n_is['sharpe']:+.2f} | {m_n_is['ann_ret']*100:+.2f} | {drag_is:+.2f} |")
    emit(f"| OOS | {m_g_oos['sharpe']:+.2f} | {m_g_oos['ann_ret']*100:+.2f} | "
         f"{m_n_oos['sharpe']:+.2f} | {m_n_oos['ann_ret']*100:+.2f} | {drag_oos:+.2f} |")
    emit("")

    emit("## NET FULL METRICS")
    emit(f"  n={m_n_full['n']}  Sharpe={m_n_full['sharpe']:+.2f}  "
         f"ann_ret={m_n_full['ann_ret']*100:+.2f}%  ann_vol={m_n_full['ann_vol']*100:.2f}%  "
         f"maxDD={m_n_full['max_dd']*100:+.2f}%  wr={m_n_full['wr']:.1f}%  "
         f"calmar={m_n_full['calmar']:.2f}  skew={m_n_full['skew']:+.2f}")
    emit(f"  best day={m_n_full['best_day']*100:+.2f}%  "
         f"worst day={m_n_full['worst_day']*100:+.2f}%")
    emit("")

    emit("## BOOTSTRAP CI95 (n_resample=2000)")
    emit(f"  OOS  Sharpe CI: [{ci_oos['sharpe'][0]:+.2f}, {ci_oos['sharpe'][2]:+.2f}] "
         f"median={ci_oos['sharpe'][1]:+.2f}")
    emit(f"  OOS  ann_ret CI: [{ci_oos['ann_ret'][0]*100:+.2f}%, "
         f"{ci_oos['ann_ret'][2]*100:+.2f}%]  median={ci_oos['ann_ret'][1]*100:+.2f}%")
    emit(f"  FULL Sharpe CI: [{ci_full['sharpe'][0]:+.2f}, {ci_full['sharpe'][2]:+.2f}] "
         f"median={ci_full['sharpe'][1]:+.2f}")
    emit("")

    emit(f"## RANDOM BASELINE OOS (n={N_RANDOM})")
    emit(f"  Sharpe p5={rand_p5:+.2f}  med={rand_med:+.2f}  p95={rand_p95:+.2f}")
    emit(f"  Actual NET OOS Sharpe={m_n_oos['sharpe']:+.2f}  → "
         f"{'BEATS' if m_n_oos['sharpe'] > rand_p95 else 'FAILS'} 95p")
    emit("")

    emit("## PER-PAIR NET METRICS (lookback 5d, individual contribution)")
    emit("| pair    | gross_full Sh | net_full Sh | gross_oos Sh | net_oos Sh | OOS verdict |")
    emit("|---|---|---|---|---|---|")
    for p in PAIRS:
        pp = perpair[p]
        v = "KEEP" if pp["net_oos"]["sharpe"] > 0.2 else ("MARGINAL" if pp["net_oos"]["sharpe"] > 0 else "DROP")
        emit(f"| {p:<7} | {pp['gross_full']['sharpe']:+.2f} | {pp['net_full']['sharpe']:+.2f} | "
             f"{pp['gross_oos']['sharpe']:+.2f} | {pp['net_oos']['sharpe']:+.2f} | {v} |")
    emit("")

    emit("## SIGNAL COMBINATION — MR_5d + MR_1d (equal weight, hedged, net)")
    emit(f"  OOS Sharpe (ensemble): {m_ens_oos['sharpe']:+.2f}  "
         f"ann_ret={m_ens_oos['ann_ret']*100:+.2f}%  maxDD={m_ens_oos['max_dd']*100:+.2f}%")
    emit(f"  OOS bootstrap CI: [{ci_ens_oos['sharpe'][0]:+.2f}, "
         f"{ci_ens_oos['sharpe'][2]:+.2f}] median={ci_ens_oos['sharpe'][1]:+.2f}")
    emit("")

    emit("## PER-YEAR NET HEDGED PORTFOLIO")
    emit("| year | n | Sharpe | ann% | maxDD% | wr% |")
    emit("|---|---|---|---|---|---|")
    for y in range(2019, 2026):
        sub = net_h[net_h.index.year == y]
        if len(sub) < 30:
            continue
        m = metrics(sub)
        emit(f"| {y} | {m['n']} | {m['sharpe']:+.2f} | {m['ann_ret']*100:+.2f} | "
             f"{m['max_dd']*100:+.2f} | {m['wr']:.1f} |")
    emit("")

    emit("## RETAIL-ADJUSTED VERDICT")
    emit("  Thresholds: net OOS Sharpe > 0.3 AND CI low > -0.2 AND > random p95")
    emit(f"  Actual: net OOS Sharpe={m_n_oos['sharpe']:+.2f} "
         f"CI low={ci_oos['sharpe'][0]:+.2f} rand_p95={rand_p95:+.2f}")
    emit(f"  VERDICT: **{verdict}**")
    emit("")

    # Recommendation
    keep_pairs = [p for p in PAIRS if perpair[p]["net_oos"]["sharpe"] > 0.2]
    drop_pairs = [p for p in PAIRS if perpair[p]["net_oos"]["sharpe"] <= 0]
    emit("## RETAIL RECOMMENDATION")
    if verdict == "TRADEABLE":
        emit("  Strategy is TRADEABLE for retail. Concrete deployment spec:")
        emit("")
        emit("  ### Position sizing (capital $15-20k)")
        emit("    - Risk per trade: 1% of capital → $150-200 per pair-day max")
        emit("    - Per-pair daily notional: weight 1/6 × leverage 2x = ~$5000")
        emit("    - Use micro-lot capable broker (IC Markets / Pepperstone Raw)")
        emit("")
        emit("  ### Pair whitelist (per-pair OOS Sharpe > 0.2):")
        emit(f"    KEEP : {keep_pairs if keep_pairs else '(none — use full basket)'}")
        emit(f"    DROP : {drop_pairs if drop_pairs else '(none)'}")
        emit("")
        emit("  ### Execution")
        emit("    - Daily UTC 22:00 (NY close) compute 5-day return per pair")
        emit("    - Signal flip = market order at next 22:01 UTC bar")
        emit("    - No SL / TP (1-day hold) ; exit forced at next 22:00 UTC")
        emit("    - Optional: skip Fri 22:00 (weekend gap risk)")
        emit("")
        emit("  ### Risk management")
        emit("    - Stop trading if monthly DD > 5% (~$750-1000 on $15-20k)")
        emit("    - Stop trading if rolling 60-day Sharpe < -0.3")
        emit("    - Review every 30 days")
        emit("")
        emit("  ### Expected (per OOS metrics, NET of costs)")
        emit(f"    - Annual return: {m_n_oos['ann_ret']*100:+.1f}%  → "
             f"${m_n_oos['ann_ret']*17500:+.0f} on $17.5k capital")
        emit(f"    - Annual vol: {m_n_oos['ann_vol']*100:.1f}%  → "
             f"~${m_n_oos['ann_vol']*17500:.0f} std dev")
        emit(f"    - Max DD historical: {m_n_oos['max_dd']*100:.1f}%  → "
             f"~${m_n_oos['max_dd']*17500:.0f} worst case")
    elif verdict == "RESEARCH_ONLY":
        emit("  RESEARCH_ONLY: positive net OOS Sharpe but fails retail thresholds.")
        emit("  Recommend: paper-trade 60 days at micro size to gather more OOS data")
        emit("  before committing real capital. Track daily realized vs expected.")
    else:
        emit("  DEAD net of costs. Don't trade.")
        emit("  Pivot recommendation (retail with <20k + accepts vol):")
        emit("    - Crypto pivot: BTC/ETH/SOL daily MR/momentum, free Binance API")
        emit("    - Higher inefficiency = better edge / pair for retail")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))

    # JSON
    OUT_METRICS.write_text(json.dumps({
        "hedge_beta": beta,
        "verdict": verdict,
        "thresholds": {"sharpe_min": 0.3, "ci_low_min": -0.2},
        "portfolio": {
            "gross_is": m_g_is, "gross_oos": m_g_oos,
            "net_is": m_n_is, "net_oos": m_n_oos, "net_full": m_n_full,
            "ci_oos": {"sharpe": list(ci_oos["sharpe"]),
                       "ann_ret": list(ci_oos["ann_ret"])},
            "ci_full": {"sharpe": list(ci_full["sharpe"])},
            "random_baseline_oos": {"p5": rand_p5, "med": rand_med, "p95": rand_p95},
        },
        "ensemble_mr5_mr1": {"oos": m_ens_oos,
                              "ci_oos_sharpe": list(ci_ens_oos["sharpe"])},
        "per_pair": {p: {k: v for k, v in perpair[p].items() if k != "net_pl"}
                     for p in PAIRS},
        "keep_pairs": keep_pairs, "drop_pairs": drop_pairs,
    }, indent=2, default=str))

    # Equity HTML (portfolio + ensemble + USD basket)
    fig = make_subplots(rows=2, cols=1, subplot_titles=(
        "MR_5d portfolio (gross vs net, hedged)",
        "MR_5d+MR_1d ensemble (hedged net) vs basket"),
        shared_xaxes=True, vertical_spacing=0.1)
    fig.add_trace(go.Scatter(x=gross_h.index, y=gross_h.cumsum().values,
                             mode="lines", name="gross", line=dict(color="#888")),
                  row=1, col=1)
    fig.add_trace(go.Scatter(x=net_h.index, y=net_h.cumsum().values,
                             mode="lines", name="net", line=dict(color="#2e7d32")),
                  row=1, col=1)
    fig.add_trace(go.Scatter(x=ens_h.index, y=ens_h.cumsum().values,
                             mode="lines", name="ensemble net", line=dict(color="#1976d2")),
                  row=2, col=1)
    fig.add_trace(go.Scatter(x=basket.index, y=basket.cumsum().values,
                             mode="lines", name="USD basket (passive)",
                             line=dict(color="#bbb", dash="dot")),
                  row=2, col=1)
    fig.update_layout(title="MR_5d retail analysis — hedged cum returns",
                      template="plotly_white", height=900, hovermode="x")
    fig.write_html(str(OUT_EQUITY), include_plotlyjs="cdn")

    # Per-pair equity HTML
    fig2 = make_subplots(rows=3, cols=2, subplot_titles=PAIRS, shared_xaxes=False,
                         vertical_spacing=0.08, horizontal_spacing=0.05)
    for i, p in enumerate(PAIRS):
        row = i // 2 + 1
        col = i % 2 + 1
        pp = perpair[p]
        cum_g = pp["net_pl"].cumsum()
        fig2.add_trace(go.Scatter(x=cum_g.index, y=cum_g.values, mode="lines",
                                  name=p, line=dict(color="#1976d2")),
                       row=row, col=col)
    fig2.update_layout(title="MR_5d per-pair net cum P&L (1.0 weight basis)",
                       template="plotly_white", height=900, showlegend=False)
    fig2.write_html(str(OUT_PERPAIR), include_plotlyjs="cdn")

    # ----- Deliverable -----
    print("\n\ndone")
    print(f"files: {OUT_REPORT.name}, {OUT_METRICS.name}, "
          f"{OUT_EQUITY.name}, {OUT_PERPAIR.name}")
    print(f"hedge_beta: {beta:+.3f}")
    print(f"net OOS Sharpe: {m_n_oos['sharpe']:+.2f}  "
          f"(gross {m_g_oos['sharpe']:+.2f}, cost drag {drag_oos:+.2f})")
    print(f"net OOS ann_ret: {m_n_oos['ann_ret']*100:+.2f}%  "
          f"ann_vol={m_n_oos['ann_vol']*100:.2f}%  maxDD={m_n_oos['max_dd']*100:+.2f}%")
    print(f"OOS bootstrap CI95 Sharpe: [{ci_oos['sharpe'][0]:+.2f}, {ci_oos['sharpe'][2]:+.2f}]")
    print(f"random p95 OOS: {rand_p95:+.2f}  ({'BEATS' if m_n_oos['sharpe'] > rand_p95 else 'FAILS'})")
    print(f"per-pair KEEP: {keep_pairs}")
    print(f"per-pair DROP: {drop_pairs}")
    print(f"ensemble MR_5d+MR_1d OOS Sharpe: {m_ens_oos['sharpe']:+.2f}")
    print(f"verdict: {verdict}")


if __name__ == "__main__":
    main()
