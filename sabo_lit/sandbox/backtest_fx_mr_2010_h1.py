"""
Sandbox — FX daily mean-reversion 2010-2025 validation using Dukascopy H1 bid/ask.

Purpose:
  - extend the current FX MR validation before 2019
  - avoid huge tick/M5 downloads by using compact H1 data
  - estimate bid/ask costs from H1 spreads and charge only when position changes
  - approximate prop-firm intraday adverse excursion from H1 lows/highs

This is an edge validation pass, not final execution-grade prop simulation.
M5 remains better for exact intraday DD, but H1 is enough to detect whether the
daily MR edge existed before the 2023-2025 favorable regime.
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pandas as pd


HERE = Path(__file__).parent
DATA_DIRS = [
    HERE / "data" / "dukascopy_research_full_h1_2010-01-01_2012-01-01",
    HERE / "data" / "dukascopy_research_full_h1_2012-01-01_2026-01-01",
]
OUT_REPORT = HERE / "backtest_fx_mr_2010_h1_report.md"
OUT_METRICS = HERE / "backtest_fx_mr_2010_h1_metrics.json"

ANN_DAYS = 252
VOL_LOOKBACK = 60
MAX_LEVERAGE = 10.0
N_RANDOM = 1000
SEED = 20260522

FX_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD"]
FROZEN_BEST_LB = {
    "EURUSD": 5,
    "GBPUSD": 3,
    "USDJPY": 10,
    "AUDUSD": 21,
    "NZDUSD": 10,
    "USDCAD": 3,
}

SPECS = {
    "FX_MR_STACK": FROZEN_BEST_LB,
    "NO_EUR_STACK": {pair: lookback for pair, lookback in FROZEN_BEST_LB.items() if pair != "EURUSD"},
    "COMDOLL_STACK": {"AUDUSD": 21, "NZDUSD": 10, "USDCAD": 3},
    "FAST_STACK": {"EURUSD": 3, "GBPUSD": 3, "USDCAD": 3},
    "UNIFORM_MR10": {pair: 10 for pair in FX_PAIRS},
}

PERIODS = {
    "2010_2013": ("2010-01-01", "2013-12-31 23:59:59"),
    "2014_2017": ("2014-01-01", "2017-12-31 23:59:59"),
    "2018_2021": ("2018-01-01", "2021-12-31 23:59:59"),
    "2022_2025": ("2022-01-01", "2025-12-31 23:59:59"),
    "pre_2019": ("2010-01-01", "2018-12-31 23:59:59"),
    "post_2019": ("2019-01-01", "2025-12-31 23:59:59"),
    "post_2023": ("2023-01-01", "2025-12-31 23:59:59"),
}


def read_side(pair: str, side: str) -> pd.DataFrame:
    frames = []
    symbol = pair.lower()
    for data_dir in DATA_DIRS:
        side_dir = data_dir / symbol / side
        if not side_dir.exists():
            continue
        for path in sorted(side_dir.glob("*.csv")):
            if path.stat().st_size == 0:
                continue
            df = pd.read_csv(path)
            if df.empty:
                continue
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df = df.set_index("timestamp").sort_index()
            frames.append(df[["open", "high", "low", "close"]].astype(float))
    if not frames:
        raise FileNotFoundError(f"no H1 {side} data for {pair}")
    out = pd.concat(frames).sort_index()
    return out[~out.index.duplicated(keep="last")]


def load_pair(pair: str) -> pd.DataFrame:
    bid = read_side(pair, "bid")
    ask = read_side(pair, "ask")
    idx = bid.index.intersection(ask.index)
    bid = bid.loc[idx]
    ask = ask.loc[idx]
    out = pd.DataFrame(index=idx)
    for column in ("open", "high", "low", "close"):
        out[f"bid_{column}"] = bid[column]
        out[f"ask_{column}"] = ask[column]
        out[f"mid_{column}"] = (bid[column] + ask[column]) / 2.0
    return out.dropna()


def daily_from_h1(h1: pd.DataFrame) -> pd.DataFrame:
    daily = h1.resample("1D").agg({
        "bid_open": "first",
        "bid_high": "max",
        "bid_low": "min",
        "bid_close": "last",
        "ask_open": "first",
        "ask_high": "max",
        "ask_low": "min",
        "ask_close": "last",
        "mid_close": "last",
    })
    return daily.dropna()


def mr_signal(daily: pd.DataFrame, lookback: int) -> pd.Series:
    close = daily["mid_close"]
    cumulative = close.shift(1) / close.shift(lookback + 1) - 1.0
    return ((cumulative < 0).astype(float) - (cumulative > 0).astype(float)).fillna(0.0)


def pair_returns(pair: str, daily: pd.DataFrame, lookback: int) -> tuple[pd.Series, pd.Series]:
    signal = mr_signal(daily, lookback).clip(-1.0, 1.0)
    mid_ret = daily["mid_close"].pct_change()
    spread_pct = (daily["ask_close"] - daily["bid_close"]) / daily["mid_close"]
    turnover = signal.diff().abs().fillna(0.0) / 2.0
    close_return = (signal * mid_ret - turnover * spread_pct).fillna(0.0)
    close_return.name = f"{pair}_MR{lookback}"

    prev_mid = daily["mid_close"].shift(1)
    mid_low = (daily["bid_low"] + daily["ask_low"]) / 2.0
    mid_high = (daily["bid_high"] + daily["ask_high"]) / 2.0
    long_path = mid_low / prev_mid - 1.0
    short_path = prev_mid / mid_high - 1.0
    intraday_min = pd.Series(
        (signal > 0) * long_path + (signal < 0) * short_path,
        index=daily.index,
        name=f"{pair}_MR{lookback}_intraday",
    ).fillna(0.0)
    return close_return, intraday_min


def combine(members: dict[str, int], pair_streams: dict[tuple[str, int], tuple[pd.Series, pd.Series]]) -> tuple[pd.Series, pd.Series]:
    daily = pd.DataFrame({f"{pair}_{lookback}": pair_streams[(pair, lookback)][0] for pair, lookback in members.items()})
    intraday = pd.DataFrame({
        f"{pair}_{lookback}": pair_streams[(pair, lookback)][1]
        for pair, lookback in members.items()
    })
    return daily.fillna(0.0).mean(axis=1), intraday.fillna(0.0).mean(axis=1)


def max_drawdown(returns: pd.Series) -> float:
    cumulative = returns.cumsum()
    return float((cumulative - cumulative.cummax()).min())


def perf(returns: pd.Series) -> dict:
    returns = returns.dropna()
    if len(returns) < 30:
        return {"n": int(len(returns)), "sharpe": 0.0, "ann_ret": 0.0, "ann_vol": 0.0, "max_dd": 0.0, "wr": 0.0}
    std = float(returns.std())
    mean = float(returns.mean())
    ann_ret = mean * ANN_DAYS
    max_dd = max_drawdown(returns)
    return {
        "n": int(len(returns)),
        "sharpe": mean / std * math.sqrt(ANN_DAYS) if std > 0 else 0.0,
        "ann_ret": ann_ret,
        "ann_vol": std * math.sqrt(ANN_DAYS),
        "max_dd": max_dd,
        "calmar": ann_ret / abs(max_dd) if max_dd < 0 else 0.0,
        "wr": float((returns > 0).mean()),
        "worst_day": float(returns.min()),
    }


def period_metrics(returns: pd.Series) -> dict[str, dict]:
    out = {"full": perf(returns)}
    for name, (start, end) in PERIODS.items():
        start_ts = pd.Timestamp(start, tz="UTC")
        end_ts = pd.Timestamp(end, tz="UTC")
        out[name] = perf(returns.loc[start_ts:end_ts])
    out["by_year"] = {
        int(year): perf(group)
        for year, group in returns.groupby(returns.index.year)
        if len(group) >= 30
    }
    return out


def vol_target(returns: pd.Series, target_vol: float) -> pd.Series:
    realized = returns.rolling(VOL_LOOKBACK).std().shift(1) * math.sqrt(ANN_DAYS)
    leverage = (target_vol / realized).clip(upper=MAX_LEVERAGE).fillna(0.0)
    return (returns * leverage).dropna()


def random_baseline(members: dict[str, int], pair_daily: dict[str, pd.DataFrame], index: pd.Index, seed: int) -> dict:
    rng = random.Random(seed)
    values = []
    for _ in range(N_RANDOM):
        member_returns = {}
        for pair in members:
            daily = pair_daily[pair].reindex(index)
            signal = pd.Series([rng.choice((-1.0, 1.0)) for _ in range(len(index))], index=index)
            mid_ret = daily["mid_close"].pct_change()
            spread_pct = (daily["ask_close"] - daily["bid_close"]) / daily["mid_close"]
            turnover = signal.diff().abs().fillna(0.0) / 2.0
            member_returns[pair] = (signal * mid_ret - turnover * spread_pct).fillna(0.0)
        random_daily = pd.DataFrame(member_returns).mean(axis=1)
        values.append(perf(random_daily)["sharpe"])
    values = sorted(values)
    return {
        "p05": values[int(0.05 * N_RANDOM)],
        "p50": values[int(0.50 * N_RANDOM)],
        "p95": values[int(0.95 * N_RANDOM)],
    }


def main() -> None:
    print("Loading H1 bid/ask 2010-2026...")
    pair_daily = {}
    for pair in FX_PAIRS:
        h1 = load_pair(pair)
        daily = daily_from_h1(h1)
        pair_daily[pair] = daily
        print(f"  {pair}: {daily.index.min().date()} -> {daily.index.max().date()} n={len(daily)}")

    needed = {(pair, lookback) for members in SPECS.values() for pair, lookback in members.items()}
    pair_streams = {}
    for pair, lookback in sorted(needed):
        pair_streams[(pair, lookback)] = pair_returns(pair, pair_daily[pair], lookback)

    strategy_returns = {}
    strategy_intraday = {}
    metrics = {}
    for name, members in SPECS.items():
        daily, intraday = combine(members, pair_streams)
        common = daily.index.intersection(intraday.index)
        daily = daily.loc[common].sort_index()
        intraday = intraday.loc[common].sort_index()
        strategy_returns[name] = daily
        strategy_intraday[name] = intraday
        m = period_metrics(daily)
        m["full"]["worst_intraday_h1"] = float(intraday.min())
        m["vol10"] = period_metrics(vol_target(daily, 0.10))
        m["random_signal_sharpe"] = random_baseline(members, pair_daily, daily.index, SEED + len(metrics))
        metrics[name] = m
        print(
            f"{name:<14} full Sh={m['full']['sharpe']:+.2f} ann={m['full']['ann_ret']*100:+.2f}% "
            f"pre2019 Sh={m['pre_2019']['sharpe']:+.2f} post2023 Sh={m['post_2023']['sharpe']:+.2f}"
        )

    lines: list[str] = []

    def emit(line: str = "") -> None:
        lines.append(line)

    emit("# FX MR 2010-2025 H1 Bid/Ask Backtest")
    emit("")
    emit("Data: Dukascopy H1 bid/ask, 6 FX pairs. Returns use mid close-to-close")
    emit("with bid/ask spread charged on turnover only. Intraday DD approximated from H1 lows/highs.")
    emit("")
    emit("## Strategy comparison")
    emit("| spec | full Sh | full ann% | full DD% | pre-2019 Sh | 2022-2025 Sh | 2023-2025 Sh | worst H1 intraday% | random p95 Sh | verdict |")
    emit("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for name, m in metrics.items():
        full = m["full"]
        random_p95 = m["random_signal_sharpe"]["p95"]
        verdict = (
            "ROBUST_WEAK"
            if full["sharpe"] > 0
            and full["ann_ret"] > 0
            and full["sharpe"] > random_p95
            and m["pre_2019"]["sharpe"] > 0
            and m["post_2023"]["sharpe"] > 0
            else "REGIME/WEAK"
        )
        emit(
            f"| {name} | {full['sharpe']:+.2f} | {full['ann_ret']*100:+.2f} | {full['max_dd']*100:+.2f} | "
            f"{m['pre_2019']['sharpe']:+.2f} | {m['2022_2025']['sharpe']:+.2f} | {m['post_2023']['sharpe']:+.2f} | "
            f"{full['worst_intraday_h1']*100:+.2f} | {random_p95:+.2f} | {verdict} |"
        )
    emit("")

    emit("## FX_MR_STACK by year")
    emit("| year | Sharpe | ann% | maxDD% | win% |")
    emit("|---|---:|---:|---:|---:|")
    for year, item in metrics["FX_MR_STACK"]["by_year"].items():
        emit(f"| {year} | {item['sharpe']:+.2f} | {item['ann_ret']*100:+.2f} | {item['max_dd']*100:+.2f} | {item['wr']*100:.1f} |")
    emit("")

    emit("## 10% vol-target comparison")
    emit("| spec | full Sh | ann% | maxDD% | pre-2019 Sh | post-2023 Sh |")
    emit("|---|---:|---:|---:|---:|---:|")
    for name, m in metrics.items():
        vt = m["vol10"]
        emit(
            f"| {name} | {vt['full']['sharpe']:+.2f} | {vt['full']['ann_ret']*100:+.2f} | "
            f"{vt['full']['max_dd']*100:+.2f} | {vt['pre_2019']['sharpe']:+.2f} | {vt['post_2023']['sharpe']:+.2f} |"
        )
    emit("")

    emit("## Interpretation")
    emit("- If pre-2019 Sharpe is negative, the edge is not a 2010-stable law; it is regime-dependent.")
    emit("- If full Sharpe beats random p95 but only because 2023-2025 is strong, deploy with regime/runway controls.")
    emit("- H1 underestimates exact intraday DD versus M5, but is sufficient for long-horizon edge validation.")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))
    OUT_METRICS.write_text(json.dumps(metrics, indent=2, default=str))
    print(f"\nWrote {OUT_REPORT}")
    print(f"Wrote {OUT_METRICS}")


if __name__ == "__main__":
    main()
