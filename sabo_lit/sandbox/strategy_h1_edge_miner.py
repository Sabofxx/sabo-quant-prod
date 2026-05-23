"""
Sandbox - H1 cross-asset edge miner for prop-firm strategy discovery.

Uses Dukascopy H1 bid/ask files downloaded locally. Costs are embedded by
pricing long entries at ask / exits at bid, and short entries at bid / exits at
ask. The goal is not a pretty standalone backtest; it is to identify liquid
cross-asset edges that can improve prop-firm pass speed without breaching daily
loss limits.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


HERE = Path(__file__).parent
DATA_DIR = HERE / "data" / "dukascopy_research_full_h1_2012-01-01_2026-01-01"
OUT_REPORT = HERE / "strategy_h1_edge_miner_report.md"
OUT_METRICS = HERE / "strategy_h1_edge_miner_metrics.json"

IS_END = pd.Timestamp("2022-12-31 23:59:59", tz="UTC")
VAL_START = pd.Timestamp("2023-01-01", tz="UTC")
VAL_END = pd.Timestamp("2023-12-31 23:59:59", tz="UTC")
OOS_START = pd.Timestamp("2024-01-01", tz="UTC")
ANN_DAYS = 252
VOL_LOOKBACK = 60
MAX_LEV = 12.0
MIN_DAILY_OBS = 600
SEED = 20260522

SYMBOLS = [
    "eurusd", "gbpusd", "usdjpy", "usdchf", "audusd", "usdcad", "nzdusd",
    "gbpjpy", "eurjpy", "audjpy", "cadjpy", "chfjpy",
    "eurgbp", "eurcad", "euraud",
    "xauusd", "xagusd",
    "usatechidxusd", "usa30idxusd", "usa500idxusd", "deuidxeur",
    "lightcmdusd", "brentcmdusd",
]


@dataclass(frozen=True)
class Candidate:
    name: str
    symbol: str
    family: str
    daily: pd.Series


def read_side(symbol: str, side: str) -> pd.DataFrame:
    symbol_dir = DATA_DIR / symbol / side
    if not symbol_dir.exists():
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    for path in sorted(symbol_dir.glob("*.csv")):
        if path.stat().st_size == 0:
            continue
        df = pd.read_csv(path)
        if df.empty:
            continue
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("timestamp").sort_index()
        frames.append(df[["open", "high", "low", "close"]].astype(float))

    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames).sort_index()
    return out[~out.index.duplicated(keep="last")]


def load_symbol(symbol: str) -> pd.DataFrame:
    bid = read_side(symbol, "bid")
    ask = read_side(symbol, "ask")
    if bid.empty or ask.empty:
        return pd.DataFrame()

    idx = bid.index.intersection(ask.index)
    bid = bid.loc[idx]
    ask = ask.loc[idx]
    out = pd.DataFrame(index=idx)
    for column in ("open", "high", "low", "close"):
        out[f"bid_{column}"] = bid[column]
        out[f"ask_{column}"] = ask[column]
        out[f"mid_{column}"] = (bid[column] + ask[column]) / 2.0
    out["spread_pct"] = (out["ask_close"] - out["bid_close"]) / out["mid_close"]
    return out.dropna()


def signed_next_hour_return(signal: pd.Series, prices: pd.DataFrame) -> pd.Series:
    signal = signal.reindex(prices.index).fillna(0.0).clip(-1.0, 1.0)
    entry_signal = signal.shift(1).fillna(0.0)
    long_ret = prices["bid_close"] / prices["ask_close"].shift(1) - 1.0
    short_ret = prices["bid_close"].shift(1) / prices["ask_close"] - 1.0
    return pd.Series(
        data=(entry_signal > 0) * long_ret + (entry_signal < 0) * short_ret,
        index=prices.index,
    ).fillna(0.0)


def signed_next_day_return(signal: pd.Series, daily_prices: pd.DataFrame) -> pd.Series:
    signal = signal.reindex(daily_prices.index).fillna(0.0).clip(-1.0, 1.0)
    long_ret = daily_prices["bid_close"].shift(-1) / daily_prices["ask_close"] - 1.0
    short_ret = daily_prices["bid_close"] / daily_prices["ask_close"].shift(-1) - 1.0
    raw = pd.Series(
        data=(signal > 0) * long_ret + (signal < 0) * short_ret,
        index=daily_prices.index,
    )
    return raw.shift(1).fillna(0.0)


def event_to_position(event: pd.Series, hold_hours: int) -> pd.Series:
    event = event.fillna(0.0).clip(-1.0, 1.0)
    values = [0.0] * len(event)
    raw = event.to_numpy()
    for idx, value in enumerate(raw):
        if value == 0:
            continue
        end = min(len(values), idx + hold_hours + 1)
        for pos_idx in range(idx + 1, end):
            if values[pos_idx] == 0.0:
                values[pos_idx] = float(value)
    return pd.Series(values, index=event.index)


def hourly_to_daily(hourly: pd.Series) -> pd.Series:
    return hourly.resample("1D").sum().dropna()


def max_drawdown(pl: pd.Series) -> float:
    cumulative = pl.cumsum()
    return float((cumulative - cumulative.cummax()).min())


def perf(daily: pd.Series) -> dict:
    daily = daily.dropna()
    if len(daily) < 30:
        return {
            "n": int(len(daily)),
            "sharpe": 0.0,
            "ann_ret": 0.0,
            "ann_vol": 0.0,
            "max_dd": 0.0,
            "daily_wr": 0.0,
            "worst_day": 0.0,
            "calmar": 0.0,
        }

    mean = float(daily.mean())
    std = float(daily.std())
    ann_ret = mean * ANN_DAYS
    ann_vol = std * math.sqrt(ANN_DAYS)
    max_dd = max_drawdown(daily)
    return {
        "n": int(len(daily)),
        "sharpe": mean / std * math.sqrt(ANN_DAYS) if std > 0 else 0.0,
        "ann_ret": ann_ret,
        "ann_vol": ann_vol,
        "max_dd": max_dd,
        "daily_wr": float((daily > 0).mean()),
        "worst_day": float(daily.min()),
        "calmar": ann_ret / abs(max_dd) if max_dd < 0 else 0.0,
    }


def vol_target(daily: pd.Series, target_vol: float) -> pd.Series:
    realized = daily.rolling(VOL_LOOKBACK).std().shift(1) * math.sqrt(ANN_DAYS)
    lev = (target_vol / realized).clip(upper=MAX_LEV).fillna(0.0)
    return daily * lev


def split_metrics(daily: pd.Series) -> dict:
    daily = daily.sort_index()
    full = perf(daily)
    is_metrics = perf(daily.loc[:IS_END])
    val_metrics = perf(daily.loc[VAL_START:VAL_END])
    oos_metrics = perf(daily.loc[OOS_START:])
    by_year = {
        int(year): perf(group)
        for year, group in daily.groupby(daily.index.year)
        if len(group) >= 30
    }
    positive_years = sum(1 for item in by_year.values() if item["ann_ret"] > 0)
    return {
        "full": full,
        "is": is_metrics,
        "val": val_metrics,
        "oos": oos_metrics,
        "by_year": by_year,
        "positive_years": positive_years,
    }


def signal_mr_tsm(prices: pd.DataFrame, lookback: int, mode: str) -> pd.Series:
    ret = prices["mid_close"].pct_change(lookback)
    if mode == "MR":
        return (ret < 0).astype(float) - (ret > 0).astype(float)
    if mode == "TSM":
        return (ret > 0).astype(float) - (ret < 0).astype(float)
    raise ValueError(mode)


def signal_mr_tsm_daily(daily_prices: pd.DataFrame, lookback: int, mode: str) -> pd.Series:
    ret = daily_prices["mid_close"].pct_change(lookback)
    if mode == "MR":
        return (ret < 0).astype(float) - (ret > 0).astype(float)
    if mode == "TSM":
        return (ret > 0).astype(float) - (ret < 0).astype(float)
    raise ValueError(mode)


def signal_ma_filter_daily(daily_prices: pd.DataFrame, lookback: int, mode: str) -> pd.Series:
    close = daily_prices["mid_close"]
    ma = close.rolling(lookback).mean()
    if mode == "LONG":
        return (close > ma).astype(float)
    if mode == "SHORT":
        return -(close < ma).astype(float)
    if mode == "BIAS":
        return (close > ma).astype(float) - (close < ma).astype(float)
    raise ValueError(mode)


def signal_zscore(prices: pd.DataFrame, lookback: int, mode: str) -> pd.Series:
    close = prices["mid_close"]
    zscore = (close - close.rolling(lookback).mean()) / close.rolling(lookback).std()
    raw = (zscore > 1.5).astype(float) - (zscore < -1.5).astype(float)
    if mode == "MR":
        return -raw
    if mode == "TSM":
        return raw
    raise ValueError(mode)


def signal_zscore_daily(daily_prices: pd.DataFrame, lookback: int, mode: str) -> pd.Series:
    close = daily_prices["mid_close"]
    zscore = (close - close.rolling(lookback).mean()) / close.rolling(lookback).std()
    raw = (zscore > 1.5).astype(float) - (zscore < -1.5).astype(float)
    if mode == "MR":
        return -raw
    if mode == "TSM":
        return raw
    raise ValueError(mode)


def signal_donchian(prices: pd.DataFrame, lookback: int, mode: str) -> pd.Series:
    close = prices["mid_close"]
    prev_high = close.rolling(lookback).max().shift(1)
    prev_low = close.rolling(lookback).min().shift(1)
    raw = (close > prev_high).astype(float) - (close < prev_low).astype(float)
    if mode == "BREAK":
        return raw
    if mode == "FADE":
        return -raw
    raise ValueError(mode)


def signal_donchian_daily(daily_prices: pd.DataFrame, lookback: int, mode: str) -> pd.Series:
    close = daily_prices["mid_close"]
    prev_high = close.rolling(lookback).max().shift(1)
    prev_low = close.rolling(lookback).min().shift(1)
    raw = (close > prev_high).astype(float) - (close < prev_low).astype(float)
    if mode == "BREAK":
        return raw
    if mode == "FADE":
        return -raw
    raise ValueError(mode)


def signal_session(prices: pd.DataFrame, mode: str, start_hour: int, end_hour: int, event_hour: int) -> pd.Series:
    event = pd.Series(0.0, index=prices.index)
    grouped = prices.groupby(prices.index.date)
    for _, day in grouped:
        session = day[(day.index.hour >= start_hour) & (day.index.hour <= end_hour)]
        event_bar = day[day.index.hour == event_hour]
        if session.empty or event_bar.empty:
            continue
        high = float(session["mid_high"].max())
        low = float(session["mid_low"].min())
        close = float(event_bar["mid_close"].iloc[-1])
        timestamp = event_bar.index[-1]
        breakout = float(close > high) - float(close < low)
        event.loc[timestamp] = breakout if mode == "BREAK" else -breakout
    return event


def build_candidates_for_symbol(symbol: str, prices: pd.DataFrame) -> list[Candidate]:
    out: list[Candidate] = []
    daily_prices = prices.resample("1D").last().dropna()

    for lookback in (1, 2, 3, 5, 10, 21, 42, 63, 126):
        for mode in ("MR", "TSM"):
            signal = signal_mr_tsm_daily(daily_prices, lookback, mode)
            daily = signed_next_day_return(signal, daily_prices)
            out.append(Candidate(f"{symbol}_{mode}_D{lookback}", symbol, f"{mode}_D", daily))

    for lookback in (20, 50, 100, 200):
        for mode in ("LONG", "SHORT", "BIAS"):
            signal = signal_ma_filter_daily(daily_prices, lookback, mode)
            daily = signed_next_day_return(signal, daily_prices)
            out.append(Candidate(f"{symbol}_MA_{mode}_D{lookback}", symbol, f"MA_{mode}", daily))

    for lookback in (20, 50, 100):
        for mode in ("MR", "TSM"):
            signal = signal_zscore_daily(daily_prices, lookback, mode)
            daily = signed_next_day_return(signal, daily_prices)
            out.append(Candidate(f"{symbol}_ZS_{mode}_D{lookback}", symbol, f"ZS_{mode}_D", daily))

    for lookback in (20, 50, 100):
        for mode in ("BREAK", "FADE"):
            signal = signal_donchian_daily(daily_prices, lookback, mode)
            daily = signed_next_day_return(signal, daily_prices)
            out.append(Candidate(f"{symbol}_DON_{mode}_D{lookback}", symbol, f"DON_{mode}_D", daily))

    for lookback in (3, 6, 12, 24, 48, 96, 168):
        for mode in ("MR", "TSM"):
            signal = signal_mr_tsm(prices, lookback, mode)
            daily = hourly_to_daily(signed_next_hour_return(signal, prices))
            out.append(Candidate(f"{symbol}_{mode}_H{lookback}", symbol, f"{mode}_H", daily))

    for lookback in (24, 48, 96, 168):
        for mode in ("MR", "TSM"):
            signal = signal_zscore(prices, lookback, mode)
            daily = hourly_to_daily(signed_next_hour_return(signal, prices))
            out.append(Candidate(f"{symbol}_ZS_{mode}_H{lookback}", symbol, f"ZS_{mode}", daily))

    for lookback in (24, 48, 96):
        for mode in ("BREAK", "FADE"):
            signal = signal_donchian(prices, lookback, mode)
            daily = hourly_to_daily(signed_next_hour_return(signal, prices))
            out.append(Candidate(f"{symbol}_DON_{mode}_H{lookback}", symbol, f"DON_{mode}", daily))

    session_specs = [
        ("ASIA", 0, 6, 7, 6),
        ("EU_PRE", 6, 10, 11, 5),
        ("US_OPEN", 12, 14, 15, 4),
    ]
    for label, start_hour, end_hour, event_hour, hold in session_specs:
        for mode in ("BREAK", "FADE"):
            event = signal_session(prices, mode, start_hour, end_hour, event_hour)
            position = event_to_position(event, hold)
            daily = hourly_to_daily(signed_next_hour_return(position, prices))
            out.append(Candidate(f"{symbol}_{label}_{mode}_HOLD{hold}", symbol, f"{label}_{mode}", daily))

    return out


def candidate_score(metrics: dict) -> float:
    full = metrics["full"]
    is_metrics = metrics["is"]
    val = metrics["val"]
    oos = metrics["oos"]
    if oos["n"] < 300 or full["n"] < MIN_DAILY_OBS:
        return -999.0
    if is_metrics["sharpe"] < -0.25 or val["sharpe"] < -0.75:
        return -50.0 + oos["sharpe"]
    return (
        1.5 * oos["sharpe"]
        + 1.0 * val["sharpe"]
        + 0.8 * is_metrics["sharpe"]
        + 0.2 * full["sharpe"]
        + 0.15 * metrics["positive_years"]
        - 2.0 * abs(min(0.0, oos["max_dd"]))
        - 1.0 * abs(min(0.0, oos["worst_day"]))
    )


def greedy_portfolio(rows: list[dict], daily_by_name: dict[str, pd.Series], max_items: int = 10) -> list[str]:
    selected: list[str] = []
    selected_symbols: set[str] = set()
    for row in rows:
        name = row["name"]
        symbol = row["symbol"]
        if len(selected) >= max_items:
            break
        if row["oos"]["sharpe"] < 0.4 or row["val"]["sharpe"] < 0.0 or row["is"]["sharpe"] < 0.0:
            continue
        if symbol in selected_symbols and len(selected_symbols) < 8:
            continue
        candidate = daily_by_name[name]
        ok = True
        for existing in selected:
            corr = candidate.corr(daily_by_name[existing])
            if pd.notna(corr) and corr > 0.70:
                ok = False
                break
        if ok:
            selected.append(name)
            selected_symbols.add(symbol)
    return selected


def combine(names: list[str], daily_by_name: dict[str, pd.Series]) -> pd.Series:
    if not names:
        return pd.Series(dtype=float)
    frame = pd.DataFrame({name: daily_by_name[name] for name in names}).fillna(0.0)
    return frame.mean(axis=1)


def phase_pass_rate(daily: pd.Series, target_vol: float, n_mc: int = 2000, horizon: int = 90) -> dict:
    levered = vol_target(daily, target_vol).loc[OOS_START:].dropna()
    if len(levered) < 100:
        return {"phase_5pct_90d": 0.0, "breach_daily": 0.0, "breach_total": 0.0}

    rng = pd.Series(range(n_mc)).sample(frac=1.0, random_state=SEED).index
    values = levered.to_numpy()
    passes = 0
    daily_breaches = 0
    total_breaches = 0
    for seed_offset in rng:
        sample = pd.Series(values).sample(n=horizon, replace=True, random_state=SEED + int(seed_offset)).to_numpy()
        equity = 0.0
        peak = 0.0
        passed = False
        breached_daily = False
        breached_total = False
        for ret in sample:
            equity += float(ret)
            peak = max(peak, equity)
            if ret <= -0.05:
                breached_daily = True
                break
            if equity <= -0.10 or equity - peak <= -0.10:
                breached_total = True
                break
            if equity >= 0.05:
                passed = True
                break
        passes += int(passed)
        daily_breaches += int(breached_daily)
        total_breaches += int(breached_total)
    return {
        "phase_5pct_90d": passes / n_mc,
        "breach_daily": daily_breaches / n_mc,
        "breach_total": total_breaches / n_mc,
    }


def main() -> None:
    rows: list[dict] = []
    daily_by_name: dict[str, pd.Series] = {}
    inventory: dict[str, dict] = {}

    for symbol in SYMBOLS:
        prices = load_symbol(symbol)
        inventory[symbol] = {
            "rows": int(len(prices)),
            "start": str(prices.index.min()) if not prices.empty else "",
            "end": str(prices.index.max()) if not prices.empty else "",
            "median_spread_bps": float(prices["spread_pct"].median() * 10_000) if not prices.empty else 0.0,
        }
        if prices.empty:
            continue

        for candidate in build_candidates_for_symbol(symbol, prices):
            daily = candidate.daily
            metrics = split_metrics(daily)
            score = candidate_score(metrics)
            row = {
                "name": candidate.name,
                "symbol": candidate.symbol,
                "family": candidate.family,
                "score": score,
                **metrics,
            }
            rows.append(row)
            daily_by_name[candidate.name] = daily

    rows.sort(key=lambda item: item["score"], reverse=True)
    selected = greedy_portfolio(rows, daily_by_name)
    portfolio = combine(selected, daily_by_name)

    frontier = {}
    for target_vol in (0.08, 0.10, 0.12, 0.15):
        levered = vol_target(portfolio, target_vol)
        frontier[f"{target_vol:.0%}"] = {
            "perf": split_metrics(levered),
            "phase": phase_pass_rate(portfolio, target_vol),
        }

    metrics_out = {
        "inventory": inventory,
        "top_candidates": rows[:80],
        "selected": selected,
        "portfolio_frontier": frontier,
    }
    OUT_METRICS.write_text(json.dumps(metrics_out, indent=2), encoding="utf-8")

    lines: list[str] = []
    lines.append("# H1 Edge Miner Report")
    lines.append("")
    lines.append("## Inventory")
    lines.append("")
    lines.append("| symbol | rows | start | end | median spread bps |")
    lines.append("|---|---:|---|---|---:|")
    for symbol, item in inventory.items():
        if item["rows"] <= 0:
            continue
        lines.append(
            f"| {symbol} | {item['rows']} | {item['start'][:10]} | "
            f"{item['end'][:10]} | {item['median_spread_bps']:.2f} |"
        )
    lines.append("")

    lines.append("## Top candidates")
    lines.append("")
    lines.append("| rank | name | family | score | IS Sh | VAL Sh | OOS Sh | OOS ret% | OOS DD% | OOS worst day% | WR% | +years |")
    lines.append("|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for rank, row in enumerate(rows[:40], 1):
        lines.append(
            f"| {rank} | {row['name']} | {row['family']} | {row['score']:.2f} | "
            f"{row['is']['sharpe']:+.2f} | {row['val']['sharpe']:+.2f} | "
            f"{row['oos']['sharpe']:+.2f} | {row['oos']['ann_ret']*100:+.1f} | "
            f"{row['oos']['max_dd']*100:+.1f} | {row['oos']['worst_day']*100:+.2f} | "
            f"{row['oos']['daily_wr']*100:.1f} | {row['positive_years']} |"
        )
    lines.append("")

    lines.append("## Greedy diversified portfolio")
    lines.append("")
    for name in selected:
        lines.append(f"- {name}")
    lines.append("")
    lines.append("| target vol | OOS Sh | OOS ret% | OOS DD% | worst day% | phase 5%/90d | daily breach | total breach |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
    for label, item in frontier.items():
        oos = item["perf"]["oos"]
        phase = item["phase"]
        lines.append(
            f"| {label} | {oos['sharpe']:+.2f} | {oos['ann_ret']*100:+.1f} | "
            f"{oos['max_dd']*100:+.1f} | {oos['worst_day']*100:+.2f} | "
            f"{phase['phase_5pct_90d']*100:.1f}% | {phase['breach_daily']*100:.1f}% | "
            f"{phase['breach_total']*100:.1f}% |"
        )
    lines.append("")

    lines.append("## Notes")
    lines.append("")
    lines.append("- Returns include bid/ask spread through execution prices.")
    lines.append("- OOS is 2024-2025. Validation is 2023. IS is <= 2022.")
    lines.append("- Phase simulation is a simple bootstrap on OOS daily returns; use it as a filter, not a payout forecast.")
    lines.append("- Old invalid ids `usa100idxusd`, `us30idxusd`, `usoilusd` are excluded; corrected ids are used.")
    OUT_REPORT.write_text("\n".join(lines), encoding="utf-8")

    print(f"Candidates tested: {len(rows)}")
    print(f"Selected: {len(selected)}")
    print(f"Report: {OUT_REPORT}")
    print(f"Metrics: {OUT_METRICS}")


if __name__ == "__main__":
    main()
