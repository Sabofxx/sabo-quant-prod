"""
Sandbox - prop-firm hybrid test: existing FX MR stack + new H1 cross-asset edges.

This script reuses the prop-firm cash simulator and adds H1 strategies discovered
from the expanded Dukascopy bid/ask dataset. H1 daily strategies get an intraday
adverse-excursion estimate from the H1 path, so daily-loss rules are still
stress-tested.
"""
from __future__ import annotations

import json
import zlib
from pathlib import Path

import pandas as pd

import strategy_h1_edge_miner as h1
import strategy_propfirm_cashmax as cash


HERE = Path(__file__).parent
OUT_REPORT = HERE / "strategy_propfirm_hybrid_h1_report.md"
OUT_METRICS = HERE / "strategy_propfirm_hybrid_h1_metrics.json"

N_MC = 3000
HORIZON_DAYS = 504
SEED = 20260522
OOS_START = cash.OOS_START

H1_SELECTED = {
    "H1_NASDAQ_TREND": ("usatechidxusd", "MA_LONG", 200),
    "H1_CHFJPY_BREAK": ("chfjpy", "DON_BREAK", 100),
    "H1_DOW_TSM": ("usa30idxusd", "TSM", 5),
}

HYBRID_PORTFOLIOS = {
    "baseline_full_budget_mixed_2x500_4x200": cash.CANDIDATE_PORTFOLIOS["full_budget_mixed_2x500_4x200"],
    "baseline_8x200_diversified": cash.CANDIDATE_PORTFOLIOS["full_budget_8x200_diversified"],
    "hybrid_8x200_fx_h1": [
        cash.AccountPlan("A1", "LOW_TARGET_200K", "FX_MR_STACK", 0.10),
        cash.AccountPlan("A2", "LOW_TARGET_200K", "EURUSD_MR5", 0.10),
        cash.AccountPlan("A3", "LOW_TARGET_200K", "NO_EUR_STACK", 0.10),
        cash.AccountPlan("A4", "LOW_TARGET_200K", "COMDOLL_STACK", 0.10),
        cash.AccountPlan("A5", "LOW_TARGET_200K", "H1_MULTI", 0.10),
        cash.AccountPlan("A6", "LOW_TARGET_200K", "H1_NASDAQ_TREND", 0.08),
        cash.AccountPlan("A7", "LOW_TARGET_200K", "H1_DOW_TSM", 0.10),
        cash.AccountPlan("A8", "LOW_TARGET_200K", "H1_CHFJPY_BREAK", 0.10),
    ],
    "hybrid_2x500_4x200": [
        cash.AccountPlan("A1", "LOW_TARGET_500K_TRAIL", "FX_MR_STACK", 0.08),
        cash.AccountPlan("A2", "LOW_TARGET_500K_TRAIL", "EURUSD_MR5", 0.08),
        cash.AccountPlan("A3", "LOW_TARGET_200K", "H1_MULTI", 0.10),
        cash.AccountPlan("A4", "LOW_TARGET_200K", "H1_NASDAQ_TREND", 0.08),
        cash.AccountPlan("A5", "LOW_TARGET_200K", "H1_DOW_TSM", 0.10),
        cash.AccountPlan("A6", "LOW_TARGET_200K", "NO_EUR_STACK", 0.10),
    ],
    "hybrid_h1_heavy_6x200": [
        cash.AccountPlan("A1", "LOW_TARGET_200K", "H1_MULTI", 0.10),
        cash.AccountPlan("A2", "LOW_TARGET_200K", "H1_NASDAQ_TREND", 0.08),
        cash.AccountPlan("A3", "LOW_TARGET_200K", "H1_DOW_TSM", 0.10),
        cash.AccountPlan("A4", "LOW_TARGET_200K", "H1_CHFJPY_BREAK", 0.10),
        cash.AccountPlan("A5", "LOW_TARGET_200K", "FX_MR_STACK", 0.10),
        cash.AccountPlan("A6", "LOW_TARGET_200K", "EURUSD_MR5", 0.10),
    ],
    "hybrid_blend_2x500_4x200": [
        cash.AccountPlan("A1", "LOW_TARGET_500K_TRAIL", "HYBRID_FX_H1", 0.08),
        cash.AccountPlan("A2", "LOW_TARGET_500K_TRAIL", "EURUSD_MR5", 0.08),
        cash.AccountPlan("A3", "LOW_TARGET_200K", "HYBRID_FX_H1", 0.10),
        cash.AccountPlan("A4", "LOW_TARGET_200K", "NO_EUR_STACK", 0.10),
        cash.AccountPlan("A5", "LOW_TARGET_200K", "COMDOLL_STACK", 0.10),
        cash.AccountPlan("A6", "LOW_TARGET_200K", "FAST_STACK", 0.12),
    ],
    "hybrid_blend_8x200": [
        cash.AccountPlan("A1", "LOW_TARGET_200K", "HYBRID_FX_H1", 0.10),
        cash.AccountPlan("A2", "LOW_TARGET_200K", "FX_MR_STACK", 0.10),
        cash.AccountPlan("A3", "LOW_TARGET_200K", "EURUSD_MR5", 0.10),
        cash.AccountPlan("A4", "LOW_TARGET_200K", "NO_EUR_STACK", 0.10),
        cash.AccountPlan("A5", "LOW_TARGET_200K", "COMDOLL_STACK", 0.10),
        cash.AccountPlan("A6", "LOW_TARGET_200K", "FAST_STACK", 0.12),
        cash.AccountPlan("A7", "LOW_TARGET_200K", "H1_MULTI", 0.10),
        cash.AccountPlan("A8", "LOW_TARGET_200K", "H1_NASDAQ_TREND", 0.08),
    ],
    "hybrid_blend_6x200_cashmax": [
        cash.AccountPlan("A1", "LOW_TARGET_200K", "HYBRID_FX_H1", 0.10),
        cash.AccountPlan("A2", "LOW_TARGET_200K", "FX_MR_STACK", 0.10),
        cash.AccountPlan("A3", "LOW_TARGET_200K", "EURUSD_MR5", 0.10),
        cash.AccountPlan("A4", "LOW_TARGET_200K", "NO_EUR_STACK", 0.10),
        cash.AccountPlan("A5", "LOW_TARGET_200K", "COMDOLL_STACK", 0.10),
        cash.AccountPlan("A6", "LOW_TARGET_200K", "FAST_STACK", 0.12),
    ],
}


def stable_seed(label: str) -> int:
    return SEED + zlib.crc32(label.encode("utf-8")) % 100_000


def daily_signal(symbol: str, family: str, lookback: int, prices: pd.DataFrame) -> pd.Series:
    daily_prices = prices.resample("1D").last().dropna()
    if family == "MA_LONG":
        return h1.signal_ma_filter_daily(daily_prices, lookback, "LONG")
    if family == "DON_BREAK":
        return h1.signal_donchian_daily(daily_prices, lookback, "BREAK")
    if family == "TSM":
        return h1.signal_mr_tsm_daily(daily_prices, lookback, "TSM")
    raise ValueError(f"unknown H1 family for {symbol}: {family}")


def h1_daily_path_returns(symbol: str, family: str, lookback: int) -> tuple[pd.Series, pd.Series]:
    prices = h1.load_symbol(symbol)
    if prices.empty:
        raise RuntimeError(f"empty H1 prices for {symbol}")

    daily_prices = prices.resample("1D").last().dropna()
    signal = daily_signal(symbol, family, lookback, prices).reindex(daily_prices.index).fillna(0.0).clip(-1.0, 1.0)

    frame = prices.copy()
    frame["day"] = frame.index.floor("D")
    frame["signal"] = frame["day"].map(signal.shift(1))
    frame["entry_ask"] = frame["day"].map(daily_prices["ask_close"].shift(1))
    frame["entry_bid"] = frame["day"].map(daily_prices["bid_close"].shift(1))
    frame.dropna(subset=["signal", "entry_ask", "entry_bid"], inplace=True)

    long_path = frame["bid_close"] / frame["entry_ask"] - 1.0
    short_path = frame["entry_bid"] / frame["ask_close"] - 1.0
    frame["path"] = (frame["signal"] > 0) * long_path + (frame["signal"] < 0) * short_path
    frame.loc[frame["signal"] == 0, "path"] = 0.0

    close_daily = frame.groupby("day")["path"].last()
    intraday_min = frame.groupby("day")["path"].min()
    common = close_daily.index.intersection(intraday_min.index)
    return close_daily.loc[common], intraday_min.loc[common]


def combine_specs(specs: dict[str, dict], names: list[str]) -> dict:
    daily = pd.DataFrame({name: specs[name]["daily"] for name in names}).fillna(0.0).mean(axis=1)
    intraday = pd.DataFrame({name: specs[name]["intraday"] for name in names}).fillna(0.0).mean(axis=1)
    common = daily.index.intersection(intraday.index)
    return {"daily": daily.loc[common], "intraday": intraday.loc[common], "members": names}


def build_hybrid_universe() -> dict[str, dict]:
    specs = cash.build_strategy_universe()
    for name, (symbol, family, lookback) in H1_SELECTED.items():
        daily, intraday = h1_daily_path_returns(symbol, family, lookback)
        specs[name] = {"daily": daily, "intraday": intraday, "members": [(symbol, family, lookback)]}
    specs["H1_MULTI"] = combine_specs(specs, list(H1_SELECTED))
    specs["HYBRID_FX_H1"] = combine_specs(specs, ["FX_MR_STACK", "H1_MULTI"])
    return specs


def oos_metrics(specs: dict[str, dict]) -> dict:
    out = {}
    for name, values in specs.items():
        daily = values["daily"]
        intraday = values["intraday"]
        oos = daily.loc[daily.index >= OOS_START]
        oos_intraday = intraday.reindex(oos.index)
        metrics = cash.metrics(oos)
        metrics["worst_intraday"] = float(oos_intraday.min())
        out[name] = metrics
    return out


def build_levered(specs: dict[str, dict], targets: list[float]) -> tuple[dict, dict]:
    levered = {}
    levered_metrics = {}
    for name, values in specs.items():
        for target in targets:
            close_lev, intraday_lev = cash.vol_target_returns(values["daily"], values["intraday"], target)
            oos = close_lev.loc[close_lev.index >= OOS_START]
            oos_intraday = intraday_lev.reindex(oos.index)
            levered[(name, target)] = (oos, oos_intraday)
            metrics = cash.metrics(oos)
            metrics["worst_intraday"] = float(oos_intraday.min())
            levered_metrics[f"{name}_{int(target * 100)}"] = metrics
    return levered, levered_metrics


def main() -> None:
    specs = build_hybrid_universe()
    spec_metrics = oos_metrics(specs)
    targets = [0.08, 0.10, 0.12]
    levered, levered_metrics = build_levered(specs, targets)

    single_results = {}
    plan = cash.FIRM_PLANS["LOW_TARGET_200K"]
    for spec_name in ["FX_MR_STACK", "EURUSD_MR5", "H1_MULTI", *H1_SELECTED, "HYBRID_FX_H1"]:
        for target in targets:
            close, intraday = levered[(spec_name, target)]
            key = f"{spec_name}_{int(target * 100)}"
            single_results[key] = cash.single_account_mc(
                close,
                intraday,
                plan,
                N_MC,
                HORIZON_DAYS,
                stable_seed(key),
            )

    portfolio_results = {}
    for name, portfolio in HYBRID_PORTFOLIOS.items():
        portfolio_results[name] = cash.portfolio_mc(
            portfolio,
            levered,
            N_MC,
            HORIZON_DAYS,
            stable_seed(name),
        )

    corr_names = [
        "FX_MR_STACK",
        "EURUSD_MR5",
        "NO_EUR_STACK",
        "H1_MULTI",
        "H1_NASDAQ_TREND",
        "H1_CHFJPY_BREAK",
        "H1_DOW_TSM",
    ]
    corr = pd.DataFrame({name: levered[(name, 0.10)][0] for name in corr_names}).dropna().corr()

    best_name = max(portfolio_results, key=lambda key: portfolio_results[key]["net_mean"])

    lines: list[str] = []
    lines.append("# Prop Firm Hybrid H1 Report")
    lines.append("")
    lines.append(f"MC paths: {N_MC}. Horizon: {HORIZON_DAYS} trading days.")
    lines.append("")
    lines.append("## OOS strategy metrics")
    lines.append("")
    lines.append("| spec | Sh | ann_ret% | vol% | maxDD% | win% | worst day% | worst intraday% |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for name, metrics in sorted(spec_metrics.items(), key=lambda item: item[1]["sharpe"], reverse=True):
        if name not in corr_names and not name.startswith("H1") and name != "HYBRID_FX_H1":
            continue
        lines.append(
            f"| {name} | {metrics['sharpe']:+.2f} | {metrics['ann_ret']*100:+.1f} | "
            f"{metrics['ann_vol']*100:.1f} | {metrics['max_dd']*100:+.1f} | "
            f"{metrics['wr']*100:.1f} | {metrics['worst_day']*100:+.2f} | "
            f"{metrics['worst_intraday']*100:+.2f} |"
        )
    lines.append("")
    lines.append("## Correlation at 10% vol")
    lines.append("")
    lines.append("```")
    lines.append(corr.round(2).to_string())
    lines.append("```")
    lines.append("")
    lines.append("## Single 200k low-target account")
    lines.append("")
    lines.append("| spec_tv | P funded | P dead | E payout | E net | median days funded |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for key, result in sorted(single_results.items(), key=lambda item: item[1]["mean_net"], reverse=True):
        med_days = "" if result["median_days_to_funded"] is None else f"{result['median_days_to_funded']:.0f}"
        lines.append(
            f"| {key} | {result['p_funded']*100:.0f}% | {result['p_dead']*100:.0f}% | "
            f"${result['mean_payout']:.0f} | ${result['mean_net']:.0f} | {med_days} |"
        )
    lines.append("")
    lines.append("## Portfolio candidates")
    lines.append("")
    lines.append("| portfolio | fees | E net | median net | P net+ | P >=1 funded | avg funded | p95 net |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for name, result in sorted(portfolio_results.items(), key=lambda item: item[1]["net_mean"], reverse=True):
        lines.append(
            f"| {name} | ${result['fees']:.0f} | ${result['net_mean']:.0f} | "
            f"${result['net_median']:.0f} | {result['p_net_positive']*100:.0f}% | "
            f"{result['p_at_least_one_funded']*100:.0f}% | {result['avg_funded']:.1f} | "
            f"${result['net_p95']:.0f} |"
        )
    lines.append("")
    lines.append("## Best allocation")
    lines.append("")
    lines.append(f"Best by expected net cash-out: **{best_name}**.")
    for account in HYBRID_PORTFOLIOS[best_name]:
        plan = cash.FIRM_PLANS[account.firm]
        lines.append(
            f"- {account.name}: {account.firm} ${plan.account_size:.0f}, "
            f"{account.spec}, vol {account.vol_target*100:.0f}%"
        )
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append("- H1 adds diversification mainly through index trend and one CHFJPY breakout signal.")
    lines.append("- Oil candidates were useful in the H1 miner, but their intraday path is too rough for prop daily-loss rules at high size.")
    lines.append("- The comparison keeps the previous FX-only best portfolios as baselines.")

    OUT_REPORT.write_text("\n".join(lines), encoding="utf-8")
    OUT_METRICS.write_text(
        json.dumps(
            {
                "spec_metrics": spec_metrics,
                "levered_metrics": levered_metrics,
                "single_results": single_results,
                "portfolio_results": portfolio_results,
                "best": best_name,
                "best_accounts": [account.__dict__ for account in HYBRID_PORTFOLIOS[best_name]],
                "correlation_10pct": corr.round(4).to_dict(),
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    print(f"Best portfolio: {best_name}")
    print(f"Report: {OUT_REPORT}")
    print(f"Metrics: {OUT_METRICS}")


if __name__ == "__main__":
    main()
