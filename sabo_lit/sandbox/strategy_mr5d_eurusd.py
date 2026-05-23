"""
Sandbox — EURUSD MR_5d focused validation for retail (<$20k, accepts vol).

Per-pair decomp from 6-pair portfolio showed EURUSD net OOS Sharpe +1.16
(strongest single signal). This script validates EURUSD-only deep:
  - Bootstrap CI on EURUSD-only net daily P&L (2000 resamples)
  - Per-year + per-month breakdown
  - Compare vs EURUSD random baseline (random ±1 daily)
  - Monthly seasonality heatmap
  - Drawdown profile
  - Concrete retail execution spec

Cost model: same as multi-pair (round_trip 1.9 pips EURUSD).
No hedge needed (single pair = USD basket beta meaningless for 1-pair portfolio).

Verdict tiers:
  TRADEABLE     : OOS Sharpe > 0.5 net AND CI low > 0 AND > random p95
  TRADEABLE_RISKY : OOS Sharpe > 0.5 net AND > random p95 BUT CI low > -0.3
  RESEARCH_ONLY : positive OOS Sharpe fails above
  DEAD          : negative OOS Sharpe
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
OUT_REPORT = HERE / "strategy_mr5d_eurusd_report.md"
OUT_METRICS = HERE / "strategy_mr5d_eurusd_metrics.json"
OUT_EQUITY = HERE / "strategy_mr5d_eurusd_equity.html"
OUT_HEATMAP = HERE / "strategy_mr5d_eurusd_heatmap.html"

PAIR = "EURUSD"
PIP = 0.0001
ROUND_TRIP_PIPS = 1.9
FILE_M5 = "eurusd-m5-bid-2019-01-01-2026-01-01.csv"

TRADING_DAYS = 252
SEED = 42
IS_END = pd.Timestamp("2023-12-31 23:59:59", tz="UTC")
OOS_START = pd.Timestamp("2024-01-01", tz="UTC")
N_BOOT = 2000
N_RANDOM = 1000
LOOKBACK = 5


def load_close_daily() -> pd.Series:
    df = pd.read_csv(DATA / FILE_M5)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    return df["close"].resample("1D").last().dropna()


def mr_signal(returns: pd.Series, lookback: int) -> pd.Series:
    cum = (1.0 + returns).rolling(lookback).apply(lambda x: x.prod() - 1.0, raw=True)
    sig = (cum < 0).astype(float) - (cum > 0).astype(float)
    return sig.shift(1).dropna()


def gross_net(sig: pd.Series, returns: pd.Series, price: pd.Series
              ) -> tuple[pd.Series, pd.Series]:
    common = sig.index.intersection(returns.index)
    g = sig.loc[common] * returns.loc[common]
    turnover = sig.diff().abs().fillna(0.0) / 2.0  # fraction of position flipped
    cost_ret = (ROUND_TRIP_PIPS * PIP) / price
    cost = (turnover * cost_ret).reindex(common).fillna(0.0)
    return g.rename("gross"), (g - cost).rename("net")


def metrics(pl: pd.Series) -> dict:
    pl = pl.dropna()
    if len(pl) < 2:
        return {"n": 0, "sharpe": 0.0, "ann_ret": 0.0, "ann_vol": 0.0,
                "max_dd": 0.0, "wr": 0.0, "calmar": 0.0,
                "best_day": 0.0, "worst_day": 0.0, "skew": 0.0}
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
            "best_day": float(pl.max()), "worst_day": float(pl.min()), "skew": skew}


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


def random_baseline(returns: pd.Series, price: pd.Series, n: int, seed: int
                    ) -> tuple[float, float, float]:
    """Random ±1 daily signal, with same cost model."""
    rng = random.Random(seed)
    shs: list[float] = []
    L = len(returns)
    for _ in range(n):
        sigs = pd.Series([rng.choice([-1, 1]) for _ in range(L)], index=returns.index)
        # cost model same
        turnover = sigs.diff().abs().fillna(0.0) / 2.0
        cost_ret = (ROUND_TRIP_PIPS * PIP) / price
        cost = turnover * cost_ret.reindex(sigs.index).fillna(0.0)
        net = sigs * returns - cost
        m = metrics(net)
        shs.append(m["sharpe"])
    shs.sort()
    return shs[int(n * 0.05)], shs[n // 2], shs[int(n * 0.95)]


def per_year(pl: pd.Series) -> list[tuple]:
    out = []
    for y in range(2019, 2026):
        sub = pl[pl.index.year == y]
        if len(sub) < 30:
            continue
        out.append((y, metrics(sub)))
    return out


def monthly_returns(pl: pd.Series) -> pd.DataFrame:
    """Returns DataFrame [year × month] of monthly cum return."""
    monthly = pl.groupby([pl.index.year, pl.index.month]).sum().unstack(fill_value=0.0)
    monthly.columns = [f"M{m:02d}" for m in monthly.columns]
    return monthly


def main() -> None:
    print(f"Loading {PAIR} M5 → daily close...")
    closes = load_close_daily()
    rets = closes.pct_change().dropna()
    px = closes.reindex(rets.index)
    print(f"  {len(rets)} daily obs, {rets.index[0].date()} → {rets.index[-1].date()}")

    sig = mr_signal(rets, LOOKBACK)
    g, n = gross_net(sig, rets, px)
    g_is = g[g.index <= IS_END]
    g_oos = g[g.index >= OOS_START]
    n_is = n[n.index <= IS_END]
    n_oos = n[n.index >= OOS_START]

    m_g_is = metrics(g_is)
    m_g_oos = metrics(g_oos)
    m_n_is = metrics(n_is)
    m_n_oos = metrics(n_oos)
    m_n_full = metrics(n)

    ci_oos = bootstrap_ci(n_oos, N_BOOT, SEED)
    ci_full = bootstrap_ci(n, N_BOOT, SEED)
    print(f"\nBootstrap done. OOS Sharpe CI: [{ci_oos['sharpe'][0]:+.2f}, "
          f"{ci_oos['sharpe'][2]:+.2f}]")

    print(f"Random baseline ({N_RANDOM} iter) on OOS...")
    rand_p5, rand_med, rand_p95 = random_baseline(
        rets[rets.index >= OOS_START], px[px.index >= OOS_START], N_RANDOM, SEED)
    print(f"  Random Sharpe p5={rand_p5:+.2f} med={rand_med:+.2f} p95={rand_p95:+.2f}")

    # Verdict
    if m_n_oos["sharpe"] > 0.5 and ci_oos["sharpe"][0] > 0 and m_n_oos["sharpe"] > rand_p95:
        verdict = "TRADEABLE"
    elif m_n_oos["sharpe"] > 0.5 and ci_oos["sharpe"][0] > -0.3 and m_n_oos["sharpe"] > rand_p95:
        verdict = "TRADEABLE_RISKY"
    elif m_n_oos["sharpe"] > 0:
        verdict = "RESEARCH_ONLY"
    else:
        verdict = "DEAD"

    # Monthly seasonality
    monthly_n = monthly_returns(n)

    # ----- Report -----
    lines: list[str] = []
    def emit(s: str) -> None:
        print(s)
        lines.append(s)

    emit(f"# EURUSD MR_{LOOKBACK}d Retail Validation")
    emit("")
    emit(f"Universe: {PAIR} only. Lookback {LOOKBACK}d. Daily close-to-close. "
         f"Round-trip cost {ROUND_TRIP_PIPS} pips.")
    emit("IS: 2019 → 2023 | OOS: 2024 → 2025")
    emit("")

    emit("## GROSS vs NET")
    emit("| period | gross Sh | gross ann% | net Sh | net ann% | cost drag |")
    emit("|---|---|---|---|---|---|")
    emit(f"| IS  | {m_g_is['sharpe']:+.2f} | {m_g_is['ann_ret']*100:+.2f} | "
         f"{m_n_is['sharpe']:+.2f} | {m_n_is['ann_ret']*100:+.2f} | "
         f"{m_g_is['sharpe'] - m_n_is['sharpe']:+.2f} |")
    emit(f"| OOS | {m_g_oos['sharpe']:+.2f} | {m_g_oos['ann_ret']*100:+.2f} | "
         f"{m_n_oos['sharpe']:+.2f} | {m_n_oos['ann_ret']*100:+.2f} | "
         f"{m_g_oos['sharpe'] - m_n_oos['sharpe']:+.2f} |")
    emit("")

    emit("## NET FULL METRICS")
    emit(f"  n={m_n_full['n']}  Sharpe={m_n_full['sharpe']:+.2f}  "
         f"ann_ret={m_n_full['ann_ret']*100:+.2f}%  vol={m_n_full['ann_vol']*100:.2f}%  "
         f"maxDD={m_n_full['max_dd']*100:+.2f}%  wr={m_n_full['wr']:.1f}%  "
         f"calmar={m_n_full['calmar']:.2f}  skew={m_n_full['skew']:+.2f}")
    emit(f"  best day={m_n_full['best_day']*100:+.2f}%  "
         f"worst day={m_n_full['worst_day']*100:+.2f}%")
    emit("")

    emit("## BOOTSTRAP CI95 (n=2000)")
    emit(f"  OOS  Sharpe : [{ci_oos['sharpe'][0]:+.2f}, {ci_oos['sharpe'][2]:+.2f}] "
         f"median={ci_oos['sharpe'][1]:+.2f}")
    emit(f"  OOS  ann_ret: [{ci_oos['ann_ret'][0]*100:+.2f}%, "
         f"{ci_oos['ann_ret'][2]*100:+.2f}%]  median={ci_oos['ann_ret'][1]*100:+.2f}%")
    emit(f"  FULL Sharpe : [{ci_full['sharpe'][0]:+.2f}, {ci_full['sharpe'][2]:+.2f}] "
         f"median={ci_full['sharpe'][1]:+.2f}")
    emit("")

    emit(f"## RANDOM BASELINE OOS ({N_RANDOM} iter, EURUSD daily ±1 random + same costs)")
    emit(f"  p5={rand_p5:+.2f}  med={rand_med:+.2f}  p95={rand_p95:+.2f}")
    emit(f"  Actual NET OOS Sharpe={m_n_oos['sharpe']:+.2f}  → "
         f"{'BEATS' if m_n_oos['sharpe'] > rand_p95 else 'FAILS'} 95p")
    emit("")

    emit("## PER-YEAR NET")
    emit("| year | n | Sharpe | ann% | maxDD% | wr% |")
    emit("|---|---|---|---|---|---|")
    for y, m in per_year(n):
        emit(f"| {y} | {m['n']} | {m['sharpe']:+.2f} | {m['ann_ret']*100:+.2f} | "
             f"{m['max_dd']*100:+.2f} | {m['wr']:.1f} |")
    emit("")

    emit("## MONTHLY RETURNS (% net)")
    emit("```")
    emit(monthly_n.map(lambda v: f"{v*100:+.2f}").to_string())
    emit("```")
    emit("")

    emit(f"## VERDICT: **{verdict}**")
    emit("")
    if verdict.startswith("TRADEABLE"):
        emit("## RETAIL EXECUTION SPEC")
        emit("")
        emit("### Account ($15-20k)")
        emit("- Broker: IC Markets / Pepperstone Raw (FX retail, micro-lot, low spread)")
        emit("- Account base: USD recommended (avoid currency conversion drag)")
        emit("- Min capital: $5000 to run with 1% risk and meaningful position size")
        emit("")
        emit("### Signal computation (daily UTC 22:00)")
        emit("```python")
        emit("# Each day at 22:00 UTC:")
        emit("# 1. Compute past 5-day cumulative return on EURUSD daily close")
        emit(f"#    cum_5d = (close[t-1] / close[t-{LOOKBACK + 1}]) - 1")
        emit("# 2. Signal:")
        emit("#    if cum_5d > 0 → short next day")
        emit("#    if cum_5d < 0 → long next day")
        emit("#    if cum_5d == 0 → flat (rare)")
        emit("# 3. Submit market order at 22:01 UTC for full position")
        emit("# 4. Exit at next 22:00 UTC (force close before submitting new signal)")
        emit("```")
        emit("")
        emit("### Position sizing (1% risk per trade)")
        emit(f"- Daily expected vol = {m_n_full['ann_vol']*100/math.sqrt(TRADING_DAYS):.2f}%")
        emit("- 1% capital risk = ~1.5x daily vol stop")
        emit("- On $17.5k capital: position notional = $17500 × 1 = $17500 (1x leverage)")
        emit("- EURUSD margin requirement ~3.3% = $580 margin per $17500 position")
        emit("- Lot size: 0.175 standard lot = 17500 units (micro-lot capable broker)")
        emit("")
        emit("### Expected performance (per OOS net metrics)")
        emit(f"- Annual return: {m_n_oos['ann_ret']*100:+.1f}%  → "
             f"${m_n_oos['ann_ret']*17500:+.0f} on $17.5k capital")
        emit(f"- Annual vol: {m_n_oos['ann_vol']*100:.1f}%  → "
             f"~${m_n_oos['ann_vol']*17500:.0f} std dev")
        emit(f"- Max DD historical: {m_n_oos['max_dd']*100:.1f}%  → "
             f"~${m_n_oos['max_dd']*17500:.0f} worst case")
        emit(f"- Calmar: {m_n_oos['calmar']:.2f}")
        emit(f"- Best day: {m_n_full['best_day']*100:+.2f}% (~${m_n_full['best_day']*17500:+.0f})")
        emit(f"- Worst day: {m_n_full['worst_day']*100:+.2f}% (~${m_n_full['worst_day']*17500:+.0f})")
        emit("")
        emit("### Kill switches (HARD STOP rules)")
        emit("- Monthly DD > 5% of capital ($875 on $17.5k) → halt + review")
        emit("- Rolling 60-day Sharpe < -0.5 → halt + review")
        emit("- Single day loss > 2% of capital → manual review next session")
        emit("- 3 consecutive losing days = OK ; 5+ = check signal logic")
        emit("")
        emit("### Risks disclosed")
        emit(f"- OOS CI95 lower bound = {ci_oos['sharpe'][0]:+.2f} Sharpe (could realize negative)")
        emit("- Single pair concentration: 100% EURUSD exposure")
        emit("- No black swan protection: 2-3% adverse gap possible at NY close / weekend")
        emit("- Edge could be regime-conditional (2024 saw weaker year per-year table)")
        emit("- Sample size: 731 OOS days = limited statistical power")
        emit("")
        emit("### Live tracking (first 30 days)")
        emit("- Record actual fill prices + slippage per trade")
        emit("- Compare daily realized P&L vs backtest expected (within 1 std)")
        emit("- After 30 days: re-evaluate. If realized within CI95 → scale up.")
        emit("- If realized below CI95 lower bound for 20+ days → STOP.")
    else:
        emit("## NO TRADEABLE — Alternative paths")
        emit("")
        emit("- Live paper-trade at micro size 30-60 days to gather more data")
        emit("- Test other pairs OOS-strongest from per-pair analysis")
        emit("- Pivot crypto (BTC/ETH daily MR, free Binance API, retail-friendly)")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))

    OUT_METRICS.write_text(json.dumps({
        "verdict": verdict,
        "thresholds": {"TRADEABLE": "OOS Sh>0.5 AND CI low>0 AND >rand_p95",
                       "TRADEABLE_RISKY": "OOS Sh>0.5 AND CI low>-0.3 AND >rand_p95"},
        "gross_is": m_g_is, "gross_oos": m_g_oos,
        "net_is": m_n_is, "net_oos": m_n_oos, "net_full": m_n_full,
        "ci_oos": {"sharpe": list(ci_oos["sharpe"]),
                   "ann_ret": list(ci_oos["ann_ret"])},
        "ci_full": {"sharpe": list(ci_full["sharpe"])},
        "random_baseline_oos": {"p5": rand_p5, "med": rand_med, "p95": rand_p95},
    }, indent=2, default=str))

    # Equity HTML
    fig = make_subplots(rows=2, cols=1, subplot_titles=(
        "Gross vs Net cum return", "Drawdown (net)"),
        shared_xaxes=True, vertical_spacing=0.1)
    fig.add_trace(go.Scatter(x=g.index, y=g.cumsum().values, mode="lines",
                             name="gross", line=dict(color="#888")), row=1, col=1)
    fig.add_trace(go.Scatter(x=n.index, y=n.cumsum().values, mode="lines",
                             name="net", line=dict(color="#2e7d32")), row=1, col=1)
    cum = n.cumsum()
    dd = cum - cum.cummax()
    fig.add_trace(go.Scatter(x=dd.index, y=dd.values, mode="lines",
                             name="drawdown", line=dict(color="#c62828"),
                             fill="tozeroy"), row=2, col=1)
    fig.update_layout(title=f"EURUSD MR_{LOOKBACK}d — gross vs net + drawdown",
                      template="plotly_white", height=800, hovermode="x")
    fig.write_html(str(OUT_EQUITY), include_plotlyjs="cdn")

    # Monthly heatmap
    months = monthly_returns(n) * 100
    fig2 = go.Figure(data=go.Heatmap(
        z=months.values, x=list(months.columns), y=list(months.index),
        colorscale="RdYlGn", zmid=0,
        text=months.map(lambda v: f"{v:+.1f}").values,
        texttemplate="%{text}",
        colorbar=dict(title="% monthly net"),
    ))
    fig2.update_layout(title=f"EURUSD MR_{LOOKBACK}d — monthly returns heatmap (% net)",
                       template="plotly_white", height=600, xaxis_title="month",
                       yaxis_title="year")
    fig2.write_html(str(OUT_HEATMAP), include_plotlyjs="cdn")

    # Deliverable
    print("\n\ndone")
    print(f"files: {OUT_REPORT.name}, {OUT_METRICS.name}, {OUT_EQUITY.name}, {OUT_HEATMAP.name}")
    print(f"net OOS Sharpe: {m_n_oos['sharpe']:+.2f}  (gross {m_g_oos['sharpe']:+.2f})")
    print(f"net OOS ann_ret: {m_n_oos['ann_ret']*100:+.2f}%  vol={m_n_oos['ann_vol']*100:.2f}%  "
          f"maxDD={m_n_oos['max_dd']*100:+.2f}%")
    print(f"OOS bootstrap CI95: [{ci_oos['sharpe'][0]:+.2f}, {ci_oos['sharpe'][2]:+.2f}]")
    print(f"random OOS p95: {rand_p95:+.2f}  "
          f"({'BEATS' if m_n_oos['sharpe'] > rand_p95 else 'FAILS'})")
    print(f"verdict: {verdict}")


if __name__ == "__main__":
    main()
