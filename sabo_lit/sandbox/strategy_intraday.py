"""
Sandbox — intraday FX strategies on M5 data, 6 pairs.

Tests 3 well-documented intraday/session FX phenomena :
  - GAP_REV   : overnight gap reversion. At 08:00 UTC compute return from prev
                21:00 UTC. Take opposite side, hold to 17:00 UTC.
  - LDN_BO    : London open breakout. Range 07:00-08:00 UTC. Above high = long ;
                below low = short. Exit at 17:00 UTC or stop at range midpoint.
  - SES_MOM   : Session momentum. If Asia session (22:00 prev → 07:00) had return,
                continue same direction during London (08:00-16:00 UTC).

Same hedge stack: USD-basket beta IS-fitted ; same verdict thresholds.

Sample: trades per pair per day = 1 max. ~250 trading days/year × 6 pairs = 1500/year.

Ambiguity:
  - "Day" defined by UTC. Friday close = 21:00 UTC (FX usual). Weekend skipped.
  - GAP_REV uses 8h hold (08-17 UTC London-NY window). Conservative.
  - LDN_BO uses 1-hour pre-London range. Standard ICT/ORB definition.
  - SES_MOM Asia = 22:00-07:00 (9h). London = 08:00-16:00 (8h).
  - Position sizing: equal weight 1/6 per pair per signal day
  - No costs (added in v2 if any spec ROBUST)
  - Stop/take-profit: NONE in v1. Pure session-aligned exit. Avoid optim bias.
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
OUT_REPORT = HERE / "strategy_intraday_report.md"
OUT_METRICS = HERE / "strategy_intraday_metrics.json"
OUT_EQUITY = HERE / "strategy_intraday_equity.html"

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


# =====================================================================
# IO
# =====================================================================
def load_m5(pair: str) -> pd.DataFrame:
    df = pd.read_csv(DATA / FILES_M5[pair])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    return df[~df.index.duplicated(keep="first")]


# =====================================================================
# Strategy P&L generators (one daily return per pair)
# =====================================================================
def gap_rev_pl(m5: pd.DataFrame) -> pd.Series:
    """For each UTC date D, compute:
        gap = close@D 08:00 / close@(D-1) 21:00 - 1
        if gap > 0 → short ; gap < 0 → long
        hold from 08:00 to 17:00 ; pnl = -sign(gap) × return(08:00 → 17:00)
       Returns: Series of daily P&L indexed by date."""
    closes = m5["close"]
    out = {}
    dates = sorted(set(closes.index.date))
    by_date = {d: g for d, g in closes.groupby(closes.index.date)}
    for i in range(1, len(dates)):
        d_prev = dates[i - 1]
        d = dates[i]
        prev_day = by_date.get(d_prev)
        cur_day = by_date.get(d)
        if prev_day is None or cur_day is None:
            continue
        # close@(D-1) 21:00
        c_21 = _bar_close_at(prev_day, 21, 0)
        c_08 = _bar_close_at(cur_day, 8, 0)
        c_17 = _bar_close_at(cur_day, 17, 0)
        if c_21 is None or c_08 is None or c_17 is None:
            continue
        gap = (c_08 - c_21) / c_21
        if gap == 0:
            continue
        direction = -1.0 if gap > 0 else +1.0  # reversion
        intraday_ret = (c_17 - c_08) / c_08
        out[pd.Timestamp(d, tz="UTC")] = direction * intraday_ret
    return pd.Series(out).sort_index()


def ldn_bo_pl(m5: pd.DataFrame) -> pd.Series:
    """London opening range breakout. Range 07:00-08:00 UTC (high/low).
    During 08:00-17:00 UTC, first close beyond high → long ; below low → short.
    Exit at 17:00 UTC."""
    out = {}
    dates = sorted(set(m5.index.date))
    by_date = {d: g for d, g in m5.groupby(m5.index.date)}
    for d in dates:
        day = by_date.get(d)
        if day is None:
            continue
        rng_window = day.between_time("07:00", "07:55")
        if rng_window.empty:
            continue
        rng_high = float(rng_window["high"].max())
        rng_low = float(rng_window["low"].min())
        session = day.between_time("08:00", "16:55")
        if session.empty:
            continue
        direction = 0
        entry_price = None
        for _, c in session.iterrows():
            if c["close"] > rng_high:
                direction = +1
                entry_price = c["close"]
                break
            if c["close"] < rng_low:
                direction = -1
                entry_price = c["close"]
                break
        if direction == 0 or entry_price is None:
            continue
        c_17 = _bar_close_at(day, 17, 0)
        if c_17 is None:
            continue
        ret = (c_17 - entry_price) / entry_price
        out[pd.Timestamp(d, tz="UTC")] = direction * ret
    return pd.Series(out).sort_index()


def ses_mom_pl(m5: pd.DataFrame) -> pd.Series:
    """Session momentum. Asia 22:00 prev → 07:00 cur. London 08:00 → 16:00 cur.
    Long London if Asia return > 0 ; short if < 0."""
    closes = m5["close"]
    out = {}
    dates = sorted(set(closes.index.date))
    by_date = {d: g for d, g in closes.groupby(closes.index.date)}
    for i in range(1, len(dates)):
        d_prev = dates[i - 1]
        d = dates[i]
        prev_day = by_date.get(d_prev)
        cur_day = by_date.get(d)
        if prev_day is None or cur_day is None:
            continue
        c_22 = _bar_close_at(prev_day, 22, 0)
        c_07 = _bar_close_at(cur_day, 7, 0)
        c_08 = _bar_close_at(cur_day, 8, 0)
        c_16 = _bar_close_at(cur_day, 16, 0)
        if any(c is None for c in (c_22, c_07, c_08, c_16)):
            continue
        asia_ret = (c_07 - c_22) / c_22
        if asia_ret == 0:
            continue
        direction = +1.0 if asia_ret > 0 else -1.0
        ldn_ret = (c_16 - c_08) / c_08
        out[pd.Timestamp(d, tz="UTC")] = direction * ldn_ret
    return pd.Series(out).sort_index()


def _bar_close_at(day_obj, hour: int, minute: int) -> float | None:
    """Closest M5 bar close at-or-after (hour:minute) UTC for this day. None if absent.
    Accepts DataFrame (use 'close' col) or Series."""
    sub = day_obj.between_time(f"{hour:02d}:{minute:02d}", f"{hour:02d}:{minute + 4:02d}")
    if sub.empty:
        return None
    if isinstance(sub, pd.DataFrame):
        return float(sub["close"].iloc[0])
    return float(sub.iloc[0])


# =====================================================================
# Portfolio + hedge
# =====================================================================
def portfolio_pl(per_pair_pl: dict) -> pd.Series:
    df = pd.DataFrame(per_pair_pl)
    df = df.fillna(0.0)
    return df.mean(axis=1).rename("portfolio_pl")


def usd_basket_from_close_returns(closes_daily: dict) -> pd.Series:
    rets = {p: closes_daily[p].pct_change() for p in PAIRS}
    df = pd.DataFrame(rets).dropna()
    parts = pd.DataFrame({p: USD_DIR[p] * df[p] for p in PAIRS})
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
    dd = float((cum - cum.cummax()).min())
    return {"n": len(pl), "sharpe": sharpe, "ann_ret": mean * TRADING_DAYS,
            "ann_vol": std * math.sqrt(TRADING_DAYS), "max_dd": dd,
            "wr": float((pl > 0).mean() * 100),
            "calmar": (mean * TRADING_DAYS) / abs(dd) if dd < 0 else float("inf")}


def bootstrap_sharpe(pl: pd.Series, n_resample: int, seed: int) -> tuple[float, float, float]:
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


def random_baseline_pl(per_pair_pl: dict, n_iter: int, seed: int) -> tuple[float, float, float]:
    """For each pair, shuffle signs of daily P&L randomly. Mean across pairs = random portfolio."""
    rng = random.Random(seed)
    sharpes: list[float] = []
    df = pd.DataFrame(per_pair_pl).fillna(0.0)
    for _ in range(n_iter):
        shuffled = df.copy()
        for col in df.columns:
            shuffled[col] = [v * (1 if rng.random() < 0.5 else -1) for v in df[col].to_list()]
        portfolio = shuffled.mean(axis=1)
        m = metrics(portfolio)
        sharpes.append(m["sharpe"])
    sharpes.sort()
    return sharpes[int(n_iter * 0.05)], sharpes[n_iter // 2], sharpes[int(n_iter * 0.95)]


# =====================================================================
# Main
# =====================================================================
def run_one_strategy(name: str, sig_fn, m5_per_pair: dict, basket: pd.Series) -> dict:
    print(f"\n=== {name} ===")
    per_pair_pl: dict = {}
    for p in PAIRS:
        pl = sig_fn(m5_per_pair[p])
        per_pair_pl[p] = pl
        print(f"  [{p}] {name}: n={len(pl)} mean={pl.mean()*1e4:+.2f}bps wr={(pl > 0).mean()*100:.1f}%")
    portfolio = portfolio_pl(per_pair_pl)
    is_mask = portfolio.index <= IS_END
    pl_is_unh = portfolio[is_mask]
    basket_is = basket.loc[basket.index.intersection(pl_is_unh.index)]
    beta = fit_beta(pl_is_unh, basket_is)
    portfolio_h = hedge(portfolio, basket, beta)
    pl_h_is = portfolio_h[portfolio_h.index <= IS_END]
    pl_h_oos = portfolio_h[portfolio_h.index >= OOS_START]
    m_is = metrics(pl_h_is)
    m_oos = metrics(pl_h_oos)
    m_full = metrics(portfolio_h)
    ci_lo, ci_med, ci_hi = bootstrap_sharpe(pl_h_oos, N_BOOT, SEED)
    r_p5, r_med, r_p95 = random_baseline_pl(per_pair_pl, N_RANDOM, SEED + hash(name) % 1000)
    if m_oos["sharpe"] > 0.5 and m_oos["sharpe"] > r_p95 and ci_lo > 0:
        verdict = "ROBUST"
    elif m_oos["sharpe"] > 0:
        verdict = "WEAK"
    else:
        verdict = "DEAD"
    return {
        "name": name, "beta": beta,
        "is": m_is, "oos": m_oos, "full": m_full,
        "ci_oos": (ci_lo, ci_med, ci_hi),
        "random_p5p95": (r_p5, r_med, r_p95),
        "verdict": verdict, "pl_h_full": portfolio_h,
    }


def main() -> None:
    print("Loading M5 data for 6 pairs (heavy: ~6 × 35MB)...")
    m5_per_pair: dict = {}
    closes_daily: dict = {}
    for p in PAIRS:
        m5 = load_m5(p)
        m5_per_pair[p] = m5
        closes_daily[p] = m5["close"].resample("1D").last().dropna()
        print(f"  {p}: M5 bars={len(m5)}")
    basket = usd_basket_from_close_returns(closes_daily)

    SPECS = {
        "GAP_REV": gap_rev_pl,
        "LDN_BO":  ldn_bo_pl,
        "SES_MOM": ses_mom_pl,
    }
    results: dict = {}
    for name, fn in SPECS.items():
        results[name] = run_one_strategy(name, fn, m5_per_pair, basket)

    # ----- Report -----
    lines: list[str] = []
    def emit(s: str) -> None:
        print(s)
        lines.append(s)

    emit("# Intraday FX Strategies — 6 pairs M5, USD-basket hedged")
    emit("")
    emit("IS: 2019-2023 | OOS: 2024-2025. Same hedge stack as daily.")
    emit("")
    emit("## COMPARATIVE")
    emit("| spec     | IS Sh | OOS Sh | OOS ann_ret% | OOS maxDD% | rand p95 | OOS CI95 | verdict |")
    emit("|---|---|---|---|---|---|---|---|")
    for name, r in results.items():
        emit(f"| {name:<7} | {r['is']['sharpe']:+.2f} | {r['oos']['sharpe']:+.2f} | "
             f"{r['oos']['ann_ret']*100:+.2f} | {r['oos']['max_dd']*100:+.2f} | "
             f"{r['random_p5p95'][2]:+.2f} | "
             f"[{r['ci_oos'][0]:+.2f}, {r['ci_oos'][2]:+.2f}] | {r['verdict']} |")
    emit("")
    for name, r in results.items():
        emit(f"## {name}")
        emit(f"  hedge_beta (IS): {r['beta']:+.3f}")
        emit(f"  IS  hedged: {r['is']}")
        emit(f"  OOS hedged: {r['oos']}")
        emit(f"  FULL hedged: {r['full']}")
        emit(f"  OOS bootstrap CI95: [{r['ci_oos'][0]:+.2f}, {r['ci_oos'][2]:+.2f}] "
             f"median={r['ci_oos'][1]:+.2f}")
        emit(f"  Random baseline: p5={r['random_p5p95'][0]:+.2f} "
             f"med={r['random_p5p95'][1]:+.2f} p95={r['random_p5p95'][2]:+.2f}")
        emit("")

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
    elif weak:
        best_weak = max(weak, key=lambda n: results[n]["oos"]["sharpe"])
        emit(f"  Best WEAK: {best_weak} (OOS Sharpe {results[best_weak]['oos']['sharpe']:+.2f})")
    else:
        emit("  ALL DEAD on intraday too.")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))

    persist = {}
    for name, r in results.items():
        persist[name] = {
            "beta": r["beta"], "verdict": r["verdict"],
            "is": r["is"], "oos": r["oos"], "full": r["full"],
            "ci_oos": list(r["ci_oos"]),
            "random_p5p95": list(r["random_p5p95"]),
        }
    OUT_METRICS.write_text(json.dumps(persist, indent=2, default=str))

    fig = go.Figure()
    for name, r in results.items():
        cum = r["pl_h_full"].cumsum()
        fig.add_trace(go.Scatter(x=cum.index, y=cum.values, mode="lines", name=name))
    fig.update_layout(title="Intraday FX — hedged cum returns",
                      template="plotly_white", height=700, hovermode="x",
                      xaxis_title="date", yaxis_title="cum return")
    fig.write_html(str(OUT_EQUITY), include_plotlyjs="cdn")

    print("\n\ndone")
    print(f"files: {OUT_REPORT.name}, {OUT_METRICS.name}, {OUT_EQUITY.name}")
    print("verdicts: " + ", ".join(f"{n}={r['verdict']}" for n, r in results.items()))


if __name__ == "__main__":
    main()
