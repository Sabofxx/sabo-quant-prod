"""
Research backtest — families 1/2/3 (managed-futures trend, sector X-mom, stock X-mom).
REAL data (yfinance, data/F{1,2,3}_*.csv). OOS-judged, pessimistic costs. No synthetic.
NOT production — only configs for what passes.
"""
from __future__ import annotations
import math
import pandas as pd

DATA = __import__("pathlib").Path(__file__).parent / "data"
ANN = 252
OOS = pd.Timestamp("2024-01-01")


def metrics(pl: pd.Series) -> dict:
    pl = pl.dropna()
    if len(pl) < 2:
        return {}
    mu, sd = pl.mean(), pl.std()
    cum = (1 + pl).cumprod()
    return {"sh": mu / sd * math.sqrt(ANN) if sd > 0 else 0.0,
            "ret": mu * ANN, "dd": float((cum / cum.cummax() - 1).min()),
            "worst": float(pl.min())}


def show(tag: str, pl: pd.Series, turnover: float | None = None):
    f = metrics(pl); o = metrics(pl[pl.index >= OOS])
    if not f:
        print(f"{tag:34} insufficient"); return
    extra = f" turn={turnover:.1f}x/yr" if turnover is not None else ""
    print(f"{tag:34} FULL Sh={f['sh']:+.2f} ret={f['ret']*100:+5.1f}% DD={f['dd']*100:6.1f}% "
          f"worst={f['worst']*100:+.2f}% | OOS Sh={o['sh']:+.2f} DD={o['dd']*100:6.1f}%{extra}")


def load(name: str) -> pd.DataFrame:
    df = pd.read_csv(DATA / name, index_col=0, parse_dates=True).sort_index()
    return df.astype(float)


# ---------- Family 1: managed-futures diversified trend ----------
def family1(lookback: int, cost_bps: float, target_vol: float = 0.10) -> tuple[pd.Series, float]:
    px = load("F1_futures.csv")
    per_market = {}
    turnovers = []
    for col in px.columns:
        c = px[col].dropna()
        rets = c.pct_change()
        sig = (c / c.shift(lookback) - 1.0)
        sig = (sig > 0).astype(float) - (sig < 0).astype(float)
        sig = sig.shift(1)
        rv = rets.rolling(60).std()
        unit = (target_vol / math.sqrt(ANN)) / rv.replace(0, pd.NA)          # equal-vol per market
        unit = unit.clip(upper=10).shift(1)
        pos = (sig * unit).fillna(0.0)
        turn = pos.diff().abs().fillna(0.0)
        turnovers.append(turn.mean() * ANN)
        per_market[col] = (pos * rets - turn * cost_bps / 1e4)
    port = pd.DataFrame(per_market).fillna(0.0).mean(axis=1)
    return port, sum(turnovers) / len(turnovers)


# ---------- cross-sectional momentum (sectors / stocks) ----------
def xsec_momentum(name: str, lookback: int, skip: int, top_frac: float,
                  cost_bps: float) -> tuple[pd.Series, float]:
    px = load(name)
    rets = px.pct_change()
    mom = px.shift(skip) / px.shift(skip + lookback) - 1.0          # 12-1 style momentum
    month_ends = px.resample("ME").last().index
    weights = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    prev = pd.Series(0.0, index=px.columns)
    turn_events = []
    for me in month_ends:
        m = mom.loc[:me].iloc[-1].dropna()
        if len(m) < 5:
            continue
        k = max(1, int(len(m) * top_frac))
        winners = m.nlargest(k).index
        w = pd.Series(0.0, index=px.columns); w[winners] = 1.0 / k
        turn_events.append((w - prev).abs().sum())
        prev = w
        weights.loc[weights.index >= me] = w.values
    weights = weights.shift(1).fillna(0.0)                          # trade next day
    gross = (weights * rets).sum(axis=1)
    # monthly rebalance cost spread over the period
    n_months = max(1, len(turn_events))
    cost_total = sum(turn_events) * cost_bps / 1e4
    cost_daily = pd.Series(0.0, index=px.index)
    for me in month_ends:
        idx = px.index[px.index >= me]
        if len(idx):
            pass
    # apply cost at each rebalance day
    cost_series = pd.Series(0.0, index=px.index)
    ev = iter(turn_events)
    for me in month_ends:
        days = px.index[px.index >= me]
        if len(days) == 0:
            continue
        try:
            t = next(ev)
        except StopIteration:
            break
        cost_series.loc[days[0]] += t * cost_bps / 1e4
    net = gross - cost_series.reindex(gross.index).fillna(0.0)
    turn_per_yr = sum(turn_events) / (len(month_ends) / 12)
    return net, turn_per_yr


print("=== Family 1: managed-futures diversified trend (10 markets, equal-vol) ===")
for lb in (126, 252):
    pl, turn = family1(lb, cost_bps=2.0)
    show(f"F1 trend lb{lb} cost2bps", pl, turn)

print("\n=== Family 2: sector cross-sectional momentum (top 4 of 11, monthly) ===")
for lb in (126, 252):
    pl, turn = xsec_momentum("F2_sectors.csv", lb, skip=21, top_frac=4/11, cost_bps=3.0)
    show(f"F2 sect lb{lb} cost3bps", pl, turn)

print("\n=== Family 3: stock cross-sectional momentum (top 20% of 40, monthly) ===")
for lb in (126, 252):
    for cost in (5.0, 10.0):
        pl, turn = xsec_momentum("F3_stocks.csv", lb, skip=21, top_frac=0.20, cost_bps=cost)
        show(f"F3 stock lb{lb} cost{int(cost)}bps", pl, turn)
