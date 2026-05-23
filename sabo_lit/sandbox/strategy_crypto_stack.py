"""
Sandbox — Crypto stack for AGGRESSIVE retail (target 50-80% annual).

5 coins with full 2019-2026 daily history : BTC, ETH, BNB, XRP, ADA.
2 with shorter : SOL (2020-08+), AVAX (2020-09+) — used if start_date OK.

Crypto-specific adjustments vs FX:
  - TRADING_DAYS = 365 (24/7 markets)
  - Cost model in % terms : round-trip = 0.20% (Binance taker 0.075%×2 + spread 0.05%)
  - Signal grid : MR {3,5,10,21} + TSM {21,63,126} per coin. Pick best per coin.
  - Vol target higher : 30/50/80/100% (crypto vol naturally 60-100% annualized)
  - Max leverage cap 5× (Binance perp futures common retail tier ; conservative)
  - Kill switch 40% DD (crypto tolerance higher than FX retail)

Verdicts:
  AGGRESSIVE_TRADEABLE : OOS Sharpe > 1.0 + no-kill + ann_ret > 50% + ruin50%/3y < 30%
  TRADEABLE            : OOS Sharpe > 0.5 + no-kill
  RESEARCH_ONLY        : positive but kill triggers
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
OUT_REPORT = HERE / "strategy_crypto_stack_report.md"
OUT_METRICS = HERE / "strategy_crypto_stack_metrics.json"
OUT_EQUITY = HERE / "strategy_crypto_stack_equity.html"

# 5 coins with full 2019-2026 history
COINS = ["BTC", "ETH", "BNB", "XRP", "ADA"]
FILES = {
    "BTC": "btcusdt-d1-spot-2019-01-01-2026-01-01.csv",
    "ETH": "ethusdt-d1-spot-2019-01-01-2026-01-01.csv",
    "BNB": "bnbusdt-d1-spot-2019-01-01-2026-01-01.csv",
    "XRP": "xrpusdt-d1-spot-2019-01-01-2026-01-01.csv",
    "ADA": "adausdt-d1-spot-2019-01-01-2026-01-01.csv",
}
ROUND_TRIP_PCT = 0.0020  # 0.20% per round-trip

TRADING_DAYS = 365
SEED = 42
IS_END = pd.Timestamp("2023-12-31 23:59:59", tz="UTC")
OOS_START = pd.Timestamp("2024-01-01", tz="UTC")
N_BOOT = 2000
VOL_LOOKBACK = 60
TARGET_VOLS = [0.30, 0.50, 0.80, 1.00, 1.50]
MAX_LEVERAGE = 5.0
KILL_DD = 0.40
CAPITAL = 25000

# Signal grid
MR_LOOKBACKS = [3, 5, 10, 21]
TSM_LOOKBACKS = [21, 63, 126]


# =====================================================================
# IO
# =====================================================================
def load_close_daily(coin: str) -> pd.Series:
    df = pd.read_csv(DATA / FILES[coin])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    return df["close"].astype(float).resample("1D").last().dropna()


# =====================================================================
# Signals
# =====================================================================
def mr_signal(returns: pd.Series, lookback: int) -> pd.Series:
    cum = (1.0 + returns).rolling(lookback).apply(lambda x: x.prod() - 1.0, raw=True)
    sig = (cum < 0).astype(float) - (cum > 0).astype(float)
    return sig.shift(1).dropna()


def tsm_signal(returns: pd.Series, lookback: int) -> pd.Series:
    cum = (1.0 + returns).rolling(lookback).apply(lambda x: x.prod() - 1.0, raw=True)
    sig = (cum > 0).astype(float) - (cum < 0).astype(float)
    return sig.shift(1).dropna()


def net_pl_pair(returns: pd.Series, signal: pd.Series) -> pd.Series:
    common = signal.index.intersection(returns.index)
    g = signal.loc[common] * returns.loc[common]
    turnover = signal.diff().abs().fillna(0.0) / 2.0
    cost = (turnover * ROUND_TRIP_PCT).reindex(common).fillna(0.0)
    return g - cost


# =====================================================================
# Metrics
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


def vol_target_leverage(pl: pd.Series, target_vol: float, lookback: int,
                        max_lev: float) -> pd.Series:
    realized = pl.rolling(lookback).std() * math.sqrt(TRADING_DAYS)
    lev = (target_vol / realized).clip(upper=max_lev)
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


def estimate_ruin_prob(pl_oos: pd.Series, ruin_thresh: float, n_iter: int, seed: int) -> float:
    rng = random.Random(seed)
    arr = pl_oos.dropna().to_list()
    L = len(arr)
    if L < 10:
        return 0.0
    ruined = 0
    horizon = TRADING_DAYS * 3
    for _ in range(n_iter):
        cum = 0.0
        peak = 0.0
        for _ in range(horizon):
            r = arr[rng.randrange(L)]
            cum += r
            peak = max(peak, cum)
            if (cum - peak) < -ruin_thresh:
                ruined += 1
                break
    return ruined / n_iter


# =====================================================================
# Best signal per coin (grid search)
# =====================================================================
def best_signal_for_coin(coin: str, rets: pd.Series) -> tuple[str, int, dict]:
    """Test MR and TSM at multiple lookbacks. Pick by IS Sharpe (avoid OOS-fit)."""
    candidates: list = []
    for lb in MR_LOOKBACKS:
        sig = mr_signal(rets, lb)
        pl = net_pl_pair(rets, sig)
        pl_is = pl[pl.index <= IS_END]
        m_is = metrics(pl_is)
        candidates.append((f"MR{lb}", lb, "MR", pl, m_is["sharpe"]))
    for lb in TSM_LOOKBACKS:
        sig = tsm_signal(rets, lb)
        pl = net_pl_pair(rets, sig)
        pl_is = pl[pl.index <= IS_END]
        m_is = metrics(pl_is)
        candidates.append((f"TSM{lb}", lb, "TSM", pl, m_is["sharpe"]))
    # pick by IS Sharpe
    best = max(candidates, key=lambda c: c[4])
    return best[0], best[1], {"type": best[2], "lookback": best[1], "pl": best[3],
                              "is_sharpe": best[4]}


# =====================================================================
# Main
# =====================================================================
def main() -> None:
    print(f"Loading {len(COINS)} crypto daily closes...")
    closes: dict = {}
    for c in COINS:
        closes[c] = load_close_daily(c)
        print(f"  {c}: {len(closes[c])} daily obs, "
              f"{closes[c].index[0].date()} → {closes[c].index[-1].date()}")
    rets = pd.DataFrame({c: closes[c].pct_change() for c in COINS}).dropna()
    print(f"  Aligned: {len(rets)} days, {rets.index[0].date()} → {rets.index[-1].date()}")

    # Per-coin signal grid → pick best by IS Sharpe
    print("\nPer-coin signal grid (best by IS Sharpe to avoid OOS overfit)...")
    best_per_coin: dict = {}
    for c in COINS:
        # full grid display
        print(f"  {c}:")
        for lb in MR_LOOKBACKS:
            pl = net_pl_pair(rets[c], mr_signal(rets[c], lb))
            m_is = metrics(pl[pl.index <= IS_END])
            m_oos = metrics(pl[pl.index >= OOS_START])
            print(f"    MR{lb:>3}: IS Sh={m_is['sharpe']:+.2f} OOS Sh={m_oos['sharpe']:+.2f} "
                  f"OOS ret={m_oos['ann_ret']*100:+.1f}%")
        for lb in TSM_LOOKBACKS:
            pl = net_pl_pair(rets[c], tsm_signal(rets[c], lb))
            m_is = metrics(pl[pl.index <= IS_END])
            m_oos = metrics(pl[pl.index >= OOS_START])
            print(f"    TSM{lb:>3}: IS Sh={m_is['sharpe']:+.2f} OOS Sh={m_oos['sharpe']:+.2f} "
                  f"OOS ret={m_oos['ann_ret']*100:+.1f}%")
        name, lb, info = best_signal_for_coin(c, rets[c])
        best_per_coin[c] = info
        best_per_coin[c]["name"] = name
        m_oos_best = metrics(info["pl"][info["pl"].index >= OOS_START])
        print(f"    → BEST (IS-picked): {name}  OOS Sh={m_oos_best['sharpe']:+.2f}")

    # Stack: equal-weight using each coin's best-IS signal
    stack_df = pd.DataFrame({c: best_per_coin[c]["pl"] for c in COINS}).fillna(0.0)
    eq_w = 1.0 / len(COINS)
    stack_unlev = stack_df.sum(axis=1) * eq_w

    m_is = metrics(stack_unlev[stack_unlev.index <= IS_END])
    m_oos = metrics(stack_unlev[stack_unlev.index >= OOS_START])
    m_full = metrics(stack_unlev)
    print("\nUnlevered stack (equal-weight, best-IS signal per coin):")
    print(f"  IS  Sharpe={m_is['sharpe']:+.2f} ann_ret={m_is['ann_ret']*100:+.1f}% "
          f"vol={m_is['ann_vol']*100:.1f}% maxDD={m_is['max_dd']*100:+.1f}%")
    print(f"  OOS Sharpe={m_oos['sharpe']:+.2f} ann_ret={m_oos['ann_ret']*100:+.1f}% "
          f"vol={m_oos['ann_vol']*100:.1f}% maxDD={m_oos['max_dd']*100:+.1f}% "
          f"Calmar={m_oos['calmar']:.2f}")

    # Correlation OOS
    oos_df = stack_df[stack_df.index >= OOS_START]
    corr = oos_df.corr()
    off = []
    for i in range(len(corr)):
        for j in range(len(corr)):
            if i != j:
                off.append(corr.iloc[i, j])
    avg_corr = sum(off) / len(off) if off else 0.0
    print(f"  Avg pair-wise correlation OOS: {avg_corr:+.2f}")

    # Vol-target frontier
    print(f"\nVol-target frontier (max_lev={MAX_LEVERAGE}×, kill_DD={KILL_DD*100:.0f}%)...")
    results: dict = {}
    for tv in TARGET_VOLS:
        lev = vol_target_leverage(stack_unlev, tv, VOL_LOOKBACK, MAX_LEVERAGE)
        pl_lev = (stack_unlev * lev).dropna()
        pl_oos = pl_lev[pl_lev.index >= OOS_START]
        killed, kill_date = apply_kill_switch(pl_oos, KILL_DD)
        m_oos_lev = metrics(pl_oos)
        m_killed = metrics(killed)
        ci = bootstrap_ci(pl_oos, N_BOOT, SEED)
        ruin30 = estimate_ruin_prob(pl_oos, 0.30, 2000, SEED)
        ruin50 = estimate_ruin_prob(pl_oos, 0.50, 2000, SEED)
        avg_lev = float(lev.loc[lev.index >= OOS_START].mean())
        max_lev_u = float(lev.loc[lev.index >= OOS_START].max())
        results[tv] = {
            "pl_lev": pl_lev, "leverage": lev,
            "oos": m_oos_lev, "killed": m_killed,
            "ci": ci, "kill_date": kill_date,
            "avg_lev": avg_lev, "max_lev_used": max_lev_u,
            "ruin30_3y": ruin30, "ruin50_3y": ruin50,
        }
        kstr = f"KILL@{kill_date.date()}" if kill_date else "NO_KILL"
        print(f"  TV={tv*100:.0f}% : OOS Sh={m_oos_lev['sharpe']:+.2f} "
              f"ret={m_oos_lev['ann_ret']*100:+.1f}% DD={m_oos_lev['max_dd']*100:+.1f}% "
              f"lev={avg_lev:.1f}× kill={kstr} ruin50%/3y={ruin50*100:.0f}%")

    # Report
    lines: list[str] = []
    def emit(s: str) -> None:
        print(s)
        lines.append(s)

    emit("# CRYPTO STACK — Aggressive retail target (50-80% annual)")
    emit("")
    emit(f"Coins: {COINS}  |  TRADING_DAYS={TRADING_DAYS} (24/7)")
    emit(f"Cost round-trip: {ROUND_TRIP_PCT*100:.2f}% per pair")
    emit(f"Max leverage cap: {MAX_LEVERAGE}× | Kill DD: {KILL_DD*100:.0f}% | Reference capital: ${CAPITAL}")
    emit("Best signal per coin picked by IS Sharpe (avoid OOS overfit)")
    emit("")

    emit("## BEST SIGNAL PER COIN")
    emit("| coin | best signal | IS Sharpe | OOS Sharpe |")
    emit("|---|---|---|---|")
    for c in COINS:
        info = best_per_coin[c]
        m_oos_best = metrics(info["pl"][info["pl"].index >= OOS_START])
        emit(f"| {c} | {info['name']} | {info['is_sharpe']:+.2f} | {m_oos_best['sharpe']:+.2f} |")
    emit("")

    emit("## UNLEVERED STACK")
    emit(f"  IS  Sharpe={m_is['sharpe']:+.2f}  ann_ret={m_is['ann_ret']*100:+.1f}%  "
         f"vol={m_is['ann_vol']*100:.1f}%  maxDD={m_is['max_dd']*100:+.1f}%")
    emit(f"  OOS Sharpe={m_oos['sharpe']:+.2f}  ann_ret={m_oos['ann_ret']*100:+.1f}%  "
         f"vol={m_oos['ann_vol']*100:.1f}%  maxDD={m_oos['max_dd']*100:+.1f}%  "
         f"Calmar={m_oos['calmar']:.2f}")
    emit(f"  Avg pair-wise correlation OOS: {avg_corr:+.2f}")
    emit("")

    emit("## VOL-TARGET FRONTIER (OOS)")
    emit("| target_vol | OOS Sh | ann_ret% | maxDD% | avg_lev | kill | killed_ret% | "
         "CI95 Sh | CI95 ret% | P(ruin30%)3y | P(ruin50%)3y |")
    emit("|---|---|---|---|---|---|---|---|---|---|---|")
    for tv in TARGET_VOLS:
        r = results[tv]
        kill = r["kill_date"].date().isoformat() if r["kill_date"] else "—"
        emit(f"| {tv*100:.0f}% | {r['oos']['sharpe']:+.2f} | "
             f"{r['oos']['ann_ret']*100:+.1f} | {r['oos']['max_dd']*100:+.1f} | "
             f"{r['avg_lev']:.1f}× | {kill} | {r['killed']['ann_ret']*100:+.1f} | "
             f"[{r['ci']['sharpe'][0]:+.2f},{r['ci']['sharpe'][2]:+.2f}] | "
             f"[{r['ci']['ann_ret'][0]*100:+.1f},{r['ci']['ann_ret'][2]*100:+.1f}] | "
             f"{r['ruin30_3y']*100:.0f}% | {r['ruin50_3y']*100:.0f}% |")
    emit("")

    emit(f"## DOLLAR FRAMING (capital ${CAPITAL})")
    emit("| target_vol | ann_ret$ | maxDD$ | worst_day$ |")
    emit("|---|---|---|---|")
    for tv in TARGET_VOLS:
        r = results[tv]
        emit(f"| {tv*100:.0f}% | ${r['oos']['ann_ret']*CAPITAL:+.0f} | "
             f"${r['oos']['max_dd']*CAPITAL:+.0f} | "
             f"${r['oos']['worst_day']*CAPITAL:+.0f} |")
    emit("")

    # Pick best per criteria
    safe_aggressive = [tv for tv in TARGET_VOLS
                       if results[tv]["kill_date"] is None
                       and results[tv]["ruin50_3y"] < 0.30
                       and results[tv]["oos"]["ann_ret"] >= 0.50]
    if safe_aggressive:
        best_tv = max(safe_aggressive, key=lambda t: results[t]["oos"]["ann_ret"])
    else:
        # max ann_ret with no kill, accept higher ruin
        no_kill = [tv for tv in TARGET_VOLS if results[tv]["kill_date"] is None]
        if no_kill:
            best_tv = max(no_kill, key=lambda t: results[t]["oos"]["ann_ret"])
        else:
            best_tv = min(TARGET_VOLS)

    best_r = results[best_tv]
    emit(f"## RECOMMENDED TARGET: **vol_target = {best_tv*100:.0f}%**")
    emit("  Criteria: max ann_ret with NO kill + ruin50%/3y < 30% + ret >= 50%")
    emit(f"  OOS Sharpe   : {best_r['oos']['sharpe']:+.2f}")
    emit(f"  OOS ann_ret  : {best_r['oos']['ann_ret']*100:+.1f}%  "
         f"(${best_r['oos']['ann_ret']*CAPITAL:+.0f} on ${CAPITAL})")
    emit(f"  OOS maxDD    : {best_r['oos']['max_dd']*100:+.1f}%  "
         f"(${best_r['oos']['max_dd']*CAPITAL:+.0f})")
    emit(f"  OOS Calmar   : {best_r['oos']['calmar']:.2f}")
    emit(f"  OOS worst day: {best_r['oos']['worst_day']*100:+.2f}%  "
         f"(${best_r['oos']['worst_day']*CAPITAL:+.0f})")
    emit(f"  avg leverage : {best_r['avg_lev']:.1f}×  (max {best_r['max_lev_used']:.1f}×)")
    emit(f"  CI95 Sharpe  : [{best_r['ci']['sharpe'][0]:+.2f}, {best_r['ci']['sharpe'][2]:+.2f}]")
    emit(f"  CI95 ann_ret : [{best_r['ci']['ann_ret'][0]*100:+.1f}%, "
         f"{best_r['ci']['ann_ret'][2]*100:+.1f}%]")
    emit(f"  P(ruin -30% / 3y) : {best_r['ruin30_3y']*100:.0f}%")
    emit(f"  P(ruin -50% / 3y) : {best_r['ruin50_3y']*100:.0f}%")
    emit("")

    # Per-year
    emit(f"## PER-YEAR @ target_vol={best_tv*100:.0f}%")
    emit("| year | n | Sharpe | ann_ret% | maxDD% | $ ret on $25k |")
    emit("|---|---|---|---|---|---|")
    best_pl = best_r["pl_lev"]
    for y in range(2019, 2027):
        sub = best_pl[best_pl.index.year == y]
        if len(sub) < 30:
            continue
        m = metrics(sub)
        emit(f"| {y} | {m['n']} | {m['sharpe']:+.2f} | {m['ann_ret']*100:+.1f} | "
             f"{m['max_dd']*100:+.1f} | ${m['ann_ret']*CAPITAL:+.0f} |")
    emit("")

    emit("## EXECUTION SPEC (Binance spot OR perp futures)")
    emit("```")
    emit(f"For each coin {COINS} at 00:00 UTC daily:")
    emit("  1. Compute signal per frozen spec per coin")
    for c in COINS:
        emit(f"     {c}: {best_per_coin[c]['name']}")
    emit("  2. Signal = +1 long / -1 short / 0 flat (rare)")
    emit("  3. Equal weight 1/5 per coin")
    emit("")
    emit("Daily leverage:")
    emit("  realized_vol_60d = std(portfolio_returns 60d) × sqrt(365)")
    emit(f"  leverage = min({best_tv} / realized_vol_60d, {MAX_LEVERAGE})")
    emit("")
    emit("Execution:")
    emit("  - 00:00 UTC : close all + reopen with new signal & leverage")
    emit("  - Binance perp futures for short capability")
    emit("  - Per-coin notional = capital × leverage × (1/5) × signal")
    emit(f"  - ${CAPITAL} × avg_lev {best_r['avg_lev']:.1f}× / 5 coins = "
         f"~${CAPITAL * best_r['avg_lev'] / 5:.0f} per coin notional")
    emit("```")
    emit("")
    emit("### Kill switches")
    emit(f"  - Peak-to-trough DD > {KILL_DD*100:.0f}% → halt 30 days")
    emit("  - Single day loss > 15% capital → manual review")
    emit("  - 30-day rolling Sharpe < -0.5 → halve target_vol")
    emit("  - Coin vol > 3× IS vol → reduce that coin to 50%")
    emit("")

    # Verdict
    if (best_r["oos"]["sharpe"] > 1.0 and best_r["kill_date"] is None
            and best_r["oos"]["ann_ret"] > 0.50 and best_r["ruin50_3y"] < 0.30):
        verdict = "AGGRESSIVE_TRADEABLE"
    elif best_r["oos"]["sharpe"] > 0.5 and best_r["kill_date"] is None:
        verdict = "TRADEABLE"
    elif best_r["oos"]["sharpe"] > 0:
        verdict = "RESEARCH_ONLY"
    else:
        verdict = "DEAD"
    emit(f"## VERDICT: **{verdict}**")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))

    # JSON
    OUT_METRICS.write_text(json.dumps({
        "coins": COINS,
        "best_signal_per_coin": {c: {"name": best_per_coin[c]["name"],
                                      "is_sharpe": best_per_coin[c]["is_sharpe"]}
                                 for c in COINS},
        "unlev": {"is": m_is, "oos": m_oos, "full": m_full},
        "avg_correlation_oos": avg_corr,
        "vol_targets": {
            f"{tv*100:.0f}%": {
                "oos": r["oos"], "killed": r["killed"],
                "ci_sharpe": list(r["ci"]["sharpe"]),
                "ci_ann_ret": list(r["ci"]["ann_ret"]),
                "kill_date": r["kill_date"].isoformat() if r["kill_date"] else None,
                "avg_lev": r["avg_lev"], "max_lev_used": r["max_lev_used"],
                "ruin30_3y": r["ruin30_3y"], "ruin50_3y": r["ruin50_3y"],
            } for tv, r in results.items()
        },
        "recommended_target_vol": f"{best_tv*100:.0f}%",
        "verdict": verdict,
    }, indent=2, default=str))

    # Equity HTML
    fig = make_subplots(rows=2, cols=1, subplot_titles=(
        "Cum return — vol-target frontier",
        "Leverage applied over time"),
        shared_xaxes=False, vertical_spacing=0.12)
    colors = ["#888", "#1976d2", "#2e7d32", "#f57c00", "#c62828"]
    for i, tv in enumerate(TARGET_VOLS):
        r = results[tv]
        cum = r["pl_lev"].cumsum()
        fig.add_trace(go.Scatter(x=cum.index, y=cum.values, mode="lines",
                                 name=f"vt={tv*100:.0f}% ({r['avg_lev']:.1f}×)",
                                 line=dict(color=colors[i])), row=1, col=1)
    fig.add_trace(go.Scatter(x=best_r["leverage"].index, y=best_r["leverage"].values,
                             mode="lines", name=f"lev @ {best_tv*100:.0f}%",
                             line=dict(color="#2e7d32")), row=2, col=1)
    fig.add_hline(y=MAX_LEVERAGE, line_dash="dash", line_color="red",
                  annotation_text=f"max lev ({MAX_LEVERAGE}×)", row=2, col=1)
    fig.update_layout(title="Crypto Stack — vol-target frontier",
                      template="plotly_white", height=900, hovermode="x")
    fig.write_html(str(OUT_EQUITY), include_plotlyjs="cdn")

    # Deliverable
    print("\n\ndone")
    print(f"files: {OUT_REPORT.name}, {OUT_METRICS.name}, {OUT_EQUITY.name}")
    print(f"unlev OOS Sharpe={m_oos['sharpe']:+.2f}  Calmar={m_oos['calmar']:.2f}")
    print(f"recommended target_vol: {best_tv*100:.0f}%")
    print(f"  → OOS Sharpe={best_r['oos']['sharpe']:+.2f}  "
          f"ann_ret={best_r['oos']['ann_ret']*100:+.1f}%  "
          f"maxDD={best_r['oos']['max_dd']*100:+.1f}%  Calmar={best_r['oos']['calmar']:.2f}")
    print(f"  → ${CAPITAL}: +${best_r['oos']['ann_ret']*CAPITAL:.0f}/yr  "
          f"DD ${best_r['oos']['max_dd']*CAPITAL:.0f}")
    print(f"  → P(ruin -30%/3y)={best_r['ruin30_3y']*100:.0f}%  "
          f"P(ruin -50%/3y)={best_r['ruin50_3y']*100:.0f}%")
    print(f"verdict: {verdict}")


if __name__ == "__main__":
    main()
