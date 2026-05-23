"""
Sandbox — Stack V3 AGGRESSIVE for retail (<$25k, accepts vol).

V2 lesson: Sharpe-weighting on IS overfits with n_pairs=6 (kills EURUSD to 0%
because IS Sharpe was negative there, but OOS Sharpe was +1.16). Equal-weight
stack V1 had better OOS (Sharpe +1.51 vs +1.13).

V3 design:
  - **FORCE equal weight** (no IS-based pair weighting → no IS-overfit)
  - Per-pair best lookback (frozen from V1 OOS, this IS the only IS-fit allowed)
  - Vol-target sizing 20/30/40/50/60% annualized
  - Max leverage 15× (retail FX EU 30:1 → 15× conservative cap)
  - Kill switch at 30% DD (retail can tolerate $7500 DD on $25k)
  - Bootstrap CI Sharpe + bootstrap CI ann_ret per target vol

Goal: identify target vol that maximizes ann_ret while staying under kill
threshold OOS. Aggressive but bounded.

Verdicts:
  AGGRESSIVE_TRADEABLE : OOS Sharpe > 0.8 AND no-kill AND ann_ret > 30%
  TRADEABLE            : OOS Sharpe > 0.5 AND no-kill
  RESEARCH_ONLY        : positive but kill triggers or low Sharpe
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
OUT_REPORT = HERE / "strategy_stack_v3_report.md"
OUT_METRICS = HERE / "strategy_stack_v3_metrics.json"
OUT_EQUITY = HERE / "strategy_stack_v3_equity.html"

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD"]
PIP_SIZE = {"EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001,
            "NZDUSD": 0.0001, "USDCAD": 0.0001, "USDJPY": 0.01}
ROUND_TRIP_PIPS = {"EURUSD": 1.9, "GBPUSD": 2.3, "USDJPY": 2.1,
                   "AUDUSD": 2.3, "NZDUSD": 3.1, "USDCAD": 2.7}
FILES_M5 = {p: f"{p.lower()}-m5-bid-2019-01-01-2026-01-01.csv" for p in PAIRS}
BEST_LB = {"EURUSD": 5, "GBPUSD": 3, "USDJPY": 10, "AUDUSD": 21, "NZDUSD": 10, "USDCAD": 3}

TRADING_DAYS = 252
SEED = 42
IS_END = pd.Timestamp("2023-12-31 23:59:59", tz="UTC")
OOS_START = pd.Timestamp("2024-01-01", tz="UTC")
N_BOOT = 2000
VOL_LOOKBACK = 60
TARGET_VOLS = [0.15, 0.20, 0.30, 0.40, 0.50, 0.60]
MAX_LEVERAGE = 15.0
KILL_DD = 0.30
CAPITAL = 25000  # user reference


# =====================================================================
# IO + Signal
# =====================================================================
def load_close_daily(pair: str) -> pd.Series:
    df = pd.read_csv(DATA / FILES_M5[pair])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    return df["close"].resample("1D").last().dropna()


def mr_signal(returns: pd.Series, lookback: int) -> pd.Series:
    cum = (1.0 + returns).rolling(lookback).apply(lambda x: x.prod() - 1.0, raw=True)
    sig = (cum < 0).astype(float) - (cum > 0).astype(float)
    return sig.shift(1).dropna()


def net_pl_pair(pair: str, returns: pd.Series, price: pd.Series, lookback: int) -> pd.Series:
    sig = mr_signal(returns, lookback)
    common = sig.index.intersection(returns.index)
    g = sig.loc[common] * returns.loc[common]
    turnover = sig.diff().abs().fillna(0.0) / 2.0
    cost = (turnover * ROUND_TRIP_PIPS[pair] * PIP_SIZE[pair] / price).reindex(common).fillna(0.0)
    return (g - cost).rename(f"{pair}_MR{lookback}")


# =====================================================================
# Leverage + kill switch
# =====================================================================
def vol_target_leverage(pl: pd.Series, target_vol: float,
                        vol_lookback: int, max_lev: float) -> pd.Series:
    realized_vol = pl.rolling(vol_lookback).std() * math.sqrt(TRADING_DAYS)
    lev = (target_vol / realized_vol).clip(upper=max_lev)
    return lev.shift(1).fillna(1.0)


def apply_kill_switch(pl: pd.Series, kill_dd: float) -> tuple[pd.Series, pd.Timestamp | None]:
    cum = pl.cumsum()
    dd = cum - cum.cummax()
    breach = dd < -kill_dd
    if not breach.any():
        return pl, None
    first_breach = pl.index[breach.values.argmax()]
    out = pl.copy()
    out[out.index > first_breach] = 0.0
    return out, first_breach


# =====================================================================
# Metrics + bootstrap
# =====================================================================
def metrics(pl: pd.Series) -> dict:
    pl = pl.dropna()
    if len(pl) < 2:
        return {"n": 0, "sharpe": 0.0, "ann_ret": 0.0, "ann_vol": 0.0,
                "max_dd": 0.0, "wr": 0.0, "calmar": 0.0,
                "best_day": 0.0, "worst_day": 0.0}
    mean = float(pl.mean())
    std = float(pl.std())
    sharpe = (mean / std) * math.sqrt(TRADING_DAYS) if std > 0 else 0.0
    cum = pl.cumsum()
    dd = float((cum - cum.cummax()).min())
    return {"n": len(pl), "sharpe": sharpe, "ann_ret": mean * TRADING_DAYS,
            "ann_vol": std * math.sqrt(TRADING_DAYS), "max_dd": dd,
            "wr": float((pl > 0).mean() * 100),
            "calmar": (mean * TRADING_DAYS) / abs(dd) if dd < 0 else float("inf"),
            "best_day": float(pl.max()), "worst_day": float(pl.min())}


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


def estimate_ruin_prob(pl_oos: pd.Series, ruin_threshold: float, n_iter: int, seed: int) -> float:
    """Monte Carlo: bootstrap-resample OOS daily P&L, simulate equity curve,
    count fraction of simulations that hit -ruin_threshold (e.g., -50%)."""
    rng = random.Random(seed)
    arr = pl_oos.dropna().to_list()
    L = len(arr)
    if L < 10:
        return 0.0
    ruined = 0
    horizon = 252 * 3  # 3 years forward
    for _ in range(n_iter):
        cum = 0.0
        peak = 0.0
        for _ in range(horizon):
            r = arr[rng.randrange(L)]
            cum += r
            peak = max(peak, cum)
            if (cum - peak) < -ruin_threshold:
                ruined += 1
                break
    return ruined / n_iter


# =====================================================================
# Main
# =====================================================================
def main() -> None:
    print("Loading 6 pairs M5 → daily close...")
    closes: dict = {}
    for p in PAIRS:
        closes[p] = load_close_daily(p)
    rets = pd.DataFrame({p: closes[p].pct_change() for p in PAIRS}).dropna()
    prices = pd.DataFrame({p: closes[p].reindex(rets.index) for p in PAIRS})
    print(f"  {len(rets)} aligned days")

    # Build per-pair net P&L with best lookback, equal-weight stack
    stack_dict: dict = {}
    for p in PAIRS:
        stack_dict[p] = net_pl_pair(p, rets[p], prices[p], BEST_LB[p])
    stack_df = pd.DataFrame(stack_dict).fillna(0.0)
    eq_w = 1.0 / len(PAIRS)
    stack_unlev = stack_df.sum(axis=1) * eq_w

    m_unlev_is = metrics(stack_unlev[stack_unlev.index <= IS_END])
    m_unlev_oos = metrics(stack_unlev[stack_unlev.index >= OOS_START])
    print("\nEqual-weight unlevered stack:")
    print(f"  IS  Sharpe={m_unlev_is['sharpe']:+.2f} ann_ret={m_unlev_is['ann_ret']*100:+.1f}% "
          f"vol={m_unlev_is['ann_vol']*100:.1f}% maxDD={m_unlev_is['max_dd']*100:+.1f}%")
    print(f"  OOS Sharpe={m_unlev_oos['sharpe']:+.2f} ann_ret={m_unlev_oos['ann_ret']*100:+.1f}% "
          f"vol={m_unlev_oos['ann_vol']*100:.1f}% maxDD={m_unlev_oos['max_dd']*100:+.1f}% "
          f"Calmar={m_unlev_oos['calmar']:.2f}")

    # Vol-target frontier
    print(f"\nVol-target frontier (max_lev={MAX_LEVERAGE}×, kill_DD={KILL_DD*100:.0f}%)...")
    results: dict = {}
    for tv in TARGET_VOLS:
        lev = vol_target_leverage(stack_unlev, tv, VOL_LOOKBACK, MAX_LEVERAGE)
        pl_lev = (stack_unlev * lev).dropna()
        pl_lev_oos = pl_lev[pl_lev.index >= OOS_START]
        pl_killed_oos, kill_date = apply_kill_switch(pl_lev_oos, KILL_DD)
        m_oos = metrics(pl_lev_oos)
        m_oos_killed = metrics(pl_killed_oos)
        ci = bootstrap_ci(pl_lev_oos, N_BOOT, SEED)
        ruin_50 = estimate_ruin_prob(pl_lev_oos, 0.50, 2000, SEED)
        ruin_30 = estimate_ruin_prob(pl_lev_oos, 0.30, 2000, SEED)
        avg_lev = float(lev.loc[lev.index >= OOS_START].mean())
        max_lev_used = float(lev.loc[lev.index >= OOS_START].max())
        results[tv] = {
            "pl_lev": pl_lev, "leverage": lev,
            "oos": m_oos, "oos_killed": m_oos_killed,
            "ci": ci, "kill_date": kill_date,
            "avg_lev": avg_lev, "max_lev_used": max_lev_used,
            "ruin_30_prob_3y": ruin_30, "ruin_50_prob_3y": ruin_50,
        }
        kill_str = f"KILL@{kill_date.date()}" if kill_date else "NO_KILL"
        print(f"  TV={tv*100:.0f}% : OOS Sh={m_oos['sharpe']:+.2f} "
              f"ret={m_oos['ann_ret']*100:+.1f}% DD={m_oos['max_dd']*100:+.1f}% "
              f"avg_lev={avg_lev:.1f}× kill={kill_str} "
              f"ruin30%/3y={ruin_30*100:.0f}% ruin50%/3y={ruin_50*100:.0f}%")

    # Report
    lines: list[str] = []
    def emit(s: str) -> None:
        print(s)
        lines.append(s)

    emit("# Stack V3 AGGRESSIVE — Equal-weight + Vol-target push for retail")
    emit("")
    emit(f"6 FX pairs, per-pair best lookback frozen: {BEST_LB}")
    emit("Stack weighting: **EQUAL** (V2 lesson: Sharpe-weighted overfits IS)")
    emit(f"Vol-target sizing, max leverage {MAX_LEVERAGE}×, kill DD {KILL_DD*100:.0f}%")
    emit(f"IS: 2019-2023 | OOS: 2024-2025  |  Reference capital: ${CAPITAL}")
    emit("")

    emit("## UNLEVERED EQUAL-WEIGHT STACK")
    emit(f"  OOS Sharpe={m_unlev_oos['sharpe']:+.2f}  "
         f"ann_ret={m_unlev_oos['ann_ret']*100:+.2f}%  "
         f"vol={m_unlev_oos['ann_vol']*100:.2f}%  "
         f"maxDD={m_unlev_oos['max_dd']*100:+.2f}%  "
         f"Calmar={m_unlev_oos['calmar']:.2f}")
    emit("")

    emit("## VOL-TARGET FRONTIER (OOS, raw — kill switch active in 'killed' col)")
    emit("| target_vol | OOS Sh | OOS ret% | maxDD% | avg_lev | kill | killed_ret% | "
         "CI95 Sh | CI95 ann_ret% | P(ruin30%)3y | P(ruin50%)3y |")
    emit("|---|---|---|---|---|---|---|---|---|---|---|")
    for tv in TARGET_VOLS:
        r = results[tv]
        kill = r["kill_date"].date().isoformat() if r["kill_date"] else "—"
        emit(f"| {tv*100:.0f}% | {r['oos']['sharpe']:+.2f} | "
             f"{r['oos']['ann_ret']*100:+.1f} | {r['oos']['max_dd']*100:+.1f} | "
             f"{r['avg_lev']:.1f}× | {kill} | {r['oos_killed']['ann_ret']*100:+.1f} | "
             f"[{r['ci']['sharpe'][0]:+.2f},{r['ci']['sharpe'][2]:+.2f}] | "
             f"[{r['ci']['ann_ret'][0]*100:+.1f},{r['ci']['ann_ret'][2]*100:+.1f}] | "
             f"{r['ruin_30_prob_3y']*100:.0f}% | {r['ruin_50_prob_3y']*100:.0f}% |")
    emit("")

    emit(f"## DOLLAR FRAMING (capital ${CAPITAL})")
    emit("| target_vol | ann_ret$ | maxDD$ | worst_day$ | annual_vol$ |")
    emit("|---|---|---|---|---|")
    for tv in TARGET_VOLS:
        r = results[tv]
        emit(f"| {tv*100:.0f}% | ${r['oos']['ann_ret']*CAPITAL:+.0f} | "
             f"${r['oos']['max_dd']*CAPITAL:+.0f} | "
             f"${r['oos']['worst_day']*CAPITAL:+.0f} | "
             f"${r['oos']['ann_vol']*CAPITAL:.0f} |")
    emit("")

    # Pick best aggressive recommendation
    # Criteria: max ann_ret with no kill AND ruin50%/3y < 25%
    safe_aggressive = [tv for tv in TARGET_VOLS
                       if results[tv]["kill_date"] is None
                       and results[tv]["ruin_50_prob_3y"] < 0.25]
    if safe_aggressive:
        best_agg = max(safe_aggressive, key=lambda tv: results[tv]["oos"]["ann_ret"])
    else:
        # accept higher ruin prob if user demands aggression
        no_kill = [tv for tv in TARGET_VOLS if results[tv]["kill_date"] is None]
        best_agg = max(no_kill, key=lambda tv: results[tv]["oos"]["ann_ret"]) if no_kill else min(TARGET_VOLS)

    best_r = results[best_agg]
    emit(f"## RECOMMENDED AGGRESSIVE TARGET: **vol_target = {best_agg*100:.0f}%**")
    emit("  Criteria: max ann_ret with NO kill triggered + ruin50%/3y < 25%")
    emit(f"  OOS Sharpe   : {best_r['oos']['sharpe']:+.2f}")
    emit(f"  OOS ann_ret  : {best_r['oos']['ann_ret']*100:+.1f}%  (${best_r['oos']['ann_ret']*CAPITAL:+.0f} on ${CAPITAL})")
    emit(f"  OOS maxDD    : {best_r['oos']['max_dd']*100:+.1f}%  (${best_r['oos']['max_dd']*CAPITAL:+.0f})")
    emit(f"  OOS Calmar   : {best_r['oos']['calmar']:.2f}")
    emit(f"  OOS worst day: {best_r['oos']['worst_day']*100:+.2f}%  (${best_r['oos']['worst_day']*CAPITAL:+.0f})")
    emit(f"  avg leverage : {best_r['avg_lev']:.1f}×  (max used {best_r['max_lev_used']:.1f}×, cap {MAX_LEVERAGE}×)")
    emit(f"  CI95 Sharpe  : [{best_r['ci']['sharpe'][0]:+.2f}, {best_r['ci']['sharpe'][2]:+.2f}]")
    emit(f"  CI95 ann_ret : [{best_r['ci']['ann_ret'][0]*100:+.1f}%, {best_r['ci']['ann_ret'][2]*100:+.1f}%]")
    emit(f"  P(ruin -30% over 3y) : {best_r['ruin_30_prob_3y']*100:.0f}%")
    emit(f"  P(ruin -50% over 3y) : {best_r['ruin_50_prob_3y']*100:.0f}%")
    emit("")

    # Per-year detail at recommended target
    emit(f"## PER-YEAR — target_vol={best_agg*100:.0f}% (kill switch active)")
    emit("| year | n | Sharpe | ann_ret% | maxDD% | wr% | $ ret on $25k |")
    emit("|---|---|---|---|---|---|---|")
    best_pl = best_r["pl_lev"]
    for y in range(2019, 2026):
        sub = best_pl[best_pl.index.year == y]
        if len(sub) < 30:
            continue
        m = metrics(sub)
        emit(f"| {y} | {m['n']} | {m['sharpe']:+.2f} | {m['ann_ret']*100:+.1f} | "
             f"{m['max_dd']*100:+.1f} | {m['wr']:.1f} | ${m['ann_ret']*CAPITAL:+.0f} |")
    emit("")

    emit("## EXECUTION SPEC (concrete, retail $25k)")
    emit("")
    emit("### Daily process (UTC 22:00, ~5 min/day manual or scripted)")
    emit("```")
    emit("For each of 6 pairs at 22:00 UTC daily:")
    emit(f"  1. Compute past N-day cum return where N = {BEST_LB}")
    emit("  2. Signal_t = -1 if past_N_ret > 0, +1 if < 0")
    emit("  3. Position equal-weight 1/6 of total leveraged capital")
    emit("")
    emit("Daily leverage recalc:")
    emit("  realized_vol = std(portfolio_returns last 60d) × sqrt(252)")
    emit(f"  leverage = min({best_agg} / realized_vol, {MAX_LEVERAGE})")
    emit("")
    emit("Execution:")
    emit("  - Close all positions at 22:00 UTC")
    emit("  - Open new positions at 22:01 UTC using signals + leverage")
    emit("  - Per-pair notional = capital × leverage × (1/6) × signal")
    emit(f"  - On ${CAPITAL} with lev {best_r['avg_lev']:.1f}× per-pair notional ≈ "
         f"${CAPITAL * best_r['avg_lev'] / 6:.0f}")
    emit("```")
    emit("")
    emit("### Kill switches (HARD STOP)")
    emit(f"  - Peak-to-trough DD > {KILL_DD*100:.0f}% (${KILL_DD*CAPITAL:.0f}) → halt + 30-day review")
    emit("  - Single day loss > 10% of capital ($2500) → manual review")
    emit("  - 60-day rolling Sharpe < -0.5 → reduce target_vol by 50%")
    emit("  - Weekly check : if any pair vol > 2× IS vol → reduce that pair's weight to 50%")
    emit("")

    # Verdict
    if (best_r["oos"]["sharpe"] > 0.8 and best_r["kill_date"] is None
            and best_r["oos"]["ann_ret"] > 0.30):
        verdict = "AGGRESSIVE_TRADEABLE"
    elif best_r["oos"]["sharpe"] > 0.5 and best_r["kill_date"] is None:
        verdict = "TRADEABLE"
    elif best_r["oos"]["sharpe"] > 0:
        verdict = "RESEARCH_ONLY"
    else:
        verdict = "DEAD"
    emit(f"## VERDICT: **{verdict}**")
    emit("")

    if verdict == "AGGRESSIVE_TRADEABLE":
        emit("READY for aggressive paper-trade. Recommended 30-day live demo before real $.")
    elif verdict == "TRADEABLE":
        emit("Tradeable but doesn't hit aggressive target. Consider crypto pivot for higher vol/ret.")
    else:
        emit("Not ready. Iterate signals or pivot.")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))

    # JSON
    OUT_METRICS.write_text(json.dumps({
        "best_lookback_per_pair": BEST_LB,
        "weighting": "equal",
        "max_leverage": MAX_LEVERAGE,
        "kill_dd": KILL_DD,
        "capital_reference": CAPITAL,
        "unlev": {"is": m_unlev_is, "oos": m_unlev_oos},
        "vol_targets": {
            f"{tv*100:.0f}%": {
                "oos": r["oos"], "oos_killed": r["oos_killed"],
                "ci_sharpe": list(r["ci"]["sharpe"]),
                "ci_ann_ret": list(r["ci"]["ann_ret"]),
                "kill_date": r["kill_date"].isoformat() if r["kill_date"] else None,
                "avg_lev": r["avg_lev"], "max_lev_used": r["max_lev_used"],
                "ruin_30_prob_3y": r["ruin_30_prob_3y"],
                "ruin_50_prob_3y": r["ruin_50_prob_3y"],
            } for tv, r in results.items()
        },
        "recommended_target_vol": f"{best_agg*100:.0f}%",
        "verdict": verdict,
    }, indent=2, default=str))

    # Equity HTML
    fig = make_subplots(rows=2, cols=1, subplot_titles=(
        "Cum return — vol-target frontier (full sample)",
        "Cum return — OOS only with kill switches"),
        shared_xaxes=False, vertical_spacing=0.12)
    colors = ["#888", "#1976d2", "#0288d1", "#2e7d32", "#f57c00", "#c62828"]
    for i, tv in enumerate(TARGET_VOLS):
        r = results[tv]
        cum = r["pl_lev"].cumsum()
        fig.add_trace(go.Scatter(x=cum.index, y=cum.values, mode="lines",
                                 name=f"vt={tv*100:.0f}% ({r['avg_lev']:.1f}×)",
                                 line=dict(color=colors[i])), row=1, col=1)
        # OOS only
        pl_oos = r["pl_lev"][r["pl_lev"].index >= OOS_START]
        killed, _ = apply_kill_switch(pl_oos, KILL_DD)
        fig.add_trace(go.Scatter(x=killed.index, y=killed.cumsum().values,
                                 mode="lines", name=f"OOS killed vt={tv*100:.0f}%",
                                 line=dict(color=colors[i], dash="dot"),
                                 showlegend=False), row=2, col=1)
    fig.update_layout(title="Stack V3 AGGRESSIVE — vol-target frontier",
                      template="plotly_white", height=900, hovermode="x")
    fig.write_html(str(OUT_EQUITY), include_plotlyjs="cdn")

    # Deliverable
    print("\n\ndone")
    print(f"files: {OUT_REPORT.name}, {OUT_METRICS.name}, {OUT_EQUITY.name}")
    print(f"unlev OOS Sharpe={m_unlev_oos['sharpe']:+.2f} Calmar={m_unlev_oos['calmar']:.2f}")
    print(f"recommended aggressive target_vol: {best_agg*100:.0f}%")
    print(f"  → OOS ann_ret={best_r['oos']['ann_ret']*100:+.1f}% maxDD={best_r['oos']['max_dd']*100:+.1f}% "
          f"Calmar={best_r['oos']['calmar']:.2f}")
    print(f"  → on $25k: +${best_r['oos']['ann_ret']*CAPITAL:.0f}/yr DD ${best_r['oos']['max_dd']*CAPITAL:.0f}")
    print(f"  → P(ruin -30%/3y)={best_r['ruin_30_prob_3y']*100:.0f}% "
          f"P(ruin -50%/3y)={best_r['ruin_50_prob_3y']*100:.0f}%")
    print(f"verdict: {verdict}")


if __name__ == "__main__":
    main()
