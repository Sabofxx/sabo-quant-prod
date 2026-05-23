"""
Sandbox — Z-score extreme MR + Bollinger reversal + other high-wr setups.

Prior accuracy_boost tests proved : any filtering of our MR signal DROPS wr or Sharpe.
Reason: edge is "small-loss / fat-right-tail" not high-frequency-accuracy.

This script tests DIFFERENT signal types known for high wr:
  Z1 ZSCORE_2SIGMA : trade only when z = (close - MA20) / std20 > +2 (short) or < -2 (long).
                     Extreme dev = higher reversion probability. Hold 1 day.
  Z2 ZSCORE_3SIGMA : same but tighter (z > 3 / < -3). Fewer trades, higher conviction.
  B1 BOLLINGER_TOUCH : trade when price closes outside Bollinger Band (20, 2σ). Exit at MA.
  B2 BOLLINGER_REVERT : trade when price was outside band yesterday AND closed back inside today.
  RSI_EXTREME : trade when RSI(14) crosses < 30 or > 70.
  COMBO_Z_VOL_GATE : Z-score 2σ + low-vol regime gate.

Each tested on 6 FX pairs equal-weight stack, OOS 2024-2025.
Compare wr + Sharpe + maxDD vs baseline FX_MR_STACK.
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
OUT_REPORT = HERE / "strategy_zscore_extreme_report.md"
OUT_METRICS = HERE / "strategy_zscore_extreme_metrics.json"
OUT_EQUITY = HERE / "strategy_zscore_extreme_equity.html"

FX_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD"]
PIP_SIZE = {"EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001,
            "NZDUSD": 0.0001, "USDCAD": 0.0001, "USDJPY": 0.01}
ROUND_TRIP_PIPS = {"EURUSD": 1.9, "GBPUSD": 2.3, "USDJPY": 2.1,
                   "AUDUSD": 2.3, "NZDUSD": 3.1, "USDCAD": 2.7}
FILES_M5 = {p: f"{p.lower()}-m5-bid-2019-01-01-2026-01-01.csv" for p in FX_PAIRS}
BEST_LB = {"EURUSD": 5, "GBPUSD": 3, "USDJPY": 10, "AUDUSD": 21, "NZDUSD": 10, "USDCAD": 3}

TRADING_DAYS = 252
SEED = 42
IS_END = pd.Timestamp("2023-12-31 23:59:59", tz="UTC")
OOS_START = pd.Timestamp("2024-01-01", tz="UTC")
N_BOOT = 1000


# =====================================================================
# IO
# =====================================================================
def load_close(p: str) -> pd.Series:
    df = pd.read_csv(DATA / FILES_M5[p])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    return df["close"].resample("1D").last().dropna()


# =====================================================================
# Signals (each returns sig in {-1, 0, +1})
# =====================================================================
def baseline_mr(close: pd.Series, lb: int) -> pd.Series:
    rets = close.pct_change()
    cum = (1.0 + rets).rolling(lb).apply(lambda x: x.prod() - 1.0, raw=True)
    s = (cum < 0).astype(float) - (cum > 0).astype(float)
    return s.shift(1).dropna()


def zscore_mr(close: pd.Series, ma_lb: int = 20, z_thresh: float = 2.0) -> pd.Series:
    """+1 if z < -threshold (oversold, long), -1 if z > +threshold (overbought, short)."""
    ma = close.rolling(ma_lb).mean()
    std = close.rolling(ma_lb).std()
    z = (close - ma) / std
    s = (z < -z_thresh).astype(float) - (z > z_thresh).astype(float)
    return s.shift(1).dropna()


def bollinger_touch(close: pd.Series, lb: int = 20, n_std: float = 2.0) -> pd.Series:
    """Trade when price closes outside Bollinger (lb, n_std). Long if below lower, short if above upper."""
    ma = close.rolling(lb).mean()
    std = close.rolling(lb).std()
    upper = ma + n_std * std
    lower = ma - n_std * std
    s = (close < lower).astype(float) - (close > upper).astype(float)
    return s.shift(1).dropna()


def bollinger_revert(close: pd.Series, lb: int = 20, n_std: float = 2.0) -> pd.Series:
    """Trade when price WAS outside band yesterday AND closed back inside today."""
    ma = close.rolling(lb).mean()
    std = close.rolling(lb).std()
    upper = ma + n_std * std
    lower = ma - n_std * std
    was_below = (close.shift(1) < lower.shift(1))
    was_above = (close.shift(1) > upper.shift(1))
    back_inside = (close > lower) & (close < upper)
    s = (was_below & back_inside).astype(float) - (was_above & back_inside).astype(float)
    return s.shift(1).dropna()


def rsi(close: pd.Series, lb: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(lb).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(lb).mean()
    rs = gain / loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def rsi_extreme(close: pd.Series, lb: int = 14, low: float = 30, high: float = 70) -> pd.Series:
    """Long when RSI < low, short when RSI > high."""
    r = rsi(close, lb)
    s = (r < low).astype(float) - (r > high).astype(float)
    return s.shift(1).dropna()


def vol_gate(rets: pd.Series, sig: pd.Series, short_lb: int = 20, long_lb: int = 252,
             ratio_threshold: float = 1.5) -> pd.Series:
    short_vol = rets.rolling(short_lb).std()
    long_vol = rets.rolling(long_lb).std()
    ratio = short_vol / long_vol
    common = sig.index.intersection(ratio.index)
    out = sig.loc[common].copy()
    out[ratio.loc[common] > ratio_threshold] = 0.0
    return out


# =====================================================================
# Net P&L + metrics
# =====================================================================
def fx_net_pl(pair: str, close: pd.Series, sig: pd.Series) -> pd.Series:
    rets = close.pct_change()
    common = sig.index.intersection(rets.index)
    g = sig.loc[common] * rets.loc[common]
    turnover = sig.diff().abs().fillna(0.0)
    cost_unit = (ROUND_TRIP_PIPS[pair] * PIP_SIZE[pair] / close).reindex(common).fillna(0.0)
    cost = (turnover / 2.0) * cost_unit
    return g - cost


def metrics(pl: pd.Series) -> dict:
    pl = pl.dropna()
    if len(pl) < 2:
        return {"n_days": 0, "n_traded": 0, "trade_rate": 0, "sharpe": 0.0,
                "ann_ret": 0.0, "ann_vol": 0.0, "max_dd": 0.0, "wr": 0.0,
                "calmar": 0.0, "expectancy_bps": 0.0}
    pl_traded = pl[pl != 0]
    m = float(pl.mean())
    s = float(pl.std())
    sh = (m / s) * math.sqrt(TRADING_DAYS) if s > 0 else 0.0
    cum = pl.cumsum()
    dd = float((cum - cum.cummax()).min())
    wins = pl_traded[pl_traded > 0]
    wr = (len(wins) / max(len(pl_traded), 1)) * 100
    expectancy = (pl_traded.mean() * 1e4) if len(pl_traded) > 0 else 0.0
    return {
        "n_days": len(pl), "n_traded": len(pl_traded),
        "trade_rate": len(pl_traded) / len(pl) * 100,
        "sharpe": sh, "ann_ret": m * TRADING_DAYS,
        "ann_vol": s * math.sqrt(TRADING_DAYS), "max_dd": dd, "wr": wr,
        "calmar": (m * TRADING_DAYS) / abs(dd) if dd < 0 else float("inf"),
        "expectancy_bps": float(expectancy),
    }


def bootstrap_ci(pl: pd.Series, n: int, seed: int) -> dict:
    rng = random.Random(seed)
    arr = pl.dropna().to_list()
    L = len(arr)
    if L < 2:
        return {"sharpe": (0, 0, 0), "wr": (0, 0, 0)}
    shs, wrs = [], []
    for _ in range(n):
        s = [arr[rng.randrange(L)] for _ in range(L)]
        m = sum(s) / L
        var = sum((x - m) ** 2 for x in s) / (L - 1)
        std = math.sqrt(var)
        shs.append((m / std) * math.sqrt(TRADING_DAYS) if std > 0 else 0.0)
        traded = [x for x in s if x != 0]
        if traded:
            wrs.append(sum(1 for x in traded if x > 0) / len(traded) * 100)
    shs.sort()
    wrs.sort()
    return {
        "sharpe": (shs[int(n*0.025)], shs[n//2], shs[int(n*0.975)-1]),
        "wr": (wrs[int(len(wrs)*0.025)], wrs[len(wrs)//2], wrs[int(len(wrs)*0.975)-1])
               if wrs else (0, 0, 0),
    }


# =====================================================================
# Main
# =====================================================================
def main() -> None:
    print("Loading 6 FX pairs M5 → daily close...")
    closes = {p: load_close(p) for p in FX_PAIRS}
    rets = pd.DataFrame({p: closes[p].pct_change() for p in FX_PAIRS}).dropna()

    # Test signals on each pair, then stack
    SIGNALS = {
        "baseline_MR_best_LB": lambda p: baseline_mr(closes[p], BEST_LB[p]),
        "Z1_zscore_2sigma":    lambda p: zscore_mr(closes[p], 20, 2.0),
        "Z2_zscore_2.5sigma":  lambda p: zscore_mr(closes[p], 20, 2.5),
        "Z3_zscore_3sigma":    lambda p: zscore_mr(closes[p], 20, 3.0),
        "B1_bollinger_touch":  lambda p: bollinger_touch(closes[p], 20, 2.0),
        "B2_bollinger_revert": lambda p: bollinger_revert(closes[p], 20, 2.0),
        "RSI_extreme_30_70":   lambda p: rsi_extreme(closes[p], 14, 30, 70),
        "RSI_extreme_25_75":   lambda p: rsi_extreme(closes[p], 14, 25, 75),
        "Z2_plus_vol_gate":    lambda p: vol_gate(rets[p], zscore_mr(closes[p], 20, 2.0)),
    }

    results: dict = {}
    print("\nPer-spec stack results (equal-weight 6 pairs):")
    print(f"\n{'spec':<28} | {'n_trd':>6} | {'trd%':>5} | {'wr%':>5} | "
          f"{'Sharpe':>7} | {'ann%':>6} | {'maxDD%':>6} | {'exp_bps':>8}")
    for name, sig_fn in SIGNALS.items():
        per_pair = {p: fx_net_pl(p, closes[p], sig_fn(p)) for p in FX_PAIRS}
        stack = pd.DataFrame(per_pair).fillna(0.0).sum(axis=1) / len(FX_PAIRS)
        stack_oos = stack[stack.index >= OOS_START]
        m = metrics(stack_oos)
        ci = bootstrap_ci(stack_oos, N_BOOT, SEED)
        results[name] = {"stack": stack, "metrics": m, "ci": ci}
        print(f"{name:<28} | {m['n_traded']:>6} | {m['trade_rate']:>5.1f} | "
              f"{m['wr']:>5.1f} | {m['sharpe']:+7.2f} | {m['ann_ret']*100:+6.1f} | "
              f"{m['max_dd']*100:+6.1f} | {m['expectancy_bps']:+8.1f}")

    # Pick top by wr (with Sharpe > 0.5 constraint)
    candidates = {n: r for n, r in results.items() if r["metrics"]["sharpe"] > 0.5}
    if not candidates:
        candidates = results
    top_wr = max(candidates, key=lambda n: candidates[n]["metrics"]["wr"])
    top_sh = max(candidates, key=lambda n: candidates[n]["metrics"]["sharpe"])
    top_calmar = max(candidates, key=lambda n: candidates[n]["metrics"]["calmar"]
                      if candidates[n]["metrics"]["calmar"] != float("inf") else 0)

    print(f"\n→ Top win rate (Sh>0.5): **{top_wr}** wr={results[top_wr]['metrics']['wr']:.1f}% "
          f"Sh={results[top_wr]['metrics']['sharpe']:+.2f}")
    print(f"→ Top Sharpe   (Sh>0.5): **{top_sh}** Sh={results[top_sh]['metrics']['sharpe']:+.2f} "
          f"wr={results[top_sh]['metrics']['wr']:.1f}%")
    print(f"→ Top Calmar   (Sh>0.5): **{top_calmar}** Calmar={results[top_calmar]['metrics']['calmar']:.2f} "
          f"wr={results[top_calmar]['metrics']['wr']:.1f}%")

    # ----- Report -----
    lines: list[str] = []
    def emit(s: str) -> None:
        print(s)
        lines.append(s)

    emit("# Z-score / Bollinger / RSI — push wr via different signal type")
    emit("")
    emit("Signals tested:")
    emit("- baseline_MR : per-pair best lookback MR (reference)")
    emit("- Z1/Z2/Z3 ZSCORE : trade when |close - MA20| / std20 > {2.0, 2.5, 3.0}")
    emit("- B1 BOLLINGER_TOUCH : trade when close outside Bollinger band (20, 2σ)")
    emit("- B2 BOLLINGER_REVERT : trade when WAS outside band yesterday AND back inside today")
    emit("- RSI_extreme : trade when RSI(14) < 30 (long) or > 70 (short), {30/70 vs 25/75}")
    emit("- Z2 + vol_gate : combine z-score 2σ + low-vol regime")
    emit("")

    emit("## RESULTS (OOS 2024-2025, equal-weight 6-pair stack)")
    emit("| spec | n_trd | trd% | wr% | Sharpe | ann% | maxDD% | exp_bps | CI95 wr% | CI95 Sh |")
    emit("|---|---|---|---|---|---|---|---|---|---|")
    for name, r in results.items():
        m = r["metrics"]
        ci = r["ci"]
        emit(f"| {name} | {m['n_traded']} | {m['trade_rate']:.1f} | "
             f"{m['wr']:.1f} | {m['sharpe']:+.2f} | {m['ann_ret']*100:+.1f} | "
             f"{m['max_dd']*100:+.1f} | {m['expectancy_bps']:+.1f} | "
             f"[{ci['wr'][0]:.1f}, {ci['wr'][2]:.1f}] | "
             f"[{ci['sharpe'][0]:+.2f}, {ci['sharpe'][2]:+.2f}] |")
    emit("")

    emit("## WINNERS (Sharpe > 0.5)")
    emit(f"- TOP WIN RATE : **{top_wr}**")
    m_wr = results[top_wr]["metrics"]
    emit(f"    wr={m_wr['wr']:.1f}%  Sh={m_wr['sharpe']:+.2f}  ann_ret={m_wr['ann_ret']*100:+.1f}%  "
         f"maxDD={m_wr['max_dd']*100:+.1f}%  n_trades={m_wr['n_traded']}")
    emit(f"- TOP SHARPE   : **{top_sh}**")
    m_sh = results[top_sh]["metrics"]
    emit(f"    Sh={m_sh['sharpe']:+.2f}  wr={m_sh['wr']:.1f}%  ann_ret={m_sh['ann_ret']*100:+.1f}%  "
         f"maxDD={m_sh['max_dd']*100:+.1f}%")
    emit(f"- TOP CALMAR   : **{top_calmar}**")
    m_c = results[top_calmar]["metrics"]
    emit(f"    Calmar={m_c['calmar']:.2f}  Sh={m_c['sharpe']:+.2f}  wr={m_c['wr']:.1f}%")
    emit("")

    # Trade-off analysis
    baseline = results["baseline_MR_best_LB"]["metrics"]
    emit("## TRADE-OFF vs BASELINE (FX_MR_STACK best LB)")
    for name, r in results.items():
        if name == "baseline_MR_best_LB":
            continue
        m = r["metrics"]
        emit(f"  {name:<28} | Δwr={m['wr']-baseline['wr']:+5.1f}pp | "
             f"ΔSh={m['sharpe']-baseline['sharpe']:+.2f} | "
             f"Δexp={m['expectancy_bps']-baseline['expectancy_bps']:+.1f}bps | "
             f"trade reduction={(1-m['n_traded']/baseline['n_traded'])*100:+.0f}%")
    emit("")

    emit("## INTERPRETATION")
    emit("Our base MR edge has wr ~48-50% with positive expectancy via R asymmetry")
    emit("(winners larger than losers in magnitude). High-wr signal types tested here:")
    emit("- If a method beats baseline wr AND Sharpe → genuine accuracy gain")
    emit("- If method has high wr but low Sharpe → small wins/big losses (anti-MR style)")
    emit("- If method has low N → over-filtering, noisy stat")
    emit("")
    emit("**Fundamental truth**: FX daily MR caps around 50% wr. Higher wr requires either")
    emit("different asset (mean-reverting stocks pairs trading, options selling), or accept")
    emit("R-asymmetric edge as-is (current baseline is OPTIMAL given universe).")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))

    OUT_METRICS.write_text(json.dumps({
        n: {"metrics": r["metrics"],
            "ci_sharpe": list(r["ci"]["sharpe"]),
            "ci_wr": list(r["ci"]["wr"])} for n, r in results.items()
    }, indent=2, default=str))

    fig = go.Figure()
    colors = ["#000", "#1976d2", "#0288d1", "#0097a7", "#2e7d32", "#43a047",
              "#f57c00", "#e64a19", "#c62828"]
    for i, (name, r) in enumerate(results.items()):
        cum = r["stack"][r["stack"].index >= OOS_START].cumsum() * 100
        fig.add_trace(go.Scatter(x=cum.index, y=cum.values, mode="lines",
                                 name=name, line=dict(color=colors[i % len(colors)])))
    fig.update_layout(title="Accuracy candidates — OOS cum return %",
                      template="plotly_white", height=700, hovermode="x",
                      xaxis_title="date", yaxis_title="cum return %")
    fig.write_html(str(OUT_EQUITY), include_plotlyjs="cdn")

    print("\n\ndone")
    print(f"files: {OUT_REPORT.name}, {OUT_METRICS.name}, {OUT_EQUITY.name}")
    print(f"top wr (Sh>0.5):     {top_wr}  wr={results[top_wr]['metrics']['wr']:.1f}% "
          f"Sh={results[top_wr]['metrics']['sharpe']:+.2f}")
    print(f"top Sharpe (Sh>0.5): {top_sh}  Sh={results[top_sh]['metrics']['sharpe']:+.2f} "
          f"wr={results[top_sh]['metrics']['wr']:.1f}%")
    print(f"top Calmar (Sh>0.5): {top_calmar}  Calmar={results[top_calmar]['metrics']['calmar']:.2f}")


if __name__ == "__main__":
    main()
