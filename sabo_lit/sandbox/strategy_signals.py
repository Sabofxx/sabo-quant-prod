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


# FX pair -> (base ccy, quote ccy). Carry = long base earns rate[base]-rate[quote].
PAIR_CCY = {
    "EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"), "USDJPY": ("USD", "JPY"),
    "AUDUSD": ("AUD", "USD"), "NZDUSD": ("NZD", "USD"), "USDCAD": ("USD", "CAD"),
}
RATES_CSV = "g10_short_rates.csv"


def load_rates() -> dict[str, pd.Series]:
    """G10 short rates (FRED OECD 3m), monthly -> daily forward-filled per ccy."""
    df = pd.read_csv(DATA / RATES_CSV)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    out: dict[str, pd.Series] = {}
    for ccy, g in df.groupby("currency"):
        s = g.set_index("date")["rate"].sort_index()
        out[ccy] = s.resample("1D").ffill()
    return out


def carry_signal_series(close: pd.Series, pair: str, rates: dict[str, pd.Series],
                        lookback: int) -> pd.Series:
    """Carry direction (sign of rate differential) filtered by price trend.

    Only hold the carry-positive direction when the price trend agrees — this is
    what turns carry's negative-skew (unwind crashes) into a convex-ish payoff.
    """
    base, quote = PAIR_CCY[pair]
    rb = rates[base].reindex(close.index, method="ffill")
    rq = rates[quote].reindex(close.index, method="ffill")
    carry = (rb - rq)
    carry_sign = (carry > 0).astype(float) - (carry < 0).astype(float)
    rets = close.pct_change()
    cum = (1 + rets).rolling(lookback).apply(lambda v: v.prod() - 1.0, raw=True)
    trend = (cum > 0).astype(float) - (cum < 0).astype(float)
    # take carry only when trend agrees, else flat
    return carry_sign.where(carry_sign == trend, 0.0)


def load_daily_close(csv_name: str) -> pd.Series:
    """Read a data/<csv> (timestamp ms, OHLC) and resample to daily close."""
    df = pd.read_csv(DATA / csv_name)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df["close"].astype(float).resample("1D").last().dropna()


def _vol_rank(close: pd.Series, lookback_rank: int = 252) -> float:
    """Percentile rank (0..1) of current 20d realized vol within the trailing year."""
    rets = close.pct_change().dropna()
    rv = rets.rolling(20).std()
    rv_window = rv.dropna().tail(lookback_rank)
    if len(rv_window) < 30 or pd.isna(rv.iloc[-1]):
        return 0.0
    return float((rv_window <= rv.iloc[-1]).mean())


def momentum_signal(close: pd.Series, lookback: int, kind: str,
                    long_only: bool = False, vol_filter_pct: float = 0.0) -> float:
    """Latest signal in {+1, -1, 0} from completed daily bars (no look-ahead)."""
    hist = close.dropna()
    if len(hist) <= lookback:
        return 0.0
    if kind == "tsm":
        chg = hist.iloc[-1] / hist.iloc[-1 - lookback] - 1.0
        sig = 1.0 if chg > 0 else -1.0 if chg < 0 else 0.0
    elif kind == "ma":
        fast = max(2, lookback // 4)
        sig = 1.0 if hist.tail(fast).mean() > hist.tail(lookback).mean() \
            else -1.0 if hist.tail(fast).mean() < hist.tail(lookback).mean() else 0.0
    elif kind == "donchian":
        hi = hist.iloc[-lookback - 1:-1].max()
        lo = hist.iloc[-lookback - 1:-1].min()
        sig = 1.0 if hist.iloc[-1] > hi else -1.0 if hist.iloc[-1] < lo else 0.0
    else:
        raise ValueError(f"unknown signal kind {kind!r}")
    if long_only and sig < 0:
        sig = 0.0
    if vol_filter_pct and _vol_rank(hist) > vol_filter_pct:
        sig = 0.0  # high-vol regime -> stand aside (crash protection)
    return sig


def _signal_series(c: pd.Series, lookback: int, kind: str) -> pd.Series:
    rets = c.pct_change()
    if kind == "tsm":
        cum = (1 + rets).rolling(lookback).apply(lambda v: v.prod() - 1.0, raw=True)
        return (cum > 0).astype(float) - (cum < 0).astype(float)
    if kind == "ma":
        fast = max(2, lookback // 4)
        return (c.rolling(fast).mean() > c.rolling(lookback).mean()).astype(float) \
            - (c.rolling(fast).mean() < c.rolling(lookback).mean()).astype(float)
    if kind == "donchian":
        hi = c.rolling(lookback).max().shift(1)
        lo = c.rolling(lookback).min().shift(1)
        return (c > hi).astype(float) - (c < lo).astype(float)
    raise ValueError(f"unknown signal kind {kind!r}")


def _apply_filters(sig: pd.Series, c: pd.Series, cfg: StrategyConfig) -> pd.Series:
    if cfg.long_only:
        sig = sig.clip(lower=0.0)
    if cfg.vol_filter_pct:
        rv = c.pct_change().rolling(20).std()
        rank = rv.rolling(252, min_periods=30).rank(pct=True)
        sig = sig.where(rank <= cfg.vol_filter_pct, 0.0)
    return sig


def _strategy_daily_returns(closes: dict[str, pd.Series], cfg: StrategyConfig,
                            rates: dict[str, pd.Series] | None = None) -> pd.Series:
    """Equal-weight signed daily returns of the strategy (for vol-target leverage)."""
    streams = {}
    for ins in cfg.instruments:
        c = closes[ins.symbol]
        if cfg.signal == "carry":
            sig = carry_signal_series(c, ins.symbol, rates, ins.lookback)
        else:
            sig = _apply_filters(_signal_series(c, ins.lookback, cfg.signal), c, cfg)
        streams[ins.symbol] = sig.shift(1) * c.pct_change()  # shift -> no look-ahead
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
    rates = load_rates() if cfg.signal == "carry" else None
    daily_ret = _strategy_daily_returns(closes, cfg, rates)
    leverage = portfolio_leverage(daily_ret, cfg.target_vol, cfg.max_leverage)
    n = len(cfg.instruments)
    weight = 1.0 / n

    rows: list[dict] = []
    for ins in cfg.instruments:
        if cfg.signal == "carry":
            sig = float(carry_signal_series(closes[ins.symbol], ins.symbol, rates,
                                            ins.lookback).iloc[-1])
        else:
            sig = momentum_signal(closes[ins.symbol], ins.lookback, cfg.signal,
                                  long_only=cfg.long_only, vol_filter_pct=cfg.vol_filter_pct)
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
