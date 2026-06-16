"""
Walk-forward backtest for a config-driven strategy (daily families).

    python backtest_strategy.py fxtsm gold index oil carry

Reuses strategy_signals (same signal + sizing path as the live runner) so the
backtest and live cannot diverge. Vol-targets the strategy returns and reports
full-sample + OOS (>=2024) Sharpe / maxDD / worst day / ann ret / #trade days.
All on REAL data already in data/.
"""
from __future__ import annotations

import math
import sys

import pandas as pd

from strategy_config import load_config
from strategy_signals import (_strategy_daily_returns, load_daily_close,
                              load_rates)

ANN = 252
OOS = pd.Timestamp("2024-01-01", tz="UTC")


def vol_target(daily: pd.Series, target_vol: float, max_lev: float) -> pd.Series:
    realized = daily.rolling(60).std() * math.sqrt(ANN)
    lev = (target_vol / realized).clip(upper=max_lev).shift(1).fillna(1.0)
    return (daily * lev).dropna()


def metrics(pl: pd.Series) -> dict:
    pl = pl.dropna()
    if len(pl) < 2:
        return {}
    mu, sd = pl.mean(), pl.std()
    cum = (1 + pl).cumprod()
    dd = float((cum / cum.cummax() - 1).min())
    return {"n": len(pl), "trades": int((pl != 0).sum()),
            "sharpe": mu / sd * math.sqrt(ANN) if sd > 0 else 0.0,
            "ret": mu * ANN, "maxdd": dd, "worst": float(pl.min())}


def run(name: str) -> None:
    cfg = load_config(name)
    closes = {ins.symbol: load_daily_close(ins.csv) for ins in cfg.instruments}
    rates = load_rates() if cfg.signal == "carry" else None
    daily = _strategy_daily_returns(closes, cfg, rates)
    lev = vol_target(daily, cfg.target_vol, cfg.max_leverage)
    f, o = metrics(lev), metrics(lev[lev.index >= OOS])
    if not f:
        print(f"{name:8} INSUFFICIENT DATA"); return
    print(f"{name:8} | FULL Sh={f['sharpe']:+.2f} ret={f['ret']*100:+5.1f}% "
          f"maxDD={f['maxdd']*100:6.1f}% worst={f['worst']*100:+.2f}% n={f['n']} "
          f"| OOS Sh={o.get('sharpe',0):+.2f} maxDD={o.get('maxdd',0)*100:.1f}%"
          if o else f"{name:8} | FULL Sh={f['sharpe']:+.2f}")


def main() -> None:
    names = sys.argv[1:] or ["fxtsm", "gold", "index", "oil", "carry"]
    for n in names:
        try:
            run(n)
        except Exception as e:
            print(f"{n:8} ERROR: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
