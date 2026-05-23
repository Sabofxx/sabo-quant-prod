"""
Sandbox — Accuracy boost layer for FX MR strategies.

Current FX_MR_STACK & EURUSD_MR5 have OOS win rate ~43%, Sharpe ~1.5/1.2.
Goal: raise win rate to 55-65% while preserving (or improving) Sharpe.

Methods tested:
  V1 STRICT_CONSENSUS  : trade only when ALL 3 lookbacks (MR3, MR5, MR10) agree.
                         Many fewer trades, but each trade is high-confidence.
  V2 SOFT_CONSENSUS    : trade when 2/3 lookbacks agree.
  V3 WEIGHTED_ENSEMBLE : position = mean of 3 lookback signals (continuous, -1..+1).
  V4 VOL_GATE          : trade only when realized 20d vol < 1.5× rolling 252d median vol
                         (chop/low-vol regime favors MR).
  V5 STRICT + VOL_GATE : combine V1 + V4.
  V6 MULTI_PAIR_CONFIRM: for each pair, trade only if signal aligns with USD-basket
                         direction (cross-confirm).

Each tested on:
  A. EURUSD solo (highest-Sharpe single pair from prior work)
  B. FX 6-pair stack equal-weight

Metrics tracked:
  - OOS Sharpe (vs baseline +1.16 EURUSD / +1.51 stack)
  - Win rate (vs baseline ~43%)
  - Trade count (consensus reduces N)
  - Avg win / avg loss (R-multiple-equivalent)
  - Bootstrap CI on Sharpe + on win rate
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


HERE = Path(__file__).parent
DATA = HERE / "data"
OUT_REPORT = HERE / "strategy_accuracy_boost_report.md"
OUT_METRICS = HERE / "strategy_accuracy_boost_metrics.json"
OUT_EQUITY = HERE / "strategy_accuracy_boost_equity.html"

FX_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD"]
USD_DIR = {"EURUSD": -1, "GBPUSD": -1, "AUDUSD": -1, "NZDUSD": -1,
           "USDJPY": +1, "USDCAD": +1}
PIP_SIZE = {"EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001,
            "NZDUSD": 0.0001, "USDCAD": 0.0001, "USDJPY": 0.01}
ROUND_TRIP_PIPS = {"EURUSD": 1.9, "GBPUSD": 2.3, "USDJPY": 2.1,
                   "AUDUSD": 2.3, "NZDUSD": 3.1, "USDCAD": 2.7}
FILES_M5 = {p: f"{p.lower()}-m5-bid-2019-01-01-2026-01-01.csv" for p in FX_PAIRS}

TRADING_DAYS = 252
SEED = 42
IS_END = pd.Timestamp("2023-12-31 23:59:59", tz="UTC")
OOS_START = pd.Timestamp("2024-01-01", tz="UTC")
N_BOOT = 1000
LOOKBACKS_CONSENSUS = [3, 5, 10]


# =====================================================================
# IO
# =====================================================================
def load_close(p: str) -> pd.Series:
    df = pd.read_csv(DATA / FILES_M5[p])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    return df["close"].resample("1D").last().dropna()


# =====================================================================
# Signal generators
# =====================================================================
def mr_sig(rets: pd.Series, lb: int) -> pd.Series:
    """+1 if past lb cum < 0 (mean rev long), -1 if > 0."""
    cum = (1.0 + rets).rolling(lb).apply(lambda x: x.prod() - 1.0, raw=True)
    s = (cum < 0).astype(float) - (cum > 0).astype(float)
    return s.shift(1).dropna()


def baseline_sig(rets: pd.Series, lb: int) -> pd.Series:
    return mr_sig(rets, lb)


def strict_consensus_sig(rets: pd.Series, lookbacks: list[int]) -> pd.Series:
    """All N lookbacks must agree on direction. Else 0 (no trade)."""
    sigs = [mr_sig(rets, lb) for lb in lookbacks]
    common = sigs[0].index
    for s in sigs[1:]:
        common = common.intersection(s.index)
    aligned = pd.DataFrame({i: s.loc[common] for i, s in enumerate(sigs)})
    # All same sign and non-zero
    out = pd.Series(0.0, index=common)
    all_pos = (aligned > 0).all(axis=1)
    all_neg = (aligned < 0).all(axis=1)
    out[all_pos] = 1.0
    out[all_neg] = -1.0
    return out


def soft_consensus_sig(rets: pd.Series, lookbacks: list[int], k: int = 2) -> pd.Series:
    """At least k of N lookbacks must agree on direction."""
    sigs = [mr_sig(rets, lb) for lb in lookbacks]
    common = sigs[0].index
    for s in sigs[1:]:
        common = common.intersection(s.index)
    aligned = pd.DataFrame({i: s.loc[common] for i, s in enumerate(sigs)})
    pos_count = (aligned > 0).sum(axis=1)
    neg_count = (aligned < 0).sum(axis=1)
    out = pd.Series(0.0, index=common)
    out[pos_count >= k] = 1.0
    out[neg_count >= k] = -1.0
    # If both >= k (impossible if k > N/2) take direction with more votes
    return out


def weighted_ensemble_sig(rets: pd.Series, lookbacks: list[int]) -> pd.Series:
    """Position = mean of lookback signals. Continuous [-1, +1]."""
    sigs = [mr_sig(rets, lb) for lb in lookbacks]
    common = sigs[0].index
    for s in sigs[1:]:
        common = common.intersection(s.index)
    aligned = pd.DataFrame({i: s.loc[common] for i, s in enumerate(sigs)})
    return aligned.mean(axis=1)


def vol_gate(rets: pd.Series, sig: pd.Series, short_lb: int = 20, long_lb: int = 252,
             ratio_threshold: float = 1.5) -> pd.Series:
    """Mute signal when short_lb realized vol > ratio_threshold × long_lb realized vol.
    MR favors low-vol regimes (chop), so we trade only when vol regime is calm."""
    short_vol = rets.rolling(short_lb).std()
    long_vol = rets.rolling(long_lb).std()
    ratio = short_vol / long_vol
    # When ratio > threshold = high vol → mute (signal = 0)
    common = sig.index.intersection(ratio.index)
    out = sig.loc[common].copy()
    high_vol_mask = ratio.loc[common] > ratio_threshold
    out[high_vol_mask] = 0.0
    return out


def usd_basket(rets_df: pd.DataFrame) -> pd.Series:
    parts = pd.DataFrame({p: USD_DIR[p] * rets_df[p] for p in FX_PAIRS})
    return parts.mean(axis=1)


def multi_pair_confirm_sig(pair: str, rets_df: pd.DataFrame, lb: int) -> pd.Series:
    """For each pair, MR signal. But require USD basket signal in same direction
    (cross-confirm via basket sign over same lookback)."""
    pair_sig = mr_sig(rets_df[pair], lb)
    basket_rets = usd_basket(rets_df)
    # Basket signal direction: if EURUSD long (basket short USD), then basket return should be negative
    # for confirmation. So if pair_sig=+1 and USD_DIR[pair]=-1, expected basket dir = -1
    # Simplified: align pair_sig with basket MR sig sign for the pair's effective USD exposure
    basket_cum = (1.0 + basket_rets).rolling(lb).apply(lambda x: x.prod() - 1.0, raw=True)
    basket_dir = (basket_cum < 0).astype(float) - (basket_cum > 0).astype(float)
    basket_dir = basket_dir.shift(1)
    common = pair_sig.index.intersection(basket_dir.index)
    out = pair_sig.loc[common].copy()
    # If pair USD direction is -1 (long pair = short USD), confirm if basket signal also short USD
    # Basket signal: +1 means long USD, -1 means short USD
    # Pair signal +1 (long pair): if USD_DIR=-1 (e.g., EURUSD), this is short USD ; want basket = -1
    # Effective USD signal from pair: pair_sig × USD_DIR
    pair_usd = pair_sig.loc[common] * USD_DIR[pair]
    # Mute if pair_usd != basket_dir (disagreement)
    disagree = (pair_usd != basket_dir.loc[common]) & (pair_sig.loc[common] != 0)
    out[disagree] = 0.0
    return out


# =====================================================================
# Net P&L with costs
# =====================================================================
def fx_net_pl(pair: str, rets: pd.Series, price: pd.Series, sig: pd.Series) -> pd.Series:
    common = sig.index.intersection(rets.index)
    g = sig.loc[common] * rets.loc[common]
    turnover = sig.diff().abs().fillna(0.0)  # full diff (sig can be -1, 0, +1, or fractional)
    cost_unit = (ROUND_TRIP_PIPS[pair] * PIP_SIZE[pair] / price).reindex(common).fillna(0.0)
    cost = (turnover / 2.0) * cost_unit  # each unit of turnover = half round-trip
    return g - cost


# =====================================================================
# Metrics
# =====================================================================
def metrics(pl: pd.Series) -> dict:
    pl = pl.dropna()
    pl_traded = pl[pl != 0]
    if len(pl) < 2:
        return {"n_days": 0, "n_traded": 0, "trade_rate": 0,
                "sharpe": 0.0, "ann_ret": 0.0, "ann_vol": 0.0,
                "max_dd": 0.0, "wr": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "calmar": 0.0}
    m = float(pl.mean())
    s = float(pl.std())
    sh = (m / s) * math.sqrt(TRADING_DAYS) if s > 0 else 0.0
    cum = pl.cumsum()
    dd = float((cum - cum.cummax()).min())
    wins = pl_traded[pl_traded > 0]
    losses = pl_traded[pl_traded < 0]
    return {
        "n_days": len(pl),
        "n_traded": len(pl_traded),
        "trade_rate": len(pl_traded) / len(pl) * 100,
        "sharpe": sh,
        "ann_ret": m * TRADING_DAYS,
        "ann_vol": s * math.sqrt(TRADING_DAYS),
        "max_dd": dd,
        "wr": float((wins.shape[0] / max(len(pl_traded), 1)) * 100),
        "avg_win": float(wins.mean()) if len(wins) > 0 else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) > 0 else 0.0,
        "calmar": (m * TRADING_DAYS) / abs(dd) if dd < 0 else float("inf"),
        "expectancy_per_trade": (m * len(pl) / len(pl_traded)) if len(pl_traded) > 0 else 0,
    }


def bootstrap_sharpe_wr(pl: pd.Series, n: int, seed: int) -> dict:
    rng = random.Random(seed)
    arr = pl.dropna().to_list()
    L = len(arr)
    if L < 2:
        return {"sharpe": (0, 0, 0), "wr": (0, 0, 0)}
    shs, wrs = [], []
    for _ in range(n):
        s = [arr[rng.randrange(L)] for _ in range(L)]
        m = sum(s) / L
        var = sum((x - m) ** 2 for x in s) / (L - 1)
        std = math.sqrt(var)
        shs.append((m / std) * math.sqrt(TRADING_DAYS) if std > 0 else 0.0)
        traded = [x for x in s if x != 0]
        if traded:
            wrs.append(sum(1 for x in traded if x > 0) / len(traded) * 100)
    shs.sort()
    wrs.sort()
    if not wrs:
        return {"sharpe": (shs[int(n*0.025)], shs[n//2], shs[int(n*0.975)-1]), "wr": (0, 0, 0)}
    return {
        "sharpe": (shs[int(n*0.025)], shs[n//2], shs[int(n*0.975)-1]),
        "wr": (wrs[int(len(wrs)*0.025)], wrs[len(wrs)//2], wrs[int(len(wrs)*0.975)-1]),
    }


# =====================================================================
# Main
# =====================================================================
def main() -> None:
    print("Loading 6 FX pairs M5 → daily close...")
    closes = {p: load_close(p) for p in FX_PAIRS}
    rets = pd.DataFrame({p: closes[p].pct_change() for p in FX_PAIRS}).dropna()
    prices = pd.DataFrame({p: closes[p].reindex(rets.index) for p in FX_PAIRS})

    # ----- EURUSD solo : test all accuracy boosters -----
    print("\n=== EURUSD solo — accuracy boost tests ===")
    eu_rets = rets["EURUSD"]
    eu_px = prices["EURUSD"]

    spec_eu: dict = {}
    spec_eu["baseline_MR5"] = fx_net_pl("EURUSD", eu_rets, eu_px, baseline_sig(eu_rets, 5))
    spec_eu["V1_strict_consensus"] = fx_net_pl("EURUSD", eu_rets, eu_px,
                                                 strict_consensus_sig(eu_rets, LOOKBACKS_CONSENSUS))
    spec_eu["V2_soft_consensus_2of3"] = fx_net_pl("EURUSD", eu_rets, eu_px,
                                                    soft_consensus_sig(eu_rets, LOOKBACKS_CONSENSUS, 2))
    spec_eu["V3_weighted_ensemble"] = fx_net_pl("EURUSD", eu_rets, eu_px,
                                                  weighted_ensemble_sig(eu_rets, LOOKBACKS_CONSENSUS))
    spec_eu["V4_vol_gate"] = fx_net_pl("EURUSD", eu_rets, eu_px,
                                         vol_gate(eu_rets, baseline_sig(eu_rets, 5)))
    spec_eu["V5_strict_plus_vol_gate"] = fx_net_pl("EURUSD", eu_rets, eu_px,
                                                     vol_gate(eu_rets, strict_consensus_sig(eu_rets, LOOKBACKS_CONSENSUS)))
    spec_eu["V6_multi_pair_confirm"] = fx_net_pl("EURUSD", eu_rets, eu_px,
                                                   multi_pair_confirm_sig("EURUSD", rets, 5))

    print("\n| spec | n_traded | trade% | wr% | Sharpe | ann_ret% | maxDD% | exp/trade |")
    eu_results: dict = {}
    for name, pl in spec_eu.items():
        pl_oos = pl[pl.index >= OOS_START]
        m = metrics(pl_oos)
        ci = bootstrap_sharpe_wr(pl_oos, N_BOOT, SEED)
        eu_results[name] = {"metrics": m, "ci": ci, "pl": pl}
        print(f"| {name:<28} | {m['n_traded']:5d} | {m['trade_rate']:5.1f} | "
              f"{m['wr']:5.1f} | {m['sharpe']:+.2f} | {m['ann_ret']*100:+.1f} | "
              f"{m['max_dd']*100:+.1f} | {m['expectancy_per_trade']*1e4:+.1f}bps |")

    # ----- FX stack : same tests across all pairs combined -----
    print("\n=== FX stack (6 pairs) — accuracy boost tests ===")
    # Stack with each accuracy method applied per pair, equal-weight combined
    BEST_LB = {"EURUSD": 5, "GBPUSD": 3, "USDJPY": 10, "AUDUSD": 21, "NZDUSD": 10, "USDCAD": 3}

    def build_stack(sig_fn) -> pd.Series:
        per_pair = {p: fx_net_pl(p, rets[p], prices[p], sig_fn(p)) for p in FX_PAIRS}
        return pd.DataFrame(per_pair).fillna(0.0).sum(axis=1) / len(FX_PAIRS)

    spec_stack: dict = {}
    spec_stack["baseline_best_per_pair"] = build_stack(
        lambda p: mr_sig(rets[p], BEST_LB[p]))
    spec_stack["V1_strict_consensus_per_pair"] = build_stack(
        lambda p: strict_consensus_sig(rets[p], LOOKBACKS_CONSENSUS))
    spec_stack["V2_soft_consensus_2of3"] = build_stack(
        lambda p: soft_consensus_sig(rets[p], LOOKBACKS_CONSENSUS, 2))
    spec_stack["V3_weighted_ensemble"] = build_stack(
        lambda p: weighted_ensemble_sig(rets[p], LOOKBACKS_CONSENSUS))
    spec_stack["V4_vol_gate_on_best"] = build_stack(
        lambda p: vol_gate(rets[p], mr_sig(rets[p], BEST_LB[p])))
    spec_stack["V5_strict_plus_vol_gate"] = build_stack(
        lambda p: vol_gate(rets[p], strict_consensus_sig(rets[p], LOOKBACKS_CONSENSUS)))
    spec_stack["V6_multi_pair_confirm"] = build_stack(
        lambda p: multi_pair_confirm_sig(p, rets, BEST_LB[p]))

    print("\n| spec | n_traded | trade% | wr% | Sharpe | ann_ret% | maxDD% | exp/trade |")
    stack_results: dict = {}
    for name, pl in spec_stack.items():
        pl_oos = pl[pl.index >= OOS_START]
        m = metrics(pl_oos)
        ci = bootstrap_sharpe_wr(pl_oos, N_BOOT, SEED)
        stack_results[name] = {"metrics": m, "ci": ci, "pl": pl}
        print(f"| {name:<32} | {m['n_traded']:5d} | {m['trade_rate']:5.1f} | "
              f"{m['wr']:5.1f} | {m['sharpe']:+.2f} | {m['ann_ret']*100:+.1f} | "
              f"{m['max_dd']*100:+.1f} | {m['expectancy_per_trade']*1e4:+.1f}bps |")

    # Pick winners
    eu_best = max(eu_results, key=lambda n: eu_results[n]["metrics"]["wr"]
                   if eu_results[n]["metrics"]["sharpe"] > 0.5 else -1)
    stack_best = max(stack_results, key=lambda n: stack_results[n]["metrics"]["wr"]
                      if stack_results[n]["metrics"]["sharpe"] > 0.5 else -1)
    print(f"\n→ EURUSD best (wr-max with Sh>0.5): **{eu_best}**")
    print(f"→ Stack  best (wr-max with Sh>0.5): **{stack_best}**")

    # ----- Report -----
    lines: list[str] = []
    def emit(s: str) -> None:
        print(s)
        lines.append(s)

    emit("# Accuracy Boost — push win rate above 50%")
    emit("")
    emit("Baseline FX_MR_STACK win rate ~43%. Strategies tested:")
    emit("- V1 STRICT_CONSENSUS : all 3 lookbacks {MR3, MR5, MR10} must agree → reduce N, raise wr")
    emit("- V2 SOFT_CONSENSUS 2/3 : 2 of 3 lookbacks agree")
    emit("- V3 WEIGHTED_ENSEMBLE : continuous position = mean of 3 signals")
    emit("- V4 VOL_GATE : trade only when short-vol < 1.5× long-vol (low-vol = chop = MR-friendly)")
    emit("- V5 STRICT + VOL_GATE : compound V1 + V4")
    emit("- V6 MULTI_PAIR_CONFIRM : require USD-basket signal in same direction")
    emit("")

    emit("## EURUSD SOLO — accuracy frontier (OOS 2024-2025)")
    emit("| spec | n_traded | trade% | wr% | Sharpe | ann_ret% | maxDD% | exp/trade(bps) | CI95 wr% |")
    emit("|---|---|---|---|---|---|---|---|---|")
    for name, r in eu_results.items():
        m = r["metrics"]
        ci = r["ci"]
        emit(f"| {name} | {m['n_traded']} | {m['trade_rate']:.1f} | "
             f"{m['wr']:.1f} | {m['sharpe']:+.2f} | {m['ann_ret']*100:+.1f} | "
             f"{m['max_dd']*100:+.1f} | {m['expectancy_per_trade']*1e4:+.1f} | "
             f"[{ci['wr'][0]:.1f}, {ci['wr'][2]:.1f}] |")
    emit(f"\n  → BEST (wr max with Sharpe > 0.5): **{eu_best}**")
    eu_best_m = eu_results[eu_best]["metrics"]
    eu_best_ci = eu_results[eu_best]["ci"]
    emit(f"    n_traded={eu_best_m['n_traded']}  trade_rate={eu_best_m['trade_rate']:.1f}%  "
         f"wr={eu_best_m['wr']:.1f}% (CI95 [{eu_best_ci['wr'][0]:.1f}, {eu_best_ci['wr'][2]:.1f}])")
    emit(f"    Sharpe={eu_best_m['sharpe']:+.2f}  ann_ret={eu_best_m['ann_ret']*100:+.1f}%  "
         f"maxDD={eu_best_m['max_dd']*100:+.1f}%")
    emit("")

    emit("## FX 6-PAIR STACK — accuracy frontier (OOS 2024-2025)")
    emit("| spec | n_traded | trade% | wr% | Sharpe | ann_ret% | maxDD% | exp/trade(bps) | CI95 wr% |")
    emit("|---|---|---|---|---|---|---|---|---|")
    for name, r in stack_results.items():
        m = r["metrics"]
        ci = r["ci"]
        emit(f"| {name} | {m['n_traded']} | {m['trade_rate']:.1f} | "
             f"{m['wr']:.1f} | {m['sharpe']:+.2f} | {m['ann_ret']*100:+.1f} | "
             f"{m['max_dd']*100:+.1f} | {m['expectancy_per_trade']*1e4:+.1f} | "
             f"[{ci['wr'][0]:.1f}, {ci['wr'][2]:.1f}] |")
    emit(f"\n  → BEST (wr max with Sharpe > 0.5): **{stack_best}**")
    stk_best_m = stack_results[stack_best]["metrics"]
    stk_best_ci = stack_results[stack_best]["ci"]
    emit(f"    n_traded={stk_best_m['n_traded']}  trade_rate={stk_best_m['trade_rate']:.1f}%  "
         f"wr={stk_best_m['wr']:.1f}% (CI95 [{stk_best_ci['wr'][0]:.1f}, {stk_best_ci['wr'][2]:.1f}])")
    emit(f"    Sharpe={stk_best_m['sharpe']:+.2f}  ann_ret={stk_best_m['ann_ret']*100:+.1f}%  "
         f"maxDD={stk_best_m['max_dd']*100:+.1f}%")
    emit("")

    emit("## TRADE-OFF SUMMARY")
    base_eu = eu_results["baseline_MR5"]["metrics"]
    boost_eu = eu_results[eu_best]["metrics"]
    base_stk = stack_results["baseline_best_per_pair"]["metrics"]
    boost_stk = stack_results[stack_best]["metrics"]
    emit("### EURUSD baseline vs best")
    emit(f"  baseline_MR5     : wr={base_eu['wr']:.1f}%  Sh={base_eu['sharpe']:+.2f}  "
         f"n_trades={base_eu['n_traded']}  trade%={base_eu['trade_rate']:.0f}")
    emit(f"  {eu_best:<14} : wr={boost_eu['wr']:.1f}%  Sh={boost_eu['sharpe']:+.2f}  "
         f"n_trades={boost_eu['n_traded']}  trade%={boost_eu['trade_rate']:.0f}")
    emit(f"  Δ wr: {boost_eu['wr'] - base_eu['wr']:+.1f} percentage points")
    emit(f"  Δ Sh: {boost_eu['sharpe'] - base_eu['sharpe']:+.2f}")
    emit(f"  Trade reduction: {(1 - boost_eu['n_traded']/base_eu['n_traded'])*100:.0f}%")
    emit("")
    emit("### FX Stack baseline vs best")
    emit(f"  baseline_best_per_pair        : wr={base_stk['wr']:.1f}%  Sh={base_stk['sharpe']:+.2f}  "
         f"n={base_stk['n_traded']}  trade%={base_stk['trade_rate']:.0f}")
    emit(f"  {stack_best:<30} : wr={boost_stk['wr']:.1f}%  Sh={boost_stk['sharpe']:+.2f}  "
         f"n={boost_stk['n_traded']}  trade%={boost_stk['trade_rate']:.0f}")
    emit(f"  Δ wr: {boost_stk['wr'] - base_stk['wr']:+.1f} pp")
    emit(f"  Δ Sh: {boost_stk['sharpe'] - base_stk['sharpe']:+.2f}")
    emit("")

    emit("## RECOMMENDATION")
    emit("Pick based on goal:")
    emit("  - Maximize **Sharpe** (best risk-adjusted return) → use FX_MR_STACK best_per_pair (baseline)")
    emit(f"  - Maximize **win rate** (psychological comfort, prop firm consistency) → "
         f"use **{stack_best}** on stack OR **{eu_best}** on EURUSD")
    emit("")
    emit("Trade-off: high wr methods reduce N trades and may reduce raw Sharpe.")
    emit("For prop firm eval: high wr = fewer DD breaches, more consistent equity curve.")
    emit("For maximum compounding: high Sharpe wins long-term.")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))

    # Persist metrics
    out_json = {
        "eurusd_methods": {
            n: {"metrics": r["metrics"], "ci_sharpe": list(r["ci"]["sharpe"]),
                "ci_wr": list(r["ci"]["wr"])}
            for n, r in eu_results.items()
        },
        "stack_methods": {
            n: {"metrics": r["metrics"], "ci_sharpe": list(r["ci"]["sharpe"]),
                "ci_wr": list(r["ci"]["wr"])}
            for n, r in stack_results.items()
        },
        "eurusd_best": eu_best,
        "stack_best": stack_best,
    }
    OUT_METRICS.write_text(json.dumps(out_json, indent=2, default=str))

    # Equity HTML
    fig = make_subplots(rows=2, cols=1, subplot_titles=(
        "EURUSD methods — cum return OOS",
        "FX Stack methods — cum return OOS"),
        shared_xaxes=False, vertical_spacing=0.12)
    colors = ["#888", "#1976d2", "#0288d1", "#2e7d32", "#f57c00", "#c62828", "#7b1fa2"]
    for i, (name, r) in enumerate(eu_results.items()):
        cum = r["pl"][r["pl"].index >= OOS_START].cumsum() * 100
        fig.add_trace(go.Scatter(x=cum.index, y=cum.values, mode="lines",
                                 name=name, line=dict(color=colors[i % len(colors)])),
                      row=1, col=1)
    for i, (name, r) in enumerate(stack_results.items()):
        cum = r["pl"][r["pl"].index >= OOS_START].cumsum() * 100
        fig.add_trace(go.Scatter(x=cum.index, y=cum.values, mode="lines",
                                 name=name, line=dict(color=colors[i % len(colors)])),
                      row=2, col=1)
    fig.update_layout(title="Accuracy boost methods — OOS comparison",
                      template="plotly_white", height=900, hovermode="x")
    fig.update_yaxes(title_text="cum return %", row=1, col=1)
    fig.update_yaxes(title_text="cum return %", row=2, col=1)
    fig.write_html(str(OUT_EQUITY), include_plotlyjs="cdn")

    # Deliverable
    print("\n\ndone")
    print(f"files: {OUT_REPORT.name}, {OUT_METRICS.name}, {OUT_EQUITY.name}")
    print(f"EURUSD best wr: {eu_best} wr={eu_best_m['wr']:.1f}% Sh={eu_best_m['sharpe']:+.2f}")
    print(f"Stack  best wr: {stack_best} wr={stk_best_m['wr']:.1f}% Sh={stk_best_m['sharpe']:+.2f}")


if __name__ == "__main__":
    main()
