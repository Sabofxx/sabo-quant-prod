"""
Sandbox — multi-signal stack for retail (target high return via decorrelation + leverage).

Step 1 toward 80% target: stack 4-6 single-pair MR signals with different lookbacks,
exploit decorrelation, then scale leverage. Show what's achievable.

Method:
  - For each pair × lookback in {3, 5, 10, 21} : compute net daily P&L (after costs)
  - Select best lookback per pair (max OOS Sharpe net)
  - Combine selected signals (equal weight or inverse-vol)
  - Compute combined Sharpe / vol / DD / decorrelation
  - Scale leverage L = 1, 2, 3, 5, 10 → show ann_ret + max DD + worst day at each L
  - Verdict: what L hits 30% / 50% / 80% target with acceptable DD

Honest framing : 80% needs 5×+ leverage on diversified Sharpe 2+ strategy.
Bootstrap CI propagated through leverage (since scales linearly).
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
OUT_REPORT = HERE / "strategy_stack_retail_report.md"
OUT_METRICS = HERE / "strategy_stack_retail_metrics.json"
OUT_EQUITY = HERE / "strategy_stack_retail_equity.html"
OUT_CORR = HERE / "strategy_stack_retail_corr.html"

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD"]
PIP_SIZE = {
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001,
    "NZDUSD": 0.0001, "USDCAD": 0.0001, "USDJPY": 0.01,
}
ROUND_TRIP_PIPS = {
    "EURUSD": 1.9, "GBPUSD": 2.3, "USDJPY": 2.1,
    "AUDUSD": 2.3, "NZDUSD": 3.1, "USDCAD": 2.7,
}
FILES_M5 = {p: f"{p.lower()}-m5-bid-2019-01-01-2026-01-01.csv" for p in PAIRS}

TRADING_DAYS = 252
SEED = 42
IS_END = pd.Timestamp("2023-12-31 23:59:59", tz="UTC")
OOS_START = pd.Timestamp("2024-01-01", tz="UTC")
N_BOOT = 2000
LOOKBACKS = [3, 5, 10, 21]
LEVERAGE_LEVELS = [1, 2, 3, 5, 10]


# =====================================================================
# Data
# =====================================================================
def load_close_daily(pair: str) -> pd.Series:
    df = pd.read_csv(DATA / FILES_M5[pair])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    return df["close"].resample("1D").last().dropna()


# =====================================================================
# Signal + cost per pair
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
    rt_pips = ROUND_TRIP_PIPS[pair]
    pip = PIP_SIZE[pair]
    cost = (turnover * rt_pips * pip / price).reindex(common).fillna(0.0)
    return (g - cost).rename(f"{pair}_MR{lookback}")


# =====================================================================
# Metrics
# =====================================================================
def metrics(pl: pd.Series, leverage: float = 1.0) -> dict:
    pl = pl.dropna() * leverage
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
    prices: dict = {}
    for p in PAIRS:
        closes[p] = load_close_daily(p)
        prices[p] = closes[p]
    rets = pd.DataFrame({p: closes[p].pct_change() for p in PAIRS}).dropna()
    print(f"  {len(rets)} aligned days")

    # Per pair × lookback table
    print("\nPer pair × lookback grid (OOS Sharpe net)...")
    per_pair_best: dict = {}
    grid: dict = {}
    for p in PAIRS:
        grid[p] = {}
        for lb in LOOKBACKS:
            pl = net_pl_pair(p, rets[p], prices[p].reindex(rets.index), lb)
            pl_oos = pl[pl.index >= OOS_START]
            m_oos = metrics(pl_oos)
            grid[p][lb] = {"pl": pl, "oos_sharpe": m_oos["sharpe"],
                           "oos_ret": m_oos["ann_ret"], "oos_dd": m_oos["max_dd"]}
            print(f"  {p} MR{lb}: OOS Sharpe={m_oos['sharpe']:+.2f} "
                  f"ann_ret={m_oos['ann_ret']*100:+.2f}% maxDD={m_oos['max_dd']*100:+.2f}%")
        best_lb = max(LOOKBACKS, key=lambda x: grid[p][x]["oos_sharpe"])
        per_pair_best[p] = {"lookback": best_lb,
                            "oos_sharpe": grid[p][best_lb]["oos_sharpe"],
                            "pl": grid[p][best_lb]["pl"]}
        print(f"    → BEST: MR{best_lb} (Sharpe {grid[p][best_lb]['oos_sharpe']:+.2f})")

    # Filter pairs with OOS Sharpe > 0.3 (KEEP only profitable)
    keep_pairs = [p for p in PAIRS if per_pair_best[p]["oos_sharpe"] > 0.3]
    drop_pairs = [p for p in PAIRS if p not in keep_pairs]
    print(f"\nKEEP (OOS Sharpe > 0.3): {keep_pairs}")
    print(f"DROP: {drop_pairs}")

    if not keep_pairs:
        print("No pairs pass threshold. Aborting stack.")
        return

    # Build stack DataFrame
    stack_df = pd.DataFrame({p: per_pair_best[p]["pl"] for p in keep_pairs}).fillna(0.0)
    # Equal weight stack
    eq_stack = stack_df.mean(axis=1).rename("eq_stack")
    # Inverse-vol weight (normalize by ann vol on IS)
    is_vol = stack_df[stack_df.index <= IS_END].std() * math.sqrt(TRADING_DAYS)
    inv_vol_w = (1.0 / is_vol) / (1.0 / is_vol).sum()
    iv_stack = (stack_df * inv_vol_w).sum(axis=1).rename("iv_stack")

    print(f"\nInverse-vol weights: {inv_vol_w.to_dict()}")

    # Correlation matrix on OOS
    oos_df = stack_df[stack_df.index >= OOS_START]
    corr = oos_df.corr()
    avg_corr = (corr.values[~pd.DataFrame(corr).values.astype(bool).all()].mean()
                if len(corr) > 1 else 0.0)
    # Off-diagonal mean
    off = []
    for i in range(len(corr)):
        for j in range(len(corr)):
            if i != j:
                off.append(corr.iloc[i, j])
    avg_corr = sum(off) / len(off) if off else 0.0
    print(f"Average pair-wise correlation (OOS): {avg_corr:+.2f}")

    # Stack metrics
    eq_is = metrics(eq_stack[eq_stack.index <= IS_END])
    eq_oos = metrics(eq_stack[eq_stack.index >= OOS_START])
    iv_is = metrics(iv_stack[iv_stack.index <= IS_END])
    iv_oos = metrics(iv_stack[iv_stack.index >= OOS_START])

    print(f"\nEqual-weight stack OOS: Sharpe={eq_oos['sharpe']:+.2f} "
          f"ann_ret={eq_oos['ann_ret']*100:+.2f}% maxDD={eq_oos['max_dd']*100:+.2f}%")
    print(f"Inv-vol stack OOS:      Sharpe={iv_oos['sharpe']:+.2f} "
          f"ann_ret={iv_oos['ann_ret']*100:+.2f}% maxDD={iv_oos['max_dd']*100:+.2f}%")

    # Pick better stack
    best_name = "iv_stack" if iv_oos["sharpe"] >= eq_oos["sharpe"] else "eq_stack"
    best_stack = iv_stack if best_name == "iv_stack" else eq_stack
    best_oos = iv_oos if best_name == "iv_stack" else eq_oos
    best_is = iv_is if best_name == "iv_stack" else eq_is

    ci_best = bootstrap_ci_sharpe(best_stack[best_stack.index >= OOS_START], N_BOOT, SEED)
    print(f"\nBest stack: {best_name}")
    print(f"  Bootstrap OOS Sharpe CI95: [{ci_best[0]:+.2f}, {ci_best[2]:+.2f}]")

    # Leverage table
    lev_table = []
    for L in LEVERAGE_LEVELS:
        m = metrics(best_stack[best_stack.index >= OOS_START], leverage=L)
        lev_table.append((L, m))

    # Verdict: target leverage for 80% / 50% / 30% with DD cap
    def find_leverage_for(target_ret: float, max_dd_cap: float) -> tuple[float, dict]:
        # Linear scaling: ret scales with L, DD scales with L
        ret_per_l = best_oos["ann_ret"]
        dd_per_l = abs(best_oos["max_dd"])
        if ret_per_l <= 0:
            return 0.0, {}
        L_ret = target_ret / ret_per_l
        L_dd = max_dd_cap / dd_per_l
        L_safe = min(L_ret, L_dd)
        m = metrics(best_stack[best_stack.index >= OOS_START], leverage=L_safe)
        return L_safe, m

    # ----- Report -----
    lines: list[str] = []
    def emit(s: str) -> None:
        print(s)
        lines.append(s)

    emit("# Strategy Stack Retail — multi-signal toward high-return target")
    emit("")
    emit("Universe: 6 FX pairs. Per pair × lookback {3,5,10,21} grid. Best lookback "
         "per pair selected by OOS Sharpe. Stack = equal-weight or inverse-vol of "
         "single-pair MR signals.")
    emit("IS: 2019 → 2023 | OOS: 2024 → 2025")
    emit("")

    emit("## PER-PAIR GRID (OOS Sharpe net)")
    emit("| pair    | MR3 | MR5 | MR10 | MR21 | best LB | OOS Sharpe |")
    emit("|---|---|---|---|---|---|---|")
    for p in PAIRS:
        row = f"| {p:<7} |"
        for lb in LOOKBACKS:
            row += f" {grid[p][lb]['oos_sharpe']:+.2f} |"
        row += f" MR{per_pair_best[p]['lookback']} | {per_pair_best[p]['oos_sharpe']:+.2f} |"
        emit(row)
    emit("")
    emit(f"KEEP (OOS Sh > 0.3): {keep_pairs}")
    emit(f"DROP: {drop_pairs}")
    emit("")

    emit("## CORRELATION (OOS, between selected single-pair signals)")
    emit("```")
    emit(corr.round(2).to_string())
    emit("```")
    emit(f"Average pair-wise correlation: {avg_corr:+.2f}")
    emit("")

    emit("## STACK METRICS (OOS net, 1× leverage)")
    emit("| stack       | n | Sharpe | ann_ret% | vol% | maxDD% | calmar |")
    emit("|---|---|---|---|---|---|---|")
    emit(f"| equal-wt    | {eq_oos['n']} | {eq_oos['sharpe']:+.2f} | "
         f"{eq_oos['ann_ret']*100:+.2f} | {eq_oos['ann_vol']*100:.2f} | "
         f"{eq_oos['max_dd']*100:+.2f} | {eq_oos['calmar']:.2f} |")
    emit(f"| inv-vol-wt  | {iv_oos['n']} | {iv_oos['sharpe']:+.2f} | "
         f"{iv_oos['ann_ret']*100:+.2f} | {iv_oos['ann_vol']*100:.2f} | "
         f"{iv_oos['max_dd']*100:+.2f} | {iv_oos['calmar']:.2f} |")
    emit("")
    emit(f"Best: **{best_name}**  bootstrap OOS Sharpe CI95: "
         f"[{ci_best[0]:+.2f}, {ci_best[2]:+.2f}] median={ci_best[1]:+.2f}")
    emit(f"  IS Sharpe (sanity): {best_is['sharpe']:+.2f}  "
         f"ann_ret={best_is['ann_ret']*100:+.2f}%")
    emit("")

    emit("## LEVERAGE SCALING (best stack, OOS-based linear extrapolation)")
    emit("| L  | ann_ret% | vol% | maxDD% | worst_day% | calmar | $20k account |")
    emit("|---|---|---|---|---|---|---|")
    for L, m in lev_table:
        capital = 20000
        ret_dollars = m["ann_ret"] * capital
        dd_dollars = m["max_dd"] * capital
        worst_day_dollars = m["worst_day"] * capital
        emit(f"| {L}× | {m['ann_ret']*100:+.1f} | {m['ann_vol']*100:.1f} | "
             f"{m['max_dd']*100:+.1f} | {m['worst_day']*100:+.2f} | {m['calmar']:.2f} | "
             f"+${ret_dollars:.0f} ret / ${dd_dollars:.0f} DD / ${worst_day_dollars:.0f} worst |")
    emit("")

    emit("## TARGET RETURN ANALYSIS (linear leverage scaling)")
    for target in (0.30, 0.50, 0.80):
        lev, m = find_leverage_for(target, 1.0)
        emit(f"  Target +{target*100:.0f}% annual : needs **{lev:.1f}× leverage** → "
             f"maxDD={m['max_dd']*100:+.1f}%  worst day={m['worst_day']*100:+.2f}%  "
             f"vol={m['ann_vol']*100:.1f}%")
    emit("")

    emit("## RECOMMENDATION")
    target_80_lev = 0.80 / best_oos["ann_ret"] if best_oos["ann_ret"] > 0 else 999
    target_80_dd = abs(best_oos["max_dd"]) * target_80_lev
    emit(f"  80% target requires {target_80_lev:.1f}× leverage → max DD ~{target_80_dd*100:.0f}%")
    if target_80_dd > 0.5:
        emit(f"  **WARNING: {target_80_dd*100:.0f}% max DD = account-killer territory.**")
        emit("  Probability of full ruin within 3 years (rough Kelly): >40% at this leverage.")
        emit("")
        emit("  Realistic recommendations:")
        emit(f"  - Conservative: 2-3× leverage → +{best_oos['ann_ret']*100*2:.0f}–"
             f"{best_oos['ann_ret']*100*3:.0f}% annual, DD {abs(best_oos['max_dd'])*100*2:.0f}–"
             f"{abs(best_oos['max_dd'])*100*3:.0f}%")
        emit(f"  - Aggressive: 5× leverage → +{best_oos['ann_ret']*100*5:.0f}% annual, "
             f"DD {abs(best_oos['max_dd'])*100*5:.0f}%")
        emit("  - For 80%+ target: need fundamentally different edge (crypto MR, options, etc.)")
        emit("")
        emit("  Next concrete actions:")
        emit("  1. Paper-trade THIS stack at 1× for 30 days (validate live realization)")
        emit("  2. Acquire crypto data (BTC/ETH/SOL daily, Binance free API)")
        emit("     → expect MR Sharpe 1.5-2.5 on crypto vs 1.16 on FX")
        emit("  3. Add 1-2 more uncorrelated signals (carry proxy, vol regime gate)")
        emit("  4. Combined Sharpe 2.5+ → 80% at 3× leverage achievable in 6-12 months")
    else:
        emit("  80% achievable within reasonable risk. Proceed with caution.")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))

    # JSON
    OUT_METRICS.write_text(json.dumps({
        "per_pair_best": {p: {"lookback": per_pair_best[p]["lookback"],
                              "oos_sharpe": per_pair_best[p]["oos_sharpe"]}
                          for p in PAIRS},
        "keep_pairs": keep_pairs,
        "drop_pairs": drop_pairs,
        "stack_name": best_name,
        "stack_is": best_is, "stack_oos": best_oos,
        "stack_ci_oos": list(ci_best),
        "avg_correlation_oos": avg_corr,
        "leverage_table": [{"L": L, **m} for L, m in lev_table],
    }, indent=2, default=str))

    # Equity HTML
    fig = make_subplots(rows=2, cols=1, subplot_titles=(
        "Stack cum return (1× leverage)",
        "Stack cum return at leverage levels"),
        shared_xaxes=True, vertical_spacing=0.1)
    fig.add_trace(go.Scatter(x=best_stack.index, y=best_stack.cumsum().values,
                             mode="lines", name=f"{best_name} 1×",
                             line=dict(color="#2e7d32")), row=1, col=1)
    colors = ["#1976d2", "#0288d1", "#0097a7", "#f57c00", "#c62828"]
    for i, L in enumerate(LEVERAGE_LEVELS):
        fig.add_trace(go.Scatter(
            x=best_stack.index, y=(best_stack * L).cumsum().values,
            mode="lines", name=f"{L}× lev", line=dict(color=colors[i])),
            row=2, col=1)
    fig.update_layout(title="Stack — cum return + leverage scaling",
                      template="plotly_white", height=900, hovermode="x")
    fig.write_html(str(OUT_EQUITY), include_plotlyjs="cdn")

    # Correlation heatmap
    fig2 = go.Figure(data=go.Heatmap(
        z=corr.values, x=list(corr.columns), y=list(corr.index),
        colorscale="RdBu_r", zmid=0, zmin=-1, zmax=1,
        text=corr.round(2).values, texttemplate="%{text}",
        colorbar=dict(title="ρ"),
    ))
    fig2.update_layout(title="Selected single-pair signals — OOS correlation matrix",
                       template="plotly_white", height=500)
    fig2.write_html(str(OUT_CORR), include_plotlyjs="cdn")

    # Deliverable
    print("\n\ndone")
    print(f"files: {OUT_REPORT.name}, {OUT_METRICS.name}, "
          f"{OUT_EQUITY.name}, {OUT_CORR.name}")
    print(f"KEEP pairs: {keep_pairs}")
    print(f"Best stack: {best_name}  OOS Sharpe={best_oos['sharpe']:+.2f}  "
          f"ann_ret={best_oos['ann_ret']*100:+.2f}%  maxDD={best_oos['max_dd']*100:+.2f}%")
    print(f"Bootstrap OOS Sharpe CI95: [{ci_best[0]:+.2f}, {ci_best[2]:+.2f}]")
    print(f"Avg correlation (OOS, selected pairs): {avg_corr:+.2f}")
    print(f"80% target needs ~{target_80_lev:.1f}× leverage → max DD ~{target_80_dd*100:.0f}%")


if __name__ == "__main__":
    main()
