"""
Sandbox — 16-year adaptive sizing validation on H1 Dukascopy data 2010-2025.

CRITICAL question : does adaptive sizing's edge survive UNSEEN 2010-2018 data?
Adaptive layer was designed/tested on 2019-2025. If it still beats static on
2010-2018, that's strong evidence the regime-detection mechanism is real.

Data sources:
- sandbox/data/dukascopy_research_full_h1_2010-01-01_2012-01-01/  (6 FX pairs)
- sandbox/data/dukascopy_research_full_h1_2012-01-01_2026-01-01/  (22 instruments)

Method:
1. Combine 2010-2012 + 2012-2026 H1 bid/ask for 6 FX pairs
2. Resample to daily mid close
3. Apply same FX_MR_STACK / NO_EUR_STACK / COMDOLL_STACK specs
4. Compare STATIC vs ADAPTIVE_50_0 over multiple windows:
   - Full 16 years (2010-2025)
   - Pre-2019 (8 years adaptive-unseen)
   - Post-2019 (already known)

If adaptive beats static on 2010-2018 too → REAL regime detection
If adaptive ties or loses → may be 2020-COVID-skip artifact
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd


HERE = Path(__file__).parent
DATA_2010 = HERE / "data" / "dukascopy_research_full_h1_2010-01-01_2012-01-01"
DATA_2012 = HERE / "data" / "dukascopy_research_full_h1_2012-01-01_2026-01-01"
OUT_REPORT = HERE / "adaptive_2010_h1_validation_report.md"
OUT_METRICS = HERE / "adaptive_2010_h1_validation_metrics.json"

ANN_DAYS = 252
VOL_TARGET = 0.10
MAX_LEVERAGE = 10.0
ROLLING_WINDOW = 126
# Test adaptive variants. Trigger source:
# - static: rolling Sharpe computed from static-vol-targeted returns
# - unlevered: rolling Sharpe computed from raw unlevered strategy returns
ADAPTIVE_VARIANTS = {
    "ADAPTIVE_50_0": {
        "tiers": [(0.3, 1.0), (0.0, 0.5), (-1e9, 0.0)],
        "trigger": "static",
    },
    "ADAPTIVE_50_25": {
        "tiers": [(0.3, 1.0), (0.0, 0.5), (-1e9, 0.25)],
        "trigger": "static",
    },
    "ADAPTIVE_75_50": {
        "tiers": [(0.3, 1.0), (0.0, 0.75), (-1e9, 0.5)],
        "trigger": "static",
    },
    "ADAPTIVE_75_50_UNLEV": {
        "tiers": [(0.3, 1.0), (0.0, 0.75), (-1e9, 0.5)],
        "trigger": "unlevered",
    },
}
ADAPTIVE_TIERS = ADAPTIVE_VARIANTS["ADAPTIVE_75_50"]["tiers"]  # report default

FX_PAIRS = ["eurusd", "gbpusd", "usdjpy", "audusd", "nzdusd", "usdcad"]
PIP_SIZE = {"eurusd": 0.0001, "gbpusd": 0.0001, "audusd": 0.0001,
            "nzdusd": 0.0001, "usdcad": 0.0001, "usdjpy": 0.01}
RT_PIPS = {"eurusd": 1.9, "gbpusd": 2.3, "usdjpy": 2.1,
           "audusd": 2.3, "nzdusd": 3.1, "usdcad": 2.7}
BEST_LB = {"eurusd": 5, "gbpusd": 3, "usdjpy": 10, "audusd": 21,
           "nzdusd": 10, "usdcad": 3}

SPECS_TO_TEST = {
    "FX_MR_STACK": list(FX_PAIRS),
    "NO_EUR_STACK": ["gbpusd", "usdjpy", "audusd", "nzdusd", "usdcad"],
    "COMDOLL_STACK": ["audusd", "nzdusd", "usdcad"],
}


def load_h1_side(data_root: Path, symbol: str, side: str) -> pd.DataFrame:
    symbol_dir = data_root / symbol / side
    if not symbol_dir.exists():
        return pd.DataFrame()
    frames = []
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


def load_symbol_full(symbol: str) -> pd.DataFrame:
    """Load 2010-2012 + 2012-2026, combine, dedup."""
    bid_old = load_h1_side(DATA_2010, symbol, "bid")
    bid_new = load_h1_side(DATA_2012, symbol, "bid")
    ask_old = load_h1_side(DATA_2010, symbol, "ask")
    ask_new = load_h1_side(DATA_2012, symbol, "ask")

    bid = pd.concat([bid_old, bid_new]).sort_index()
    bid = bid[~bid.index.duplicated(keep="last")]
    ask = pd.concat([ask_old, ask_new]).sort_index()
    ask = ask[~ask.index.duplicated(keep="last")]

    idx = bid.index.intersection(ask.index)
    bid = bid.loc[idx]
    ask = ask.loc[idx]
    mid_close = (bid["close"] + ask["close"]) / 2.0
    return pd.DataFrame({"bid_close": bid["close"], "ask_close": ask["close"],
                          "mid_close": mid_close}).dropna()


def to_daily_close(h1_df: pd.DataFrame) -> pd.Series:
    return h1_df["mid_close"].resample("1D").last().dropna()


def mr_signal(rets: pd.Series, lookback: int) -> pd.Series:
    cum = (1.0 + rets).rolling(lookback).apply(lambda x: x.prod() - 1.0, raw=True)
    s = (cum < 0).astype(float) - (cum > 0).astype(float)
    return s.shift(1).dropna()


def pair_net_daily(symbol: str, daily_close: pd.Series, lookback: int) -> pd.Series:
    rets = daily_close.pct_change()
    sig = mr_signal(rets, lookback)
    common = sig.index.intersection(rets.index)
    g = sig.loc[common] * rets.loc[common]
    turnover = sig.diff().abs().fillna(0.0) / 2.0
    cost = (turnover * RT_PIPS[symbol] * PIP_SIZE[symbol] / daily_close).reindex(common).fillna(0.0)
    return g - cost


def build_spec_daily(spec_members: list[str], daily_closes: dict) -> pd.Series:
    streams = {p: pair_net_daily(p, daily_closes[p], BEST_LB[p]) for p in spec_members}
    df = pd.DataFrame(streams).fillna(0.0)
    return df.sum(axis=1) / len(spec_members)


def static_levered(daily: pd.Series, target_vol: float) -> pd.Series:
    realized = daily.rolling(60).std() * math.sqrt(ANN_DAYS)
    lev = (target_vol / realized).clip(upper=MAX_LEVERAGE).shift(1).fillna(1.0)
    return (daily * lev).dropna()


def adaptive_levered(daily: pd.Series, target_vol: float, window: int = 126,
                      tiers: list | None = None, trigger_source: str = "static"
                      ) -> tuple[pd.Series, pd.Series]:
    realized = daily.rolling(60).std() * math.sqrt(ANN_DAYS)
    static_lev = (target_vol / realized).clip(upper=MAX_LEVERAGE).shift(1).fillna(1.0)
    static_returns = (daily * static_lev).dropna()

    if trigger_source == "unlevered":
        trigger_returns = daily.dropna()
    elif trigger_source == "static":
        trigger_returns = static_returns
    else:
        raise ValueError(f"unknown trigger_source: {trigger_source}")

    rolling_mean = trigger_returns.rolling(window).mean()
    rolling_std = trigger_returns.rolling(window).std()
    rolling_sharpe = ((rolling_mean / rolling_std) * math.sqrt(ANN_DAYS)).shift(1)

    use_tiers = tiers if tiers is not None else ADAPTIVE_TIERS

    def to_mult(sh):
        if pd.isna(sh):
            return 1.0
        for thr, mult in use_tiers:
            if sh >= thr:
                return mult
        return use_tiers[-1][1]

    multiplier = rolling_sharpe.map(to_mult).fillna(1.0)
    eff_lev = (target_vol * multiplier / realized).clip(upper=MAX_LEVERAGE).shift(1).fillna(1.0)
    return (daily * eff_lev).dropna(), multiplier


def metrics(pl: pd.Series) -> dict:
    pl = pl.dropna()
    if len(pl) < 30:
        return {"n": len(pl), "sharpe": 0.0, "ann_ret": 0.0, "max_dd": 0.0, "calmar": 0.0}
    mean = float(pl.mean())
    std = float(pl.std())
    sharpe = (mean / std) * math.sqrt(ANN_DAYS) if std > 0 else 0.0
    cum = pl.cumsum()
    dd = float((cum - cum.cummax()).min())
    return {"n": len(pl), "sharpe": sharpe, "ann_ret": mean * ANN_DAYS,
            "max_dd": dd, "calmar": (mean * ANN_DAYS) / abs(dd) if dd < 0 else float("inf")}


def main() -> None:
    print("Loading 6 FX pairs H1 2010-2026 (combined Dukascopy)...")
    daily_closes = {}
    for p in FX_PAIRS:
        h1 = load_symbol_full(p)
        if h1.empty:
            print(f"  {p}: NO DATA")
            continue
        daily = to_daily_close(h1)
        daily_closes[p] = daily
        print(f"  {p}: {len(daily)} daily obs, {daily.index[0].date()} → {daily.index[-1].date()}")

    if len(daily_closes) < 6:
        print("Missing some pairs, aborting")
        return

    # Build spec daily streams
    print("\nBuilding spec daily P&L streams...")
    spec_dailies = {}
    for spec_name, members in SPECS_TO_TEST.items():
        spec_dailies[spec_name] = build_spec_daily(members, daily_closes)
        print(f"  {spec_name}: {len(spec_dailies[spec_name])} obs")

    # Apply static + adaptive, partition by period
    periods = {
        "FULL_2010_2025": (pd.Timestamp("2010-01-01", tz="UTC"), pd.Timestamp("2025-12-31 23:59:59", tz="UTC")),
        "PRE_2019":       (pd.Timestamp("2010-01-01", tz="UTC"), pd.Timestamp("2018-12-31 23:59:59", tz="UTC")),
        "POST_2019":      (pd.Timestamp("2019-01-01", tz="UTC"), pd.Timestamp("2025-12-31 23:59:59", tz="UTC")),
    }

    results = {}
    print(f"\n{'spec':<14} | {'period':<14} | {'mode':<18} | "
          f"{'Sh':>6} | {'ann%':>6} | {'DD%':>6} | {'Calmar':>7} | low_mult%")
    for spec_name, daily in spec_dailies.items():
        static_pl = static_levered(daily, VOL_TARGET)
        results[spec_name] = {}
        modes = [("STATIC", static_pl, None)]
        for variant_name, variant in ADAPTIVE_VARIANTS.items():
            adapt_pl, mult = adaptive_levered(
                daily,
                VOL_TARGET,
                ROLLING_WINDOW,
                variant["tiers"],
                variant["trigger"],
            )
            modes.append((variant_name, adapt_pl, mult))
        for period_name, (start, end) in periods.items():
            results[spec_name][period_name] = {}
            for mode_name, pl, mult in modes:
                sub = pl[(pl.index >= start) & (pl.index <= end)]
                m = metrics(sub)
                low_mult_pct = 0.0
                if mult is not None:
                    mult_sub = mult[(mult.index >= start) & (mult.index <= end)]
                    # "low_mult" = bottom tier multiplier (worst regime)
                    bottom_mult = ADAPTIVE_VARIANTS[mode_name]["tiers"][-1][1]
                    low_mult_pct = float((mult_sub == bottom_mult).mean() * 100)
                results[spec_name][period_name][mode_name] = {**m, "low_mult_pct": low_mult_pct}
                print(f"{spec_name:<14} | {period_name:<14} | {mode_name:<18} | "
                      f"{m['sharpe']:>+6.2f} | {m['ann_ret']*100:>+6.1f} | {m['max_dd']*100:>+6.1f} | "
                      f"{m['calmar']:>7.2f} | {low_mult_pct:>8.1f}%")

    # Report
    lines: list[str] = []
    def emit(s: str = "") -> None:
        lines.append(s)
    emit("# Adaptive Sizing Validation on 2010-2025 H1 Data")
    emit("")
    emit("CRITICAL test : adaptive layer designed/tested on 2019-2025.")
    emit("Does it still beat static on UNSEEN 2010-2018 data (9 years)?")
    emit("")
    emit(f"Data : Dukascopy H1 bid/ask for {len(FX_PAIRS)} FX pairs, 2010-01-01 to 2025-12-31")
    emit(f"Vol target : {VOL_TARGET*100:.0f}% | Adaptive window : {ROLLING_WINDOW}d")
    emit(f"Production tiers : {ADAPTIVE_TIERS}")
    emit("")

    for spec_name in SPECS_TO_TEST:
        emit(f"## {spec_name}")
        emit("")
        emit("| period | mode | Sharpe | ann% | DD% | Calmar | pause% |")
        emit("|---|---|---:|---:|---:|---:|---:|")
        for period_name in periods:
            for mode_name in ("STATIC", *ADAPTIVE_VARIANTS.keys()):
                m = results[spec_name][period_name][mode_name]
                emit(f"| {period_name} | {mode_name} | {m['sharpe']:+.2f} | "
                     f"{m['ann_ret']*100:+.1f} | {m['max_dd']*100:+.1f} | "
                     f"{m['calmar']:.2f} | {m.get('low_mult_pct', 0):.1f}% |")
        # Delta Calmar
        static_pre = results[spec_name]["PRE_2019"]["STATIC"]["calmar"]
        adapt_pre = results[spec_name]["PRE_2019"]["ADAPTIVE_75_50"]["calmar"]
        unlev_pre = results[spec_name]["PRE_2019"]["ADAPTIVE_75_50_UNLEV"]["calmar"]
        static_post = results[spec_name]["POST_2019"]["STATIC"]["calmar"]
        adapt_post = results[spec_name]["POST_2019"]["ADAPTIVE_75_50"]["calmar"]
        unlev_post = results[spec_name]["POST_2019"]["ADAPTIVE_75_50_UNLEV"]["calmar"]
        emit("")
        emit(f"**Calmar improvement** : PRE_2019 {static_pre:.2f} → {adapt_pre:.2f} "
             f"(Δ {adapt_pre - static_pre:+.2f}) | POST_2019 {static_post:.2f} → {adapt_post:.2f} "
             f"(Δ {adapt_post - static_post:+.2f})")
        emit(f"**Unlevered trigger check** : PRE_2019 {unlev_pre:.2f} | POST_2019 {unlev_post:.2f}")
        emit("")

    # Verdict
    emit("## VERDICT")
    emit("")
    adaptive_wins_pre = 0
    static_wins_pre = 0
    for spec_name in SPECS_TO_TEST:
        static_pre = results[spec_name]["PRE_2019"]["STATIC"]["calmar"]
        adapt_pre = results[spec_name]["PRE_2019"]["ADAPTIVE_75_50"]["calmar"]
        if adapt_pre > static_pre * 1.1:
            adaptive_wins_pre += 1
        elif adapt_pre < static_pre * 0.9:
            static_wins_pre += 1

    if adaptive_wins_pre >= 2:
        verdict = ("**ADAPTIVE confirmed on UNSEEN 2010-2018 data**. The regime-detection "
                   "mechanism appears genuine, not a 2020-COVID-skip artifact.")
    elif static_wins_pre >= 2:
        verdict = ("**WARNING : ADAPTIVE may be overfit to 2019-2025**. On unseen 2010-2018 "
                   "data, static performs better or equal. Adaptive may need recalibration.")
    else:
        verdict = ("**MIXED**. Adaptive ties static on 2010-2018. Real value mostly comes "
                   "from 2020 COVID skip in 2019+ data. Use with caution.")
    emit(verdict)
    emit("")

    OUT_REPORT.write_text("\n".join(lines))
    OUT_METRICS.write_text(json.dumps(results, indent=2, default=str))
    print(f"\ndone\nfiles: {OUT_REPORT.name}, {OUT_METRICS.name}")
    print(f"verdict: {'ADAPTIVE confirmed' if adaptive_wins_pre >= 2 else ('STATIC wins' if static_wins_pre >= 2 else 'MIXED')}")


if __name__ == "__main__":
    main()
