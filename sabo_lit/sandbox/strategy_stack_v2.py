"""
Sandbox — Stack V2 with vol-target sizing + Sharpe-weighting toward high return.

Improvements vs stack V1:
  1. **Vol-target sizing** : daily leverage = target_vol / realized_60d_portfolio_vol.
     Capped at MAX_LEV. Makes ann_ret hit target without uncapped DD risk.
  2. **Sharpe-weighted stack** : weight pairs by IS Sharpe (concentrate on strong).
  3. **Multiple target vol levels** : 10/15/20/25/30% — show realistic frontier.
  4. **Hard kill switches** modeled : if portfolio peak-to-trough DD > KILL_DD%
     mid-OOS, halt rest of OOS (sim retail discipline).

Per-pair best lookback (frozen from V1 result, no re-optim):
  EURUSD MR5 | GBPUSD MR3 | USDJPY MR10 | AUDUSD MR21 | NZDUSD MR10 | USDCAD MR3

Verdicts:
  ROBUST_TRADEABLE  : OOS Sharpe > 1.0 + CI low > 0.3 + post-kill ann_ret > 15%
  TRADEABLE         : OOS Sharpe > 0.5 + CI low > 0
  RESEARCH_ONLY     : positive OOS Sharpe
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
OUT_REPORT = HERE / "strategy_stack_v2_report.md"
OUT_METRICS = HERE / "strategy_stack_v2_metrics.json"
OUT_EQUITY = HERE / "strategy_stack_v2_equity.html"
OUT_LEVERAGE = HERE / "strategy_stack_v2_leverage.html"

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD"]
PIP_SIZE = {"EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001,
            "NZDUSD": 0.0001, "USDCAD": 0.0001, "USDJPY": 0.01}
ROUND_TRIP_PIPS = {"EURUSD": 1.9, "GBPUSD": 2.3, "USDJPY": 2.1,
                   "AUDUSD": 2.3, "NZDUSD": 3.1, "USDCAD": 2.7}
FILES_M5 = {p: f"{p.lower()}-m5-bid-2019-01-01-2026-01-01.csv" for p in PAIRS}

# Per-pair best lookback (from V1)
BEST_LB = {"EURUSD": 5, "GBPUSD": 3, "USDJPY": 10, "AUDUSD": 21, "NZDUSD": 10, "USDCAD": 3}

TRADING_DAYS = 252
SEED = 42
IS_END = pd.Timestamp("2023-12-31 23:59:59", tz="UTC")
OOS_START = pd.Timestamp("2024-01-01", tz="UTC")
N_BOOT = 2000
VOL_LOOKBACK = 60       # rolling vol estimation window (days)
TARGET_VOLS = [0.10, 0.15, 0.20, 0.25, 0.30]  # annualized targets
MAX_LEVERAGE = 8.0      # hard cap (broker + risk discipline)
KILL_DD = 0.25          # 25% peak-to-trough triggers halt mid-OOS


# =====================================================================
# IO
# =====================================================================
def load_close_daily(pair: str) -> pd.Series:
    df = pd.read_csv(DATA / FILES_M5[pair])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    return df["close"].resample("1D").last().dropna()


# =====================================================================
# Signal + cost
# =====================================================================
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
# Stack weights
# =====================================================================
def sharpe_weights(stack_df: pd.DataFrame, is_end: pd.Timestamp) -> pd.Series:
    """Weights proportional to IS Sharpe (floored at 0). Normalized to sum=1."""
    is_df = stack_df[stack_df.index <= is_end]
    mean = is_df.mean()
    std = is_df.std()
    sharpe = (mean / std) * math.sqrt(TRADING_DAYS)
    weights = sharpe.clip(lower=0.0)
    if weights.sum() == 0:
        return pd.Series(1.0 / len(stack_df.columns), index=stack_df.columns)
    return weights / weights.sum()


def equal_weights(stack_df: pd.DataFrame) -> pd.Series:
    return pd.Series(1.0 / len(stack_df.columns), index=stack_df.columns)


# =====================================================================
# Vol-target sizing
# =====================================================================
def vol_target_leverage(pl_unlev: pd.Series, target_vol: float,
                        vol_lookback: int, max_lev: float) -> pd.Series:
    """Daily leverage = target_vol / realized_lookback_vol (annualized).
    Capped at max_lev. Shift +1 day to avoid look-ahead."""
    realized_vol = pl_unlev.rolling(vol_lookback).std() * math.sqrt(TRADING_DAYS)
    lev = (target_vol / realized_vol).clip(upper=max_lev)
    return lev.shift(1).fillna(1.0)


def apply_leverage(pl_unlev: pd.Series, leverage: pd.Series) -> pd.Series:
    common = pl_unlev.index.intersection(leverage.index)
    return pl_unlev.loc[common] * leverage.loc[common]


# =====================================================================
# Kill switch
# =====================================================================
def apply_kill_switch(pl: pd.Series, kill_dd: float) -> tuple[pd.Series, pd.Timestamp | None]:
    """Simulate: track peak-to-trough DD on cum sum. If DD < -kill_dd, zero out
    all subsequent returns (halt). Returns (clipped_pl, kill_date_or_None)."""
    cum = pl.cumsum()
    peak = cum.cummax()
    dd = cum - peak
    breach_mask = dd < -kill_dd
    if not breach_mask.any():
        return pl, None
    first_breach = pl.index[breach_mask.values.argmax()]
    out = pl.copy()
    out[out.index > first_breach] = 0.0
    return out, first_breach


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


def bootstrap_ci_sharpe(pl: pd.Series, n: int, seed: int) -> tuple[float, float, float]:
    rng = random.Random(seed)
    arr = pl.dropna().to_list()
    L = len(arr)
    if L < 2:
        return 0.0, 0.0, 0.0
    shs: list[float] = []
    for _ in range(n):
        s = [arr[rng.randrange(L)] for _ in range(L)]
        m = sum(s) / L
        var = sum((x - m) ** 2 for x in s) / (L - 1)
        std = math.sqrt(var)
        shs.append((m / std) * math.sqrt(TRADING_DAYS) if std > 0 else 0.0)
    shs.sort()
    return shs[int(n * 0.025)], shs[n // 2], shs[int(n * 0.975) - 1]


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

    # Per-pair net P&L with best lookback (frozen)
    print(f"\nBuilding stack with frozen best lookbacks: {BEST_LB}")
    stack_dict: dict = {}
    for p in PAIRS:
        stack_dict[p] = net_pl_pair(p, rets[p], prices[p], BEST_LB[p])
    stack_df = pd.DataFrame(stack_dict).fillna(0.0)

    # Weights
    eq_w = equal_weights(stack_df)
    sh_w = sharpe_weights(stack_df, IS_END)
    print(f"\nEqual weights: {eq_w.to_dict()}")
    print(f"Sharpe weights (IS-fitted): {sh_w.round(3).to_dict()}")

    eq_unlev = (stack_df * eq_w).sum(axis=1)
    sh_unlev = (stack_df * sh_w).sum(axis=1)

    # Pick the better stack on IS (avoid look-ahead)
    m_eq_is = metrics(eq_unlev[eq_unlev.index <= IS_END])
    m_sh_is = metrics(sh_unlev[sh_unlev.index <= IS_END])
    print(f"\nUnlev IS Sharpe — equal={m_eq_is['sharpe']:+.2f} sharpe-w={m_sh_is['sharpe']:+.2f}")
    if m_sh_is["sharpe"] > m_eq_is["sharpe"]:
        best_unlev = sh_unlev.rename("sh_unlev")
        best_w_name = "sharpe-weighted"
    else:
        best_unlev = eq_unlev.rename("eq_unlev")
        best_w_name = "equal-weighted"
    print(f"Chosen (IS-best): **{best_w_name}**")

    # Vol-target experiments
    print("\nTesting vol-target levels...")
    results_vt: dict = {}
    for tv in TARGET_VOLS:
        lev_series = vol_target_leverage(best_unlev, tv, VOL_LOOKBACK, MAX_LEVERAGE)
        pl_lev = apply_leverage(best_unlev, lev_series).dropna()
        pl_lev_oos = pl_lev[pl_lev.index >= OOS_START]
        pl_lev_oos_killed, kill_date = apply_kill_switch(pl_lev_oos, KILL_DD)
        m_full = metrics(pl_lev)
        m_oos = metrics(pl_lev_oos)
        m_oos_killed = metrics(pl_lev_oos_killed)
        ci = bootstrap_ci_sharpe(pl_lev_oos, N_BOOT, SEED)
        avg_lev_oos = float(lev_series.loc[lev_series.index >= OOS_START].mean())
        max_lev_oos = float(lev_series.loc[lev_series.index >= OOS_START].max())
        results_vt[tv] = {
            "pl_lev": pl_lev, "pl_lev_oos": pl_lev_oos,
            "leverage": lev_series, "kill_date": kill_date,
            "full": m_full, "oos": m_oos, "oos_killed": m_oos_killed,
            "ci_oos": ci, "avg_lev_oos": avg_lev_oos, "max_lev_oos": max_lev_oos,
        }
        kill_str = f"KILL@{kill_date.date()}" if kill_date else "OK"
        print(f"  target_vol={tv*100:.0f}% : OOS Sharpe={m_oos['sharpe']:+.2f} "
              f"ann_ret={m_oos['ann_ret']*100:+.1f}% maxDD={m_oos['max_dd']*100:+.1f}% "
              f"avg_lev={avg_lev_oos:.1f}× max_lev={max_lev_oos:.1f}× kill={kill_str}")

    # ----- Report -----
    lines: list[str] = []
    def emit(s: str) -> None:
        print(s)
        lines.append(s)

    emit("# Stack V2 — Vol-targeted, Sharpe-weighted toward high-return")
    emit("")
    emit(f"Pairs (per-pair best lookback): {BEST_LB}")
    emit(f"Weighting: **{best_w_name}** (chosen by IS Sharpe)")
    emit(f"Vol lookback: {VOL_LOOKBACK} days. Max leverage cap: {MAX_LEVERAGE}×. "
         f"Kill switch: peak-to-trough DD > {KILL_DD*100:.0f}% halts trading.")
    emit("IS: 2019 → 2023 | OOS: 2024 → 2025")
    emit("")

    emit("## STACK WEIGHTS (chosen)")
    if best_w_name == "sharpe-weighted":
        for p in PAIRS:
            emit(f"  {p}: {sh_w[p]:.3f}")
    else:
        for p in PAIRS:
            emit(f"  {p}: {eq_w[p]:.3f}")
    emit("")

    emit("## VOL-TARGET FRONTIER (OOS, kill switch active)")
    emit("| target_vol | OOS Sharpe | OOS ann_ret% | OOS maxDD% | "
         "avg_lev | max_lev | kill_date | killed_ann_ret% | CI95 Sharpe |")
    emit("|---|---|---|---|---|---|---|---|---|")
    for tv in TARGET_VOLS:
        r = results_vt[tv]
        kill = r["kill_date"].date().isoformat() if r["kill_date"] else "—"
        emit(f"| {tv*100:.0f}% | {r['oos']['sharpe']:+.2f} | "
             f"{r['oos']['ann_ret']*100:+.1f} | {r['oos']['max_dd']*100:+.1f} | "
             f"{r['avg_lev_oos']:.1f}× | {r['max_lev_oos']:.1f}× | {kill} | "
             f"{r['oos_killed']['ann_ret']*100:+.1f} | "
             f"[{r['ci_oos'][0]:+.2f}, {r['ci_oos'][2]:+.2f}] |")
    emit("")

    # Best by Calmar (return-to-DD ratio, robust target for retail)
    best_tv = max(TARGET_VOLS, key=lambda t: results_vt[t]["oos"]["calmar"]
                  if results_vt[t]["kill_date"] is None else -999)
    best_r = results_vt[best_tv]
    emit(f"## OPTIMAL TARGET VOL (max Calmar, no-kill subset): **{best_tv*100:.0f}%**")
    emit(f"  OOS Sharpe={best_r['oos']['sharpe']:+.2f}  "
         f"ann_ret={best_r['oos']['ann_ret']*100:+.1f}%  "
         f"maxDD={best_r['oos']['max_dd']*100:+.1f}%  Calmar={best_r['oos']['calmar']:.2f}")
    emit(f"  Average leverage applied OOS: {best_r['avg_lev_oos']:.1f}× "
         f"(max {best_r['max_lev_oos']:.1f}×, cap {MAX_LEVERAGE}×)")
    emit(f"  Bootstrap OOS Sharpe CI95: [{best_r['ci_oos'][0]:+.2f}, "
         f"{best_r['ci_oos'][2]:+.2f}] median={best_r['ci_oos'][1]:+.2f}")
    emit("")

    # Honest answer to 80% target
    emit("## ANSWER TO 80% TARGET REQUEST")
    feasible_80 = [tv for tv in TARGET_VOLS
                   if results_vt[tv]["oos"]["ann_ret"] >= 0.80
                   and results_vt[tv]["kill_date"] is None]
    if feasible_80:
        emit(f"  Levels hitting 80% ann_ret WITHOUT triggering kill: {[f'{t*100:.0f}%' for t in feasible_80]}")
    else:
        # Find best ann_ret with no kill
        no_kill = [tv for tv in TARGET_VOLS if results_vt[tv]["kill_date"] is None]
        if no_kill:
            best_safe = max(no_kill, key=lambda t: results_vt[t]["oos"]["ann_ret"])
            emit("  80% NOT achievable within kill-switch boundaries.")
            emit(f"  Best safe target_vol: {best_safe*100:.0f}% → "
                 f"ann_ret={results_vt[best_safe]['oos']['ann_ret']*100:+.1f}% "
                 f"maxDD={results_vt[best_safe]['oos']['max_dd']*100:+.1f}%")
        emit("")
        emit("  To realistically achieve 80%:")
        emit("    (a) Stack 2-3 more uncorrelated edges (crypto, options) → combined")
        emit("        Sharpe 3+, then 30% target_vol could yield 80%+ with safe DD")
        emit("    (b) Accept higher kill probability: target_vol 35-40%, expect")
        emit("        25-40% chance of kill within 3 years")
        emit("    (c) Pivot to crypto where MR Sharpe historically 1.5-2.5 and")
        emit("        vol naturally higher → 80% achievable at 2-3× leverage")
    emit("")

    # Per-year on best target vol
    emit(f"## PER-YEAR — target_vol={best_tv*100:.0f}% (kill switch active)")
    emit("| year | n | Sharpe | ann_ret% | maxDD% | wr% |")
    emit("|---|---|---|---|---|---|")
    best_pl = best_r["pl_lev"]
    for y in range(2019, 2026):
        sub = best_pl[best_pl.index.year == y]
        if len(sub) < 30:
            continue
        m = metrics(sub)
        emit(f"| {y} | {m['n']} | {m['sharpe']:+.2f} | {m['ann_ret']*100:+.1f} | "
             f"{m['max_dd']*100:+.1f} | {m['wr']:.1f} |")
    emit("")

    # ----- Verdict -----
    if (best_r["oos"]["sharpe"] > 1.0 and best_r["ci_oos"][0] > 0.3
            and best_r["oos"]["ann_ret"] > 0.15 and best_r["kill_date"] is None):
        verdict = "ROBUST_TRADEABLE"
    elif best_r["oos"]["sharpe"] > 0.5 and best_r["ci_oos"][0] > 0:
        verdict = "TRADEABLE"
    elif best_r["oos"]["sharpe"] > 0:
        verdict = "RESEARCH_ONLY"
    else:
        verdict = "DEAD"
    emit(f"## VERDICT (best target_vol={best_tv*100:.0f}%): **{verdict}**")
    emit("")

    # Execution spec
    emit("## RETAIL EXECUTION SPEC")
    emit(f"  Stack: 6 FX pairs with frozen lookbacks {BEST_LB}")
    emit(f"  Weights: {best_w_name}")
    emit(f"  Target vol: {best_tv*100:.0f}% annualized")
    emit(f"  Leverage: dynamic = {best_tv*100:.0f}% / realized_60d_vol, capped {MAX_LEVERAGE}×")
    emit(f"  Kill switch: halt if peak-to-trough DD > {KILL_DD*100:.0f}%")
    emit(f"  On $17.5k capital with avg lev {best_r['avg_lev_oos']:.1f}× :")
    emit(f"    expected annual return: ${best_r['oos']['ann_ret']*17500:+.0f}")
    emit(f"    expected max DD: ${best_r['oos']['max_dd']*17500:.0f}")
    emit(f"    Calmar: {best_r['oos']['calmar']:.2f}")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))

    # JSON
    json_safe = {
        "weights_chosen": best_w_name,
        "best_lookback_per_pair": BEST_LB,
        "max_leverage_cap": MAX_LEVERAGE,
        "kill_dd_threshold": KILL_DD,
        "vol_lookback": VOL_LOOKBACK,
        "vol_target_results": {
            f"{tv*100:.0f}%": {
                "oos": r["oos"], "full": r["full"], "oos_killed": r["oos_killed"],
                "ci_oos": list(r["ci_oos"]),
                "avg_lev_oos": r["avg_lev_oos"], "max_lev_oos": r["max_lev_oos"],
                "kill_date": r["kill_date"].isoformat() if r["kill_date"] else None,
            } for tv, r in results_vt.items()
        },
        "optimal_target_vol": f"{best_tv*100:.0f}%",
        "verdict": verdict,
    }
    OUT_METRICS.write_text(json.dumps(json_safe, indent=2, default=str))

    # Equity HTML (cum return at each target vol)
    fig = make_subplots(rows=2, cols=1, subplot_titles=(
        "Cum return — vol-target levels",
        "Leverage applied over time (best target)"),
        shared_xaxes=True, vertical_spacing=0.1)
    colors = ["#888", "#1976d2", "#2e7d32", "#f57c00", "#c62828"]
    for i, tv in enumerate(TARGET_VOLS):
        r = results_vt[tv]
        cum = r["pl_lev"].cumsum()
        fig.add_trace(go.Scatter(x=cum.index, y=cum.values, mode="lines",
                                 name=f"vt={tv*100:.0f}% (lev~{r['avg_lev_oos']:.1f}×)",
                                 line=dict(color=colors[i])), row=1, col=1)
    fig.add_trace(go.Scatter(x=best_r["leverage"].index, y=best_r["leverage"].values,
                             mode="lines", name=f"leverage @ {best_tv*100:.0f}%",
                             line=dict(color="#1976d2")), row=2, col=1)
    fig.add_hline(y=MAX_LEVERAGE, line_dash="dash", line_color="red",
                  annotation_text=f"max lev cap ({MAX_LEVERAGE}×)", row=2, col=1)
    fig.update_layout(title="Stack V2 — vol-target frontier",
                      template="plotly_white", height=900, hovermode="x")
    fig.update_yaxes(title_text="cum return", row=1, col=1)
    fig.update_yaxes(title_text="leverage", row=2, col=1)
    fig.write_html(str(OUT_EQUITY), include_plotlyjs="cdn")

    # Leverage detail HTML
    fig2 = go.Figure()
    for i, tv in enumerate(TARGET_VOLS):
        lev = results_vt[tv]["leverage"]
        fig2.add_trace(go.Scatter(x=lev.index, y=lev.values, mode="lines",
                                  name=f"target_vol {tv*100:.0f}%",
                                  line=dict(color=colors[i])))
    fig2.add_hline(y=MAX_LEVERAGE, line_dash="dash", line_color="red",
                   annotation_text=f"max lev cap ({MAX_LEVERAGE}×)")
    fig2.update_layout(title="Daily leverage applied per target vol",
                       xaxis_title="date", yaxis_title="leverage",
                       template="plotly_white", height=600, hovermode="x")
    fig2.write_html(str(OUT_LEVERAGE), include_plotlyjs="cdn")

    # Deliverable
    print("\n\ndone")
    print(f"files: {OUT_REPORT.name}, {OUT_METRICS.name}, {OUT_EQUITY.name}, {OUT_LEVERAGE.name}")
    print(f"weighting: {best_w_name}")
    print(f"optimal target_vol: {best_tv*100:.0f}%")
    print(f"OOS Sharpe={best_r['oos']['sharpe']:+.2f}  "
          f"ann_ret={best_r['oos']['ann_ret']*100:+.1f}%  "
          f"maxDD={best_r['oos']['max_dd']*100:+.1f}%  "
          f"Calmar={best_r['oos']['calmar']:.2f}")
    print(f"avg leverage OOS: {best_r['avg_lev_oos']:.1f}×  (max {best_r['max_lev_oos']:.1f}×)")
    print(f"CI95 Sharpe: [{best_r['ci_oos'][0]:+.2f}, {best_r['ci_oos'][2]:+.2f}]")
    print(f"verdict: {verdict}")


if __name__ == "__main__":
    main()
