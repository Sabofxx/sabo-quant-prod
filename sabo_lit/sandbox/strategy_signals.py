"""
Config-driven signal + delta-order generator for the parameterized runner.

Reuses the PROVEN daily price path (read data/<csv>, ms timestamp, resample to
daily close) — the same source cash.load_m5_close uses — rather than the fragile
dukascopy h1 loader. Produces the exact delta CSV the executor consumes
(columns: instrument, account_id, delta_notional_usd) in the strategy's isolated
live/ dir.

Signals (momentum family — FTMO-convex, NOT mean-reversion):
  tsm : time-series momentum. N-day return > 0 -> LONG (+1), < 0 -> SHORT (-1).
        (literally the opposite sign of the MR generator's contrarian rule.)
  ma  : trend via fast/slow MA cross of the close (lookback = slow; fast = slow//4).

Sizing: portfolio vol-target, identical pattern to the MR generator —
  leverage = clip(target_vol / realized_ann_vol, max_leverage), shifted (no
  look-ahead), applied to equal-weight signals across the strategy's instruments.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

import pandas as pd

from strategy_config import StrategyConfig

HERE = Path(__file__).parent
DATA = HERE / "data"
ANN_DAYS = 252
VOL_LOOKBACK = 60


def load_daily_close(csv_name: str) -> pd.Series:
    """Read a data/<csv> (timestamp ms, OHLC) and resample to daily close."""
    df = pd.read_csv(DATA / csv_name)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df["close"].astype(float).resample("1D").last().dropna()


def momentum_signal(close: pd.Series, lookback: int, kind: str) -> float:
    """Latest momentum signal in {+1, -1, 0} from completed daily bars (no look-ahead)."""
    hist = close.dropna()
    if len(hist) <= lookback:
        return 0.0
    if kind == "tsm":
        chg = hist.iloc[-1] / hist.iloc[-1 - lookback] - 1.0
        return 1.0 if chg > 0 else -1.0 if chg < 0 else 0.0
    if kind == "ma":
        fast = max(2, lookback // 4)
        ma_fast = hist.tail(fast).mean()
        ma_slow = hist.tail(lookback).mean()
        return 1.0 if ma_fast > ma_slow else -1.0 if ma_fast < ma_slow else 0.0
    raise ValueError(f"unknown signal kind {kind!r}")


def _strategy_daily_returns(closes: dict[str, pd.Series], cfg: StrategyConfig) -> pd.Series:
    """Equal-weight signed daily returns of the strategy (for vol-target leverage)."""
    streams = {}
    for ins in cfg.instruments:
        c = closes[ins.symbol]
        rets = c.pct_change()
        if cfg.signal == "tsm":
            cum = (1 + rets).rolling(ins.lookback).apply(lambda v: v.prod() - 1.0, raw=True)
            sig = (cum > 0).astype(float) - (cum < 0).astype(float)
        else:  # ma
            fast = max(2, ins.lookback // 4)
            sig = (c.rolling(fast).mean() > c.rolling(ins.lookback).mean()).astype(float) \
                  - (c.rolling(fast).mean() < c.rolling(ins.lookback).mean()).astype(float)
        streams[ins.symbol] = sig.shift(1) * rets  # shift -> no look-ahead
    return pd.DataFrame(streams).fillna(0.0).mean(axis=1)


def portfolio_leverage(daily_returns: pd.Series, target_vol: float, max_lev: float) -> float:
    hist = daily_returns.dropna()
    if len(hist) < 2:
        return 0.0
    realized = float(hist.tail(VOL_LOOKBACK).std() * math.sqrt(ANN_DAYS))
    if realized <= 0 or math.isnan(realized):
        return 0.0
    return min(max_lev, target_vol / realized)


def generate_delta_orders(cfg: StrategyConfig, account_equity: float) -> list[dict]:
    """Compute signed delta notional per instrument and write the delta CSV.

    Returns the list of order rows. Sizing mirrors the MR generator:
    notional = equity * leverage * (1/n) * signal.
    """
    closes = {ins.symbol: load_daily_close(ins.csv) for ins in cfg.instruments}
    daily_ret = _strategy_daily_returns(closes, cfg)
    leverage = portfolio_leverage(daily_ret, cfg.target_vol, cfg.max_leverage)
    n = len(cfg.instruments)
    weight = 1.0 / n

    rows: list[dict] = []
    for ins in cfg.instruments:
        sig = momentum_signal(closes[ins.symbol], ins.lookback, cfg.signal)
        notional = account_equity * leverage * weight * sig
        rows.append({
            "instrument": ins.symbol,
            "account_id": cfg.account_id,
            "delta_notional_usd": round(notional, 2),
            "signal": sig,
            "lookback": ins.lookback,
            "leverage": round(leverage, 4),
        })

    cfg.live_dir.mkdir(parents=True, exist_ok=True)
    out = cfg.live_dir / "prop_delta_orders_latest.csv"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["instrument", "account_id", "delta_notional_usd",
                                          "signal", "lookback", "leverage"])
        w.writeheader()
        w.writerows(rows)
    return rows
