"""
Sandbox — prop firm cash-max optimizer.

Goal: choose the setup with the highest expected cash-out, not the prettiest
backtest metric.

This script differs from the previous prop-firm scripts:
  - Uses current no-time-limit style evaluation rules for major CFD firms.
  - Models easier 5%/5% two-step plans separately from classic 10%/5%.
  - Uses M5 data to estimate intraday adverse excursion, not only daily closes.
  - Tests multiple FX MR variants so accounts do not all take identical trades.
  - Simulates portfolios with common bootstrapped dates to preserve correlation.
"""
from __future__ import annotations

import json
import math
import random
import zlib
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


HERE = Path(__file__).parent
DATA = HERE / "data"
OUT_REPORT = HERE / "strategy_propfirm_cashmax_report.md"
OUT_METRICS = HERE / "strategy_propfirm_cashmax_metrics.json"

FX_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD"]
FX_PIP = {
    "EURUSD": 0.0001,
    "GBPUSD": 0.0001,
    "AUDUSD": 0.0001,
    "NZDUSD": 0.0001,
    "USDCAD": 0.0001,
    "USDJPY": 0.01,
}
FX_RT_PIPS = {
    "EURUSD": 1.9,
    "GBPUSD": 2.3,
    "USDJPY": 2.1,
    "AUDUSD": 2.3,
    "NZDUSD": 3.1,
    "USDCAD": 2.7,
}
FX_BEST_LB = {
    "EURUSD": 5,
    "GBPUSD": 3,
    "USDJPY": 10,
    "AUDUSD": 21,
    "NZDUSD": 10,
    "USDCAD": 3,
}
FX_FILES = {pair: f"{pair.lower()}-m5-bid-2019-01-01-2026-01-01.csv" for pair in FX_PAIRS}

IS_END = pd.Timestamp("2023-12-31 23:59:59", tz="UTC")
OOS_START = pd.Timestamp("2024-01-01", tz="UTC")
ANN_DAYS = 252
VOL_LOOKBACK = 60
MAX_LEVERAGE = 10.0
N_MC = 5000
HORIZON_DAYS = 504
SEED = 20260521


@dataclass(frozen=True)
class FirmPlan:
    name: str
    account_size: float
    fee: float
    targets: tuple[float, ...]
    max_daily_loss: float
    max_overall_loss: float
    profit_share: float
    overall_mode: str
    daily_profit_cap_eval: float | None = None
    fee_refund_after_first_payout: bool = False


@dataclass(frozen=True)
class AccountPlan:
    name: str
    firm: str
    spec: str
    vol_target: float
    funded_vol_target: float | None = None


FIRM_PLANS = {
    "LOW_TARGET_200K": FirmPlan(
        name="LOW_TARGET_200K",
        account_size=200_000,
        fee=1_200,
        targets=(0.05, 0.05),
        max_daily_loss=0.05,
        max_overall_loss=0.10,
        profit_share=0.80,
        overall_mode="static",
        daily_profit_cap_eval=0.02,
        fee_refund_after_first_payout=True,
    ),
    "CLASSIC_200K": FirmPlan(
        name="CLASSIC_200K",
        account_size=200_000,
        fee=1_080,
        targets=(0.10, 0.05),
        max_daily_loss=0.05,
        max_overall_loss=0.10,
        profit_share=0.80,
        overall_mode="static",
    ),
    "ONE_STEP_200K": FirmPlan(
        name="ONE_STEP_200K",
        account_size=200_000,
        fee=1_080,
        targets=(0.10,),
        max_daily_loss=0.03,
        max_overall_loss=0.10,
        profit_share=0.90,
        overall_mode="eod_trailing",
    ),
    "LOW_TARGET_500K_TRAIL": FirmPlan(
        name="LOW_TARGET_500K_TRAIL",
        account_size=500_000,
        fee=2_400,
        targets=(0.08, 0.05),
        max_daily_loss=0.05,
        max_overall_loss=0.10,
        profit_share=0.80,
        overall_mode="eod_trailing",
        daily_profit_cap_eval=0.05,
        fee_refund_after_first_payout=True,
    ),
}


CANDIDATE_PORTFOLIOS = {
    "single_best_low_target": [
        AccountPlan("A1", "LOW_TARGET_200K", "FX_MR_STACK", 0.10),
    ],
    "cashmax_5x_200k_diversified": [
        AccountPlan("A1", "LOW_TARGET_200K", "FX_MR_STACK", 0.10),
        AccountPlan("A2", "LOW_TARGET_200K", "EURUSD_MR5", 0.10),
        AccountPlan("A3", "LOW_TARGET_200K", "NO_EUR_STACK", 0.10),
        AccountPlan("A4", "LOW_TARGET_200K", "COMDOLL_STACK", 0.10),
        AccountPlan("A5", "LOW_TARGET_200K", "FAST_STACK", 0.10),
    ],
    "cashmax_5x_200k_aggressive": [
        AccountPlan("A1", "LOW_TARGET_200K", "FX_MR_STACK", 0.12),
        AccountPlan("A2", "LOW_TARGET_200K", "EURUSD_MR5", 0.12),
        AccountPlan("A3", "LOW_TARGET_200K", "NO_EUR_STACK", 0.12),
        AccountPlan("A4", "LOW_TARGET_200K", "COMDOLL_STACK", 0.12),
        AccountPlan("A5", "LOW_TARGET_200K", "FAST_STACK", 0.12),
    ],
    "classic_5x_200k_diversified": [
        AccountPlan("A1", "CLASSIC_200K", "FX_MR_STACK", 0.12),
        AccountPlan("A2", "CLASSIC_200K", "EURUSD_MR5", 0.12),
        AccountPlan("A3", "CLASSIC_200K", "NO_EUR_STACK", 0.12),
        AccountPlan("A4", "CLASSIC_200K", "COMDOLL_STACK", 0.12),
        AccountPlan("A5", "CLASSIC_200K", "FAST_STACK", 0.12),
    ],
    "mixed_large_diversified": [
        AccountPlan("A1", "LOW_TARGET_500K_TRAIL", "FX_MR_STACK", 0.08),
        AccountPlan("A2", "LOW_TARGET_500K_TRAIL", "EURUSD_MR5", 0.08),
        AccountPlan("A3", "LOW_TARGET_200K", "NO_EUR_STACK", 0.10),
        AccountPlan("A4", "LOW_TARGET_200K", "COMDOLL_STACK", 0.10),
    ],
    "full_budget_mixed_2x500_4x200": [
        AccountPlan("A1", "LOW_TARGET_500K_TRAIL", "FX_MR_STACK", 0.08),
        AccountPlan("A2", "LOW_TARGET_500K_TRAIL", "EURUSD_MR5", 0.08),
        AccountPlan("A3", "LOW_TARGET_200K", "NO_EUR_STACK", 0.10),
        AccountPlan("A4", "LOW_TARGET_200K", "COMDOLL_STACK", 0.10),
        AccountPlan("A5", "LOW_TARGET_200K", "FAST_STACK", 0.12),
        AccountPlan("A6", "LOW_TARGET_200K", "FX_MR_STACK", 0.10),
    ],
    "improved_lowvol_2x500_4x200": [
        AccountPlan("A1", "LOW_TARGET_500K_TRAIL", "FX_MR_STACK", 0.08),
        AccountPlan("A2", "LOW_TARGET_500K_TRAIL", "EURUSD_MR5", 0.08),
        AccountPlan("A3", "LOW_TARGET_200K", "ANTIPODEAN_LOW_VOL", 0.12),
        AccountPlan("A4", "LOW_TARGET_200K", "COMDOLL_LOW_VOL", 0.12),
        AccountPlan("A5", "LOW_TARGET_200K", "NO_EUR_LOW_VOL", 0.12),
        AccountPlan("A6", "LOW_TARGET_200K", "FX_MR_STACK", 0.10),
    ],
    "improved_lowvol_6x200": [
        AccountPlan("A1", "LOW_TARGET_200K", "FX_MR_STACK", 0.10),
        AccountPlan("A2", "LOW_TARGET_200K", "EURUSD_MR5", 0.12),
        AccountPlan("A3", "LOW_TARGET_200K", "ANTIPODEAN_LOW_VOL", 0.12),
        AccountPlan("A4", "LOW_TARGET_200K", "COMDOLL_LOW_VOL", 0.12),
        AccountPlan("A5", "LOW_TARGET_200K", "NO_EUR_LOW_VOL", 0.12),
        AccountPlan("A6", "LOW_TARGET_200K", "FAST_STACK", 0.12),
    ],
    "phased_eval10_funded8_2x500_4x200": [
        AccountPlan("A1", "LOW_TARGET_500K_TRAIL", "FX_MR_STACK", 0.08, 0.08),
        AccountPlan("A2", "LOW_TARGET_500K_TRAIL", "EURUSD_MR5", 0.08, 0.08),
        AccountPlan("A3", "LOW_TARGET_200K", "NO_EUR_STACK", 0.10, 0.08),
        AccountPlan("A4", "LOW_TARGET_200K", "COMDOLL_STACK", 0.10, 0.08),
        AccountPlan("A5", "LOW_TARGET_200K", "FAST_STACK", 0.12, 0.08),
        AccountPlan("A6", "LOW_TARGET_200K", "FX_MR_STACK", 0.10, 0.08),
    ],
    "phased_fast_eval_safe_funded": [
        AccountPlan("A1", "LOW_TARGET_500K_TRAIL", "FX_MR_STACK", 0.10, 0.08),
        AccountPlan("A2", "LOW_TARGET_500K_TRAIL", "EURUSD_MR5", 0.10, 0.08),
        AccountPlan("A3", "LOW_TARGET_200K", "NO_EUR_STACK", 0.10, 0.08),
        AccountPlan("A4", "LOW_TARGET_200K", "COMDOLL_STACK", 0.12, 0.08),
        AccountPlan("A5", "LOW_TARGET_200K", "FAST_STACK", 0.12, 0.08),
        AccountPlan("A6", "LOW_TARGET_200K", "FX_MR_STACK", 0.10, 0.08),
    ],
    "full_budget_8x200_diversified": [
        AccountPlan("A1", "LOW_TARGET_200K", "FX_MR_STACK", 0.10),
        AccountPlan("A2", "LOW_TARGET_200K", "EURUSD_MR5", 0.12),
        AccountPlan("A3", "LOW_TARGET_200K", "NO_EUR_STACK", 0.10),
        AccountPlan("A4", "LOW_TARGET_200K", "COMDOLL_STACK", 0.12),
        AccountPlan("A5", "LOW_TARGET_200K", "FAST_STACK", 0.12),
        AccountPlan("A6", "LOW_TARGET_200K", "SLOW_STACK", 0.12),
        AccountPlan("A7", "LOW_TARGET_200K", "MR5_ALL", 0.12),
        AccountPlan("A8", "LOW_TARGET_200K", "FX_MR_STACK", 0.08),
    ],
    "upper_bound_4x500_trail": [
        AccountPlan("A1", "LOW_TARGET_500K_TRAIL", "FX_MR_STACK", 0.08),
        AccountPlan("A2", "LOW_TARGET_500K_TRAIL", "EURUSD_MR5", 0.08),
        AccountPlan("A3", "LOW_TARGET_500K_TRAIL", "NO_EUR_STACK", 0.08),
        AccountPlan("A4", "LOW_TARGET_500K_TRAIL", "COMDOLL_STACK", 0.08),
    ],
    "one_step_fast": [
        AccountPlan("A1", "ONE_STEP_200K", "FX_MR_STACK", 0.08),
        AccountPlan("A2", "ONE_STEP_200K", "EURUSD_MR5", 0.08),
        AccountPlan("A3", "ONE_STEP_200K", "NO_EUR_STACK", 0.08),
        AccountPlan("A4", "ONE_STEP_200K", "COMDOLL_STACK", 0.08),
        AccountPlan("A5", "ONE_STEP_200K", "FAST_STACK", 0.08),
    ],
}


def load_m5_close(pair: str) -> pd.Series:
    df = pd.read_csv(DATA / FX_FILES[pair])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    return df["close"].astype(float)


def stable_seed(label: str) -> int:
    return SEED + zlib.crc32(label.encode("utf-8")) % 100_000


def mr_signal(rets: pd.Series, lookback: int) -> pd.Series:
    cumulative = (1.0 + rets).rolling(lookback).apply(lambda values: values.prod() - 1.0, raw=True)
    signal = (cumulative < 0).astype(float) - (cumulative > 0).astype(float)
    return signal.shift(1).dropna()


def pair_daily_net(pair: str, daily_close: pd.Series, lookback: int) -> pd.Series:
    rets = daily_close.pct_change()
    signal = mr_signal(rets, lookback)
    common = signal.index.intersection(rets.index)
    gross = signal.loc[common] * rets.loc[common]
    turnover = signal.diff().abs().fillna(0.0) / 2.0
    cost = (turnover * FX_RT_PIPS[pair] * FX_PIP[pair] / daily_close).reindex(common).fillna(0.0)
    return gross - cost


def pair_intraday_min(pair: str, m5_close: pd.Series, daily_close: pd.Series, lookback: int) -> pd.Series:
    rets = daily_close.pct_change()
    signal = mr_signal(rets, lookback)
    frame = pd.DataFrame({"close": m5_close})
    frame["day"] = frame.index.floor("D")
    prev_close_by_day = daily_close.shift(1)
    frame["prev_close"] = frame["day"].map(prev_close_by_day)
    frame["signal"] = frame["day"].map(signal)
    frame.dropna(inplace=True)
    frame["path_pnl"] = frame["signal"] * (frame["close"] / frame["prev_close"] - 1.0)
    return frame.groupby("day")["path_pnl"].min()


def combine_spec(
    members: list[tuple[str, int]],
    daily_net_by_pair_lb: dict[tuple[str, int], pd.Series],
    intraday_min_by_pair_lb: dict[tuple[str, int], pd.Series],
) -> tuple[pd.Series, pd.Series]:
    daily = pd.DataFrame({f"{pair}_{lookback}": daily_net_by_pair_lb[(pair, lookback)] for pair, lookback in members})
    intraday = pd.DataFrame(
        {f"{pair}_{lookback}": intraday_min_by_pair_lb[(pair, lookback)] for pair, lookback in members}
    )
    return daily.fillna(0.0).sum(axis=1) / len(members), intraday.fillna(0.0).sum(axis=1) / len(members)


def low_vol_gate(daily: pd.Series, intraday: pd.Series) -> tuple[pd.Series, pd.Series]:
    realized = daily.rolling(60).std().shift(1)
    rank = realized.rolling(252, min_periods=80).rank(pct=True)
    gate = (rank <= 0.50).fillna(False)
    daily_gated = daily.where(gate, 0.0)
    intraday_gated = intraday.where(gate.reindex(intraday.index).fillna(False), 0.0)
    return daily_gated, intraday_gated


def max_drawdown(pl: pd.Series) -> float:
    cumulative = pl.cumsum()
    return float((cumulative - cumulative.cummax()).min())


def metrics(pl: pd.Series) -> dict:
    pl = pl.dropna()
    if len(pl) < 2:
        return {"n": len(pl), "sharpe": 0.0, "ann_ret": 0.0, "ann_vol": 0.0, "max_dd": 0.0, "wr": 0.0}
    std = float(pl.std())
    mean = float(pl.mean())
    return {
        "n": len(pl),
        "sharpe": mean / std * math.sqrt(ANN_DAYS) if std > 0 else 0.0,
        "ann_ret": mean * ANN_DAYS,
        "ann_vol": std * math.sqrt(ANN_DAYS),
        "max_dd": max_drawdown(pl),
        "wr": float((pl > 0).mean()),
        "worst_day": float(pl.min()),
    }


def vol_target_returns(close_pl: pd.Series, intraday_min: pd.Series, target_vol: float) -> tuple[pd.Series, pd.Series]:
    realized = close_pl.rolling(VOL_LOOKBACK).std() * math.sqrt(ANN_DAYS)
    leverage = (target_vol / realized).clip(upper=MAX_LEVERAGE).shift(1).fillna(1.0)
    close_lev = (close_pl * leverage).dropna()
    intraday_lev = (intraday_min.reindex(close_lev.index).fillna(close_pl) * leverage.reindex(close_lev.index)).dropna()
    common = close_lev.index.intersection(intraday_lev.index)
    return close_lev.loc[common], intraday_lev.loc[common]


def build_strategy_universe() -> dict[str, dict]:
    print("Loading M5 closes...")
    m5 = {pair: load_m5_close(pair) for pair in FX_PAIRS}
    daily_close = {pair: series.resample("1D").last().dropna() for pair, series in m5.items()}

    needed_members = set()
    for pair, lookback in FX_BEST_LB.items():
        needed_members.add((pair, lookback))
    for pair in FX_PAIRS:
        needed_members.add((pair, 5))
        needed_members.add((pair, 21))
    needed_members.add(("EURUSD", 3))
    needed_members.add(("GBPUSD", 3))
    needed_members.add(("USDCAD", 3))

    print(f"Computing {len(needed_members)} pair/lookback return streams with M5 intraday risk...")
    daily_net_by_pair_lb = {}
    intraday_min_by_pair_lb = {}
    for pair, lookback in sorted(needed_members):
        daily_net_by_pair_lb[(pair, lookback)] = pair_daily_net(pair, daily_close[pair], lookback)
        intraday_min_by_pair_lb[(pair, lookback)] = pair_intraday_min(pair, m5[pair], daily_close[pair], lookback)

    best_members = [(pair, lookback) for pair, lookback in FX_BEST_LB.items()]
    specs_members = {
        "FX_MR_STACK": best_members,
        "EURUSD_MR5": [("EURUSD", 5)],
        "NO_EUR_STACK": [(pair, FX_BEST_LB[pair]) for pair in FX_PAIRS if pair != "EURUSD"],
        "COMDOLL_STACK": [("AUDUSD", 21), ("NZDUSD", 10), ("USDCAD", 3)],
        "FAST_STACK": [("EURUSD", 3), ("GBPUSD", 3), ("USDCAD", 3)],
        "SLOW_STACK": [(pair, 21) for pair in FX_PAIRS],
        "MR5_ALL": [(pair, 5) for pair in FX_PAIRS],
    }

    specs = {}
    for name, members in specs_members.items():
        daily, intraday = combine_spec(members, daily_net_by_pair_lb, intraday_min_by_pair_lb)
        common = daily.index.intersection(intraday.index)
        specs[name] = {"daily": daily.loc[common], "intraday": intraday.loc[common], "members": members}

    low_vol_sources = {
        "ANTIPODEAN_LOW_VOL": [("AUDUSD", 21), ("NZDUSD", 10)],
        "COMDOLL_LOW_VOL": [("AUDUSD", 21), ("NZDUSD", 10), ("USDCAD", 3)],
        "NO_EUR_LOW_VOL": [(pair, FX_BEST_LB[pair]) for pair in FX_PAIRS if pair != "EURUSD"],
    }
    for name, members in low_vol_sources.items():
        daily, intraday = combine_spec(members, daily_net_by_pair_lb, intraday_min_by_pair_lb)
        daily, intraday = low_vol_gate(daily, intraday)
        common = daily.index.intersection(intraday.index)
        specs[name] = {"daily": daily.loc[common], "intraday": intraday.loc[common], "members": members}
    return specs


def breaches_overall(cum_pct: float, intraday_min_pct: float, peak_pct: float, plan: FirmPlan) -> bool:
    if plan.overall_mode == "static":
        return cum_pct + intraday_min_pct <= -plan.max_overall_loss
    if plan.overall_mode == "eod_trailing":
        floor = min(peak_pct - plan.max_overall_loss, 0.0)
        return cum_pct + intraday_min_pct <= floor
    raise ValueError(f"unknown overall mode: {plan.overall_mode}")


def apply_eval_daily_cap(close_r: float, intraday_min_r: float, plan: FirmPlan, funded: bool) -> tuple[float, float]:
    if funded or plan.daily_profit_cap_eval is None or close_r <= plan.daily_profit_cap_eval:
        return close_r, intraday_min_r
    return plan.daily_profit_cap_eval, min(intraday_min_r, plan.daily_profit_cap_eval)


def simulate_account_on_path(
    close_path: list[float],
    intraday_path: list[float],
    plan: FirmPlan,
    account_size: float,
    funded_close_path: list[float] | None = None,
    funded_intraday_path: list[float] | None = None,
) -> dict:
    state_idx = 0
    state = "EVAL_1"
    cum_pct = 0.0
    peak_pct = 0.0
    funded_cum = 0.0
    payouts = 0.0
    first_payout_done = False
    reached_funded = False
    days_to_funded: int | None = None

    if funded_close_path is None:
        funded_close_path = close_path
    if funded_intraday_path is None:
        funded_intraday_path = intraday_path

    for day_idx, (eval_close_raw, eval_intraday_raw, funded_close_raw, funded_intraday_raw) in enumerate(
        zip(close_path, intraday_path, funded_close_path, funded_intraday_path, strict=True),
        start=1,
    ):
        funded = state == "FUNDED"
        close_r_raw = funded_close_raw if funded else eval_close_raw
        intraday_min_raw = funded_intraday_raw if funded else eval_intraday_raw
        close_r, intraday_min = apply_eval_daily_cap(close_r_raw, intraday_min_raw, plan, funded)
        if intraday_min <= -plan.max_daily_loss:
            return {
                "final_state": "DEAD_DAILY",
                "reached_funded": reached_funded,
                "payouts": payouts,
                "days_to_funded": days_to_funded,
            }
        if breaches_overall(cum_pct, intraday_min, peak_pct, plan):
            return {
                "final_state": "DEAD_DD",
                "reached_funded": reached_funded,
                "payouts": payouts,
                "days_to_funded": days_to_funded,
            }

        cum_pct += close_r
        peak_pct = max(peak_pct, cum_pct)

        if state.startswith("EVAL"):
            target = plan.targets[state_idx]
            if cum_pct >= target:
                state_idx += 1
                if state_idx == len(plan.targets):
                    state = "FUNDED"
                    reached_funded = True
                    days_to_funded = day_idx
                    cum_pct = 0.0
                    peak_pct = 0.0
                    funded_cum = 0.0
                else:
                    state = f"EVAL_{state_idx + 1}"
                    cum_pct = 0.0
                    peak_pct = 0.0
        else:
            funded_cum += close_r
            if day_idx % 21 == 0 and funded_cum > 0.0:
                payout = funded_cum * plan.profit_share * account_size
                payouts += payout
                cum_pct -= funded_cum
                funded_cum = 0.0
                if plan.fee_refund_after_first_payout and not first_payout_done:
                    payouts += plan.fee
                    first_payout_done = True

    return {
        "final_state": state,
        "reached_funded": reached_funded,
        "payouts": payouts,
        "days_to_funded": days_to_funded,
    }


def single_account_mc(
    close: pd.Series,
    intraday: pd.Series,
    plan: FirmPlan,
    n_iter: int,
    horizon_days: int,
    seed: int,
) -> dict:
    rng = random.Random(seed)
    common = close.index.intersection(intraday.index)
    close_values = close.loc[common].to_list()
    intraday_values = intraday.loc[common].to_list()
    n = len(common)
    outcomes = []
    for _ in range(n_iter):
        picks = [rng.randrange(n) for _ in range(horizon_days)]
        close_path = [close_values[idx] for idx in picks]
        intraday_path = [intraday_values[idx] for idx in picks]
        outcomes.append(simulate_account_on_path(close_path, intraday_path, plan, plan.account_size))

    payouts = sorted(outcome["payouts"] for outcome in outcomes)
    days = [outcome["days_to_funded"] for outcome in outcomes if outcome["days_to_funded"] is not None]
    return {
        "p_funded": sum(outcome["reached_funded"] for outcome in outcomes) / n_iter,
        "p_dead": sum(outcome["final_state"].startswith("DEAD") for outcome in outcomes) / n_iter,
        "mean_payout": sum(payouts) / n_iter,
        "median_payout": payouts[n_iter // 2],
        "p95_payout": payouts[int(n_iter * 0.95)],
        "mean_net": sum(payouts) / n_iter - plan.fee,
        "median_days_to_funded": float(pd.Series(days).median()) if days else None,
    }


def portfolio_mc(
    portfolio: list[AccountPlan],
    levered: dict[tuple[str, float], tuple[pd.Series, pd.Series]],
    n_iter: int,
    horizon_days: int,
    seed: int,
) -> dict:
    rng = random.Random(seed)
    all_indices = sorted(
        set.intersection(
            *[
                set(levered[(account.spec, account.vol_target)][0].index)
                for account in portfolio
            ],
            *[
                set(levered[(account.spec, account.funded_vol_target or account.vol_target)][0].index)
                for account in portfolio
            ],
        )
    )
    idx_count = len(all_indices)
    prepared = {}
    for account in portfolio:
        close, intraday = levered[(account.spec, account.vol_target)]
        funded_target = account.funded_vol_target or account.vol_target
        funded_close, funded_intraday = levered[(account.spec, funded_target)]
        close = close.reindex(all_indices)
        intraday = intraday.reindex(all_indices)
        funded_close = funded_close.reindex(all_indices)
        funded_intraday = funded_intraday.reindex(all_indices)
        prepared[account.name] = (
            close.to_list(),
            intraday.to_list(),
            funded_close.to_list(),
            funded_intraday.to_list(),
        )

    total_fees = sum(FIRM_PLANS[account.firm].fee for account in portfolio)
    outcomes = []
    for _ in range(n_iter):
        picks = [rng.randrange(idx_count) for _ in range(horizon_days)]
        total_payout = 0.0
        funded_count = 0
        dead_count = 0
        for account in portfolio:
            plan = FIRM_PLANS[account.firm]
            close_values, intraday_values, funded_close_values, funded_intraday_values = prepared[account.name]
            close_path = [close_values[idx] for idx in picks]
            intraday_path = [intraday_values[idx] for idx in picks]
            funded_close_path = [funded_close_values[idx] for idx in picks]
            funded_intraday_path = [funded_intraday_values[idx] for idx in picks]
            result = simulate_account_on_path(
                close_path,
                intraday_path,
                plan,
                plan.account_size,
                funded_close_path,
                funded_intraday_path,
            )
            total_payout += result["payouts"]
            funded_count += int(result["reached_funded"])
            dead_count += int(result["final_state"].startswith("DEAD"))
        outcomes.append({
            "gross": total_payout,
            "net": total_payout - total_fees,
            "funded_count": funded_count,
            "dead_count": dead_count,
        })

    gross = sorted(outcome["gross"] for outcome in outcomes)
    net = sorted(outcome["net"] for outcome in outcomes)
    return {
        "fees": total_fees,
        "gross_mean": sum(gross) / n_iter,
        "gross_median": gross[n_iter // 2],
        "gross_p05": gross[int(n_iter * 0.05)],
        "gross_p95": gross[int(n_iter * 0.95)],
        "net_mean": sum(net) / n_iter,
        "net_median": net[n_iter // 2],
        "net_p05": net[int(n_iter * 0.05)],
        "net_p95": net[int(n_iter * 0.95)],
        "p_net_positive": sum(value > 0 for value in net) / n_iter,
        "p_at_least_one_funded": sum(outcome["funded_count"] > 0 for outcome in outcomes) / n_iter,
        "avg_funded": sum(outcome["funded_count"] for outcome in outcomes) / n_iter,
        "avg_dead": sum(outcome["dead_count"] for outcome in outcomes) / n_iter,
    }


def main() -> None:
    specs = build_strategy_universe()
    print("\nStrategy OOS metrics, unlevered:")
    spec_metrics = {}
    for name, values in specs.items():
        daily = values["daily"]
        intraday = values["intraday"]
        oos = daily[daily.index >= OOS_START]
        oos_intraday = intraday.reindex(oos.index)
        m = metrics(oos)
        m["worst_intraday"] = float(oos_intraday.min())
        spec_metrics[name] = m
        print(
            f"  {name:<14} Sh={m['sharpe']:+.2f} ret={m['ann_ret']*100:+.1f}% "
            f"vol={m['ann_vol']*100:.1f}% DD={m['max_dd']*100:+.1f}% "
            f"wr={m['wr']*100:.1f}% worst_intra={m['worst_intraday']*100:+.2f}%"
        )

    vol_targets = [0.08, 0.10, 0.12]
    levered = {}
    levered_metrics = {}
    for spec_name, values in specs.items():
        for vol_target in vol_targets:
            close_lev, intraday_lev = vol_target_returns(values["daily"], values["intraday"], vol_target)
            oos = close_lev[close_lev.index >= OOS_START]
            oos_intraday = intraday_lev.reindex(oos.index)
            levered[(spec_name, vol_target)] = (oos, oos_intraday)
            m = metrics(oos)
            m["worst_intraday"] = float(oos_intraday.min())
            levered_metrics[f"{spec_name}_{int(vol_target * 100)}"] = m

    print("\nSingle-account MC on LOW_TARGET_200K:")
    single_results = {}
    low_target = FIRM_PLANS["LOW_TARGET_200K"]
    for spec_name in specs:
        for vol_target in vol_targets:
            close, intraday = levered[(spec_name, vol_target)]
            result = single_account_mc(
                close,
                intraday,
                low_target,
                N_MC,
                HORIZON_DAYS,
                stable_seed(f"{spec_name}_{vol_target}"),
            )
            key = f"{spec_name}_{int(vol_target * 100)}"
            single_results[key] = result
            print(
                f"  {key:<18} funded={result['p_funded']*100:4.0f}% dead={result['p_dead']*100:4.0f}% "
                f"E[payout]=${result['mean_payout']:7.0f} E[net]=${result['mean_net']:7.0f} "
                f"med_days={result['median_days_to_funded']}"
            )

    print("\nPortfolio MC:")
    portfolio_results = {}
    for name, portfolio in CANDIDATE_PORTFOLIOS.items():
        result = portfolio_mc(portfolio, levered, N_MC, HORIZON_DAYS, stable_seed(name))
        portfolio_results[name] = result
        print(
            f"  {name:<30} fees=${result['fees']:5.0f} E[net]=${result['net_mean']:8.0f} "
            f"med=${result['net_median']:8.0f} p+={result['p_net_positive']*100:4.0f}% "
            f"p95=${result['net_p95']:8.0f} funded={result['avg_funded']:.1f}"
        )

    best_portfolio_name = max(portfolio_results, key=lambda name: portfolio_results[name]["net_mean"])
    corr_df = pd.DataFrame({name: levered[(name, 0.10)][0] for name in specs}).dropna().corr()

    lines = []

    def emit(line: str = "") -> None:
        lines.append(line)

    emit("# Prop Firm Cash-Max Optimizer")
    emit("")
    emit(f"OOS window: 2024-2025. MC horizon: {HORIZON_DAYS} trading days. MC paths: {N_MC}.")
    emit("M5 data is used to estimate intraday adverse excursion for daily-loss checks.")
    emit("Funded profits are withdrawn monthly, reducing the account profit buffer after each payout.")
    emit("")
    emit("## Unlevered Strategy Quality")
    emit("| spec | Sharpe | ann_ret% | ann_vol% | maxDD% | win% | worst intraday% |")
    emit("|---|---:|---:|---:|---:|---:|---:|")
    for name, m in sorted(spec_metrics.items(), key=lambda item: item[1]["sharpe"], reverse=True):
        emit(
            f"| {name} | {m['sharpe']:+.2f} | {m['ann_ret']*100:+.1f} | "
            f"{m['ann_vol']*100:.1f} | {m['max_dd']*100:+.1f} | "
            f"{m['wr']*100:.1f} | {m['worst_intraday']*100:+.2f} |"
        )
    emit("")
    emit("## 10% Vol-Target Correlation")
    emit("```")
    emit(corr_df.round(2).to_string())
    emit("```")
    emit("")
    emit("## Single 200k Low-Target Account")
    emit("Plan modeled: 5% Phase 1, 5% Phase 2, 5% daily loss, 10% static max loss, 80% profit share.")
    emit("| spec_tv | P(funded) | P(dead) | E[payout] | E[net after fee] | median days funded |")
    emit("|---|---:|---:|---:|---:|---:|")
    for key, result in sorted(single_results.items(), key=lambda item: item[1]["mean_net"], reverse=True):
        med_days = "" if result["median_days_to_funded"] is None else f"{result['median_days_to_funded']:.0f}"
        emit(
            f"| {key} | {result['p_funded']*100:.0f}% | {result['p_dead']*100:.0f}% | "
            f"${result['mean_payout']:.0f} | ${result['mean_net']:.0f} | {med_days} |"
        )
    emit("")
    emit("## Portfolio Candidates")
    emit("| portfolio | fees | E gross | E net | median net | P(net+) | P>=1 funded | avg funded | avg dead | p95 net |")
    emit("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name, result in sorted(portfolio_results.items(), key=lambda item: item[1]["net_mean"], reverse=True):
        emit(
            f"| {name} | ${result['fees']:.0f} | ${result['gross_mean']:.0f} | "
            f"${result['net_mean']:.0f} | ${result['net_median']:.0f} | "
            f"{result['p_net_positive']*100:.0f}% | {result['p_at_least_one_funded']*100:.0f}% | "
            f"{result['avg_funded']:.1f} | {result['avg_dead']:.1f} | ${result['net_p95']:.0f} |"
        )
    emit("")
    emit("## Recommended Cash-Max Allocation")
    emit(f"Best by expected net cash-out: **{best_portfolio_name}**.")
    emit("Treat LOW_TARGET plans as rule templates; use separate firms only when their current rules match.")
    for account in CANDIDATE_PORTFOLIOS[best_portfolio_name]:
        plan = FIRM_PLANS[account.firm]
        funded_target = account.funded_vol_target or account.vol_target
        vol_label = (
            f"eval_vol={account.vol_target*100:.0f}%, funded_vol={funded_target*100:.0f}%"
            if funded_target != account.vol_target
            else f"vol_target={account.vol_target*100:.0f}%"
        )
        emit(
            f"- {account.name}: {account.firm} ${plan.account_size:.0f}, "
            f"{account.spec}, {vol_label}"
        )
    emit("")
    emit("## Hard Constraints")
    emit("- Do not run the same spec on every account; common-date MC shows correlation matters.")
    emit("- Avoid crypto for CFD prop rules unless the firm has wider daily-loss bands; daily excursions are too large.")
    emit("- Prefer 5%/5% static-drawdown two-step plans over 10%/5% plans when available.")
    emit("- Avoid trailing drawdown 500k plans unless you size lower; expected gross can be high but path risk is worse.")
    emit("- Do not assume multiple accounts are allowed inside one firm; verify max allocation and copy-trading rules first.")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))
    OUT_METRICS.write_text(json.dumps({
        "spec_metrics": spec_metrics,
        "levered_metrics": levered_metrics,
        "single_low_target_results": single_results,
        "portfolio_results": portfolio_results,
        "recommended": best_portfolio_name,
        "recommended_accounts": [account.__dict__ for account in CANDIDATE_PORTFOLIOS[best_portfolio_name]],
        "firm_plans": {name: plan.__dict__ for name, plan in FIRM_PLANS.items()},
        "correlation_10pct": corr_df.round(4).to_dict(),
    }, indent=2, default=str))

    print(f"\nWrote {OUT_REPORT}")
    print(f"Wrote {OUT_METRICS}")
    print(f"Best portfolio: {best_portfolio_name}")


if __name__ == "__main__":
    main()
