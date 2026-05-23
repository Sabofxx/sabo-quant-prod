"""
Sandbox — edge miner for additional FX daily strategies.

Purpose: find genuinely useful additions to the current prop-firm stack.

This is intentionally conservative:
  - Generates many simple, interpretable daily FX strategies.
  - Reports IS, OOS, full, recent and per-year stability.
  - Penalizes high correlation with the existing FX_MR_STACK.
  - Flags candidates that are likely only OOS/data-mined artifacts.

It does not deploy anything automatically. It is a research filter.
"""
from __future__ import annotations

import itertools
import json
import math
from pathlib import Path

import pandas as pd


HERE = Path(__file__).parent
DATA = HERE / "data"
OUT_REPORT = HERE / "strategy_edge_miner_report.md"
OUT_METRICS = HERE / "strategy_edge_miner_metrics.json"

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD"]
PIP = {
    "EURUSD": 0.0001,
    "GBPUSD": 0.0001,
    "AUDUSD": 0.0001,
    "NZDUSD": 0.0001,
    "USDCAD": 0.0001,
    "USDJPY": 0.01,
}
RT_PIPS = {
    "EURUSD": 1.9,
    "GBPUSD": 2.3,
    "USDJPY": 2.1,
    "AUDUSD": 2.3,
    "NZDUSD": 3.1,
    "USDCAD": 2.7,
}
BEST_LB = {
    "EURUSD": 5,
    "GBPUSD": 3,
    "USDJPY": 10,
    "AUDUSD": 21,
    "NZDUSD": 10,
    "USDCAD": 3,
}
FILES = {pair: f"{pair.lower()}-m5-bid-2019-01-01-2026-01-01.csv" for pair in PAIRS}

LOOKBACKS = [1, 2, 3, 5, 10, 15, 21, 42, 63, 126]
ANN_DAYS = 252
IS_END = pd.Timestamp("2023-12-31 23:59:59", tz="UTC")
OOS_START = pd.Timestamp("2024-01-01", tz="UTC")
RECENT_START = pd.Timestamp("2023-01-01", tz="UTC")


def load_daily_close(pair: str) -> pd.Series:
    df = pd.read_csv(DATA / FILES[pair])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    return df["close"].astype(float).resample("1D").last().dropna()


def signal_from_returns(rets: pd.Series, lookback: int, mode: str) -> pd.Series:
    cumulative = (1.0 + rets).rolling(lookback).apply(lambda values: values.prod() - 1.0, raw=True)
    if mode == "MR":
        signal = (cumulative < 0).astype(float) - (cumulative > 0).astype(float)
    elif mode == "TSM":
        signal = (cumulative > 0).astype(float) - (cumulative < 0).astype(float)
    else:
        raise ValueError(mode)
    return signal.shift(1).dropna()


def net_pair_return(pair: str, rets: pd.Series, close: pd.Series, signal: pd.Series) -> pd.Series:
    common = rets.index.intersection(signal.index)
    signal = signal.loc[common]
    gross = signal * rets.loc[common]
    turnover = signal.diff().abs().fillna(0.0) / 2.0
    cost = (turnover * RT_PIPS[pair] * PIP[pair] / close).reindex(common).fillna(0.0)
    return gross - cost


def stack(streams: dict[str, pd.Series]) -> pd.Series:
    return pd.DataFrame(streams).fillna(0.0).sum(axis=1) / len(streams)


def max_drawdown(pl: pd.Series) -> float:
    cumulative = pl.cumsum()
    return float((cumulative - cumulative.cummax()).min())


def perf(pl: pd.Series) -> dict:
    pl = pl.dropna()
    if len(pl) < 30:
        return {
            "n": len(pl),
            "sharpe": 0.0,
            "ann_ret": 0.0,
            "ann_vol": 0.0,
            "max_dd": 0.0,
            "wr": 0.0,
            "calmar": 0.0,
        }
    mean = float(pl.mean())
    std = float(pl.std())
    ann_ret = mean * ANN_DAYS
    max_dd = max_drawdown(pl)
    return {
        "n": len(pl),
        "sharpe": mean / std * math.sqrt(ANN_DAYS) if std > 0 else 0.0,
        "ann_ret": ann_ret,
        "ann_vol": std * math.sqrt(ANN_DAYS),
        "max_dd": max_dd,
        "wr": float((pl > 0).mean()),
        "calmar": ann_ret / abs(max_dd) if max_dd < 0 else 0.0,
    }


def by_year(pl: pd.Series) -> dict[int, dict]:
    out = {}
    for year, group in pl.groupby(pl.index.year):
        out[int(year)] = perf(group)
    return out


def positive_years(year_metrics: dict[int, dict]) -> int:
    return sum(1 for metrics in year_metrics.values() if metrics["ann_ret"] > 0)


def longest_losing_streak_years(year_metrics: dict[int, dict]) -> int:
    longest = 0
    current = 0
    for year in sorted(year_metrics):
        if year_metrics[year]["ann_ret"] <= 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def regime_gate(pl: pd.Series, kind: str) -> pd.Series:
    realized = pl.rolling(60).std().shift(1)
    rank = realized.rolling(252, min_periods=80).rank(pct=True)
    if kind == "LOW_VOL":
        gate = rank <= 0.50
    elif kind == "HIGH_VOL":
        gate = rank >= 0.50
    elif kind == "MID_VOL":
        gate = (rank >= 0.25) & (rank <= 0.75)
    else:
        raise ValueError(kind)
    return pl.where(gate.fillna(False), 0.0)


def weekday_gate(pl: pd.Series, weekday: int) -> pd.Series:
    return pl.where(pl.index.weekday == weekday, 0.0)


def build_candidates() -> tuple[dict[str, pd.Series], pd.Series]:
    closes = {pair: load_daily_close(pair) for pair in PAIRS}
    rets = pd.DataFrame({pair: closes[pair].pct_change() for pair in PAIRS}).dropna()
    closes_df = pd.DataFrame({pair: closes[pair].reindex(rets.index) for pair in PAIRS})

    pair_streams: dict[tuple[str, str, int], pd.Series] = {}
    for pair in PAIRS:
        for mode in ("MR", "TSM"):
            for lookback in LOOKBACKS:
                signal = signal_from_returns(rets[pair], lookback, mode)
                pair_streams[(pair, mode, lookback)] = net_pair_return(pair, rets[pair], closes_df[pair], signal)

    candidates: dict[str, pd.Series] = {}

    # Current benchmark.
    candidates["FX_MR_STACK_BEST"] = stack(
        {pair: pair_streams[(pair, "MR", BEST_LB[pair])] for pair in PAIRS}
    )

    # Fixed lookback stacks.
    for mode in ("MR", "TSM"):
        for lookback in LOOKBACKS:
            candidates[f"{mode}_ALL_L{lookback}"] = stack(
                {pair: pair_streams[(pair, mode, lookback)] for pair in PAIRS}
            )

    # Single-pair strategies.
    for pair in PAIRS:
        for mode in ("MR", "TSM"):
            for lookback in LOOKBACKS:
                candidates[f"{pair}_{mode}_L{lookback}"] = pair_streams[(pair, mode, lookback)]

    # Economically interpretable subsets.
    candidates["MR_NO_EUR_BEST"] = stack(
        {pair: pair_streams[(pair, "MR", BEST_LB[pair])] for pair in PAIRS if pair != "EURUSD"}
    )
    candidates["MR_COMDOLL_BEST"] = stack(
        {pair: pair_streams[(pair, "MR", BEST_LB[pair])] for pair in ("AUDUSD", "NZDUSD", "USDCAD")}
    )
    candidates["MR_EUROPE_BEST"] = stack(
        {pair: pair_streams[(pair, "MR", BEST_LB[pair])] for pair in ("EURUSD", "GBPUSD")}
    )
    candidates["MR_JPY_CAD_BEST"] = stack(
        {pair: pair_streams[(pair, "MR", BEST_LB[pair])] for pair in ("USDJPY", "USDCAD")}
    )
    candidates["MR_ANTIPODEAN_BEST"] = stack(
        {pair: pair_streams[(pair, "MR", BEST_LB[pair])] for pair in ("AUDUSD", "NZDUSD")}
    )

    # Best-lookback subsets. Useful for account diversification, but flagged by selection risk.
    for size in range(2, 6):
        for members in itertools.combinations(PAIRS, size):
            name = "MR_BEST_SUB_" + "_".join(members)
            candidates[name] = stack(
                {pair: pair_streams[(pair, "MR", BEST_LB[pair])] for pair in members}
            )

    # Regime and weekday variants of only pre-defined, interpretable bases.
    base_names = [
        "FX_MR_STACK_BEST",
        "MR_NO_EUR_BEST",
        "MR_COMDOLL_BEST",
        "MR_EUROPE_BEST",
        "MR_ANTIPODEAN_BEST",
    ]
    for base_name in base_names:
        base = candidates[base_name]
        for kind in ("LOW_VOL", "HIGH_VOL", "MID_VOL"):
            candidates[f"{base_name}_{kind}"] = regime_gate(base, kind)
        for weekday in range(5):
            candidates[f"{base_name}_DOW{weekday}"] = weekday_gate(base, weekday)

    return candidates, candidates["FX_MR_STACK_BEST"]


def evaluate_candidates(candidates: dict[str, pd.Series], benchmark: pd.Series) -> list[dict]:
    rows = []
    for name, pl in candidates.items():
        full = perf(pl)
        is_metrics = perf(pl[pl.index <= IS_END])
        oos = perf(pl[pl.index >= OOS_START])
        recent = perf(pl[pl.index >= RECENT_START])
        year_metrics = by_year(pl)
        oos_common = pl[pl.index >= OOS_START].dropna()
        bench_common = benchmark.reindex(oos_common.index).dropna()
        common = oos_common.index.intersection(bench_common.index)
        corr = float(oos_common.loc[common].corr(bench_common.loc[common])) if len(common) > 30 else 0.0
        pos_years = positive_years(year_metrics)
        losing_streak = longest_losing_streak_years(year_metrics)
        robust = (
            oos["sharpe"] >= 0.80
            and recent["sharpe"] >= 0.60
            and full["sharpe"] >= 0.20
            and pos_years >= 4
            and losing_streak <= 2
        )
        additive = robust and corr <= 0.65 and oos["sharpe"] >= 0.75
        score = (
            oos["sharpe"]
            + 0.40 * recent["sharpe"]
            + 0.25 * full["sharpe"]
            - 0.35 * max(corr, 0.0)
            + 0.08 * (pos_years - 3)
            - 0.20 * max(losing_streak - 1, 0)
        )
        if full["ann_ret"] <= 0:
            score -= 0.50
        if name == "FX_MR_STACK_BEST":
            score += 0.20
        rows.append({
            "name": name,
            "score": score,
            "full": full,
            "is": is_metrics,
            "oos": oos,
            "recent": recent,
            "years": year_metrics,
            "pos_years": pos_years,
            "losing_streak": losing_streak,
            "corr_to_benchmark": corr,
            "robust": robust,
            "additive": additive,
        })
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def main() -> None:
    print("Building candidate library...")
    candidates, benchmark = build_candidates()
    print(f"Candidates: {len(candidates)}")
    rows = evaluate_candidates(candidates, benchmark)

    robust_rows = [row for row in rows if row["robust"]]
    additive_rows = [row for row in rows if row["additive"]]

    lines: list[str] = []

    def emit(line: str = "") -> None:
        print(line)
        lines.append(line)

    emit("# FX Edge Miner")
    emit("")
    emit(f"Candidates tested: {len(rows)}")
    emit("Split: IS 2019-2023, OOS 2024-2025, recent 2023-2025.")
    emit("Benchmark: current `FX_MR_STACK_BEST`.")
    emit("")
    emit("## Top 25 By Robust Score")
    emit("| rank | name | score | full Sh | IS Sh | OOS Sh | recent Sh | OOS ret% | full DD% | wr% | corr bench | pos years | robust | additive |")
    emit("|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|")
    for rank, row in enumerate(rows[:25], start=1):
        emit(
            f"| {rank} | {row['name']} | {row['score']:.2f} | "
            f"{row['full']['sharpe']:+.2f} | {row['is']['sharpe']:+.2f} | "
            f"{row['oos']['sharpe']:+.2f} | {row['recent']['sharpe']:+.2f} | "
            f"{row['oos']['ann_ret']*100:+.1f} | {row['full']['max_dd']*100:+.1f} | "
            f"{row['oos']['wr']*100:.1f} | {row['corr_to_benchmark']:+.2f} | "
            f"{row['pos_years']} | {row['robust']} | {row['additive']} |"
        )
    emit("")

    emit("## Robust Candidates")
    emit("| name | full Sh | OOS Sh | recent Sh | corr bench | pos years | losing streak |")
    emit("|---|---:|---:|---:|---:|---:|---:|")
    for row in robust_rows[:30]:
        emit(
            f"| {row['name']} | {row['full']['sharpe']:+.2f} | "
            f"{row['oos']['sharpe']:+.2f} | {row['recent']['sharpe']:+.2f} | "
            f"{row['corr_to_benchmark']:+.2f} | {row['pos_years']} | {row['losing_streak']} |"
        )
    if not robust_rows:
        emit("| none | | | | | | |")
    emit("")

    emit("## Additive Candidates")
    emit("Additive means robust and OOS corr <= +0.65 vs current stack.")
    emit("| name | OOS Sh | OOS ret% | corr bench | reason |")
    emit("|---|---:|---:|---:|---|")
    for row in additive_rows[:20]:
        reason = "separate account candidate" if row["name"] != "FX_MR_STACK_BEST" else "benchmark"
        emit(
            f"| {row['name']} | {row['oos']['sharpe']:+.2f} | "
            f"{row['oos']['ann_ret']*100:+.1f} | {row['corr_to_benchmark']:+.2f} | {reason} |"
        )
    if not additive_rows:
        emit("| none | | | | |")
    emit("")

    emit("## Decision")
    if additive_rows:
        emit("New usable additions found. Add only top additive candidates to prop allocation after forward paper verification.")
    else:
        emit("No genuinely additive robust edge found. Current FX_MR_STACK remains the core edge; improvements should come from execution, rules selection, or new asset data.")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))
    OUT_METRICS.write_text(json.dumps({
        "top": rows[:50],
        "robust": robust_rows,
        "additive": additive_rows,
    }, indent=2, default=str))
    print(f"\nWrote {OUT_REPORT}")
    print(f"Wrote {OUT_METRICS}")


if __name__ == "__main__":
    main()
