"""
VolBreak BACKTEST ONLY — does FX intraday opening-range breakout have an edge?

NOT the live loop. Simulates the intraday logic on REAL m15 bars (EURUSD/GBPUSD/
USDJPY) to decide whether the family is worth the intraday infra investment.

Logic (defensible a-priori, NOT tuned to fit):
  - Session 07:00-20:00 UTC (London open -> NY).
  - Opening range = high/low of the first OR_HOURS of the session.
  - After the OR window, first bar that closes beyond the OR -> enter that side.
  - One trade/day/pair. Flat at session end (zero overnight).
  - Pessimistic round-trip cost (intraday FX, Capital-retail-like).

Judged on OOS (>=2024), few variants, real costs. No synthetic data.
"""
from __future__ import annotations

import math

import pandas as pd

DATA = __import__("pathlib").Path(__file__).parent / "data"
ANN = 252
OOS = pd.Timestamp("2024-01-01", tz="UTC")
PAIRS = {
    "EURUSD": ("eurusd-m15-bid-2019-01-01-2026-01-01.csv", 0.0001),
    "GBPUSD": ("gbpusd-m15-bid-2019-01-01-2026-01-01.csv", 0.0001),
    "USDJPY": ("usdjpy-m15-bid-2019-01-01-2026-01-01.csv", 0.01),
}
RT_COST_PIPS = 1.5  # pessimistic intraday round-trip (spread+slippage)
SESSION_START, OR_END_DEFAULT, SESSION_END = 7, 9, 20  # UTC hours


def load_m15(csv: str) -> pd.DataFrame:
    df = pd.read_csv(DATA / csv)
    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.set_index("ts").sort_index()[["open", "high", "low", "close"]].astype(float)


def daily_orb_returns(df: pd.DataFrame, pip: float, or_hours: int) -> pd.Series:
    """One ORB trade per day; flat at session end. Returns net daily returns."""
    h = df.index.hour
    sess = df[(h >= SESSION_START) & (h < SESSION_END)]
    out = {}
    or_end = SESSION_START + or_hours
    for day, g in sess.groupby(sess.index.normalize()):
        orng = g[g.index.hour < or_end]
        post = g[g.index.hour >= or_end]
        if len(orng) < 2 or len(post) < 2:
            continue
        hi, lo = orng["high"].max(), orng["low"].min()
        entry = direction = None
        for ts, row in post.iterrows():
            if row["close"] > hi:
                direction, entry = 1, row["close"]; break
            if row["close"] < lo:
                direction, entry = -1, row["close"]; break
        if entry is None:
            out[day] = 0.0; continue
        exit_px = post["close"].iloc[-1]                     # flat EOD
        gross = direction * (exit_px / entry - 1.0)
        cost = RT_COST_PIPS * pip / entry                    # round-trip cost
        out[day] = gross - cost
    return pd.Series(out)


def strat_returns(or_hours: int) -> pd.Series:
    streams = {}
    for pair, (csv, pip) in PAIRS.items():
        streams[pair] = daily_orb_returns(load_m15(csv), pip, or_hours)
    return pd.DataFrame(streams).fillna(0.0).mean(axis=1)


def vol_target(daily: pd.Series, tv: float, ml: float = 8.0) -> pd.Series:
    rv = daily.rolling(60).std() * math.sqrt(ANN)
    lev = (tv / rv).clip(upper=ml).shift(1).fillna(1.0)
    return (daily * lev).dropna()


def metrics(pl: pd.Series) -> dict:
    pl = pl.dropna()
    if len(pl) < 2:
        return {}
    mu, sd = pl.mean(), pl.std()
    cum = (1 + pl).cumprod()
    return {"n": len(pl), "trades": int((pl != 0).sum()),
            "sh": mu / sd * math.sqrt(ANN) if sd > 0 else 0.0,
            "ret": mu * ANN, "dd": float((cum / cum.cummax() - 1).min()),
            "worst": float(pl.min())}


def main() -> None:
    print(f"VolBreak ORB backtest — real m15, round-trip cost {RT_COST_PIPS} pip")
    for orh in (1, 2, 3):                       # few variants, judge OOS
        for tv in (0.05,):
            base = strat_returns(orh)
            lev = vol_target(base, tv)
            f, o = metrics(lev), metrics(lev[lev.index >= OOS])
            if not f:
                print(f"OR={orh}h: insufficient"); continue
            print(f"OR={orh}h vol{int(tv*100)} | FULL Sh={f['sh']:+.2f} ret={f['ret']*100:+5.1f}% "
                  f"DD={f['dd']*100:6.1f}% worst={f['worst']*100:+.2f}% trades={f['trades']} "
                  f"| OOS Sh={o.get('sh',0):+.2f} DD={o.get('dd',0)*100:.1f}%")


if __name__ == "__main__":
    main()
