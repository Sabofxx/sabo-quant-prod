"""
Sandbox prototype — Asia Range Liquidity Sweep backtest (v3: global H3 filter).

Pairs: EURUSD, GBPUSD, USDJPY. ~6 years (2019-01-01 → 2026-01-01) on M5/M15/H1
CSVs in sandbox/data/. Sandbox-only: no import from lit/, core/, governance/.

Pipeline:
  1. Asia range (prev_day 23:00 UTC → day 06:00 UTC)
  2. London sweep window (06:00 → 09:00 UTC)
  3. H1 context filter (upper/lower half of last fractal swing pair, 2L/2R)
  4. M15 BOS within `bos_window` M15 bars post-sweep
  5. M5 FVG within `fvg_window` M5 bars post-BOS
  6. H3 filter: skip if (fvg_size_pips / asia_range_pips) > `fvg_ratio_cap`
  7. Limit entry at FVG midpoint, deadline 11:30 UTC
  8. SL at Asia extreme ±2 pips
  9. TP hierarchy: walk {opp_asia, PDH/PDL if dist<=4xSL, opp_H1_swing};
     pick CLOSEST valid candidate in trade direction (first hit going outward)
 10. RR filter >= `rr_floor`; partial 50% at RR=2 if planned RR > 3
 11. 24h max hold; conservative same-bar SL+TP -> SL first

V3 changes vs V2: added H3 filter (fvg_ratio_cap, default 0.10). Grid mode runs
3 configs (v2_relax_all = cap=inf, v3_h3_strict = 0.10, v3_h3_loose = 0.15).
Reporting: comparative with trades_removed + exp_on_removed, EURUSD post-filter
diagnostic (bootstrap CI + H7 regime re-test).

V4 changes vs V3: added `oos` mode for out-of-sample test on AUDUSD/NZDUSD/USDCAD
using frozen v3_h3_strict spec. M15/H1 resampled from M5 for OOS pairs (M5-only
provided, prior authorization). New outputs `_v4_oos`. Trade dict gets `is_oos`.

Ambiguity resolutions (inline comments at each site):
  - "No-trade 12:00-13:00" already covered by entry deadline 11:30 UTC
  - TP "first hit going outward" = CLOSEST valid candidate in trade direction
  - Same M5 bar SL+TP collision -> SL first (conservative)
  - M15 pre-sweep slice excludes M15 candle containing sweep_ts
  - M5 fill candle excluded from SL/TP sim (trade starts at next bar)
  - Asia range is M5 high.max() / low.min() over the 7h window (no gap handling)
  - Timestamps in CSV assumed UTC (epoch ms). Header: timestamp,open,high,low,close.
  - Partial-TP threshold (RR>3) kept hardcoded
  - Bootstrap PF clamped to 999 when sample has no losses (sentinel for percentile)
  - H3 filter applied AFTER FVG detected but BEFORE entry/SL/TP work
    (per spec: "Après FVG détecté, avant entry"). New funnel key `fvg_ratio_ok`.
  - Equity HTML: 3 subplots (one per pair), 3 lines per subplot (one per config) —
    spec mentions "9 panels" but text says "3 courbes superposées par paire" so we
    go superposed (3 panels x 3 lines = 9 line-traces in 3 panels).
  - EURUSD REPAIRED iff exp_R >= +0.10R AND bootstrap CI low > 0.
    INSUFFICIENT_N if n < 20. Else STILL_BROKEN.
  - DXY regime: EURUSD H1 EMA-proxy (delta close over last 120 H1) >|50|p threshold.
"""
from __future__ import annotations

import argparse
import json
import random
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


DATA_DIR = Path(__file__).parent / "data"

# v1/v2 outputs preserved on disk; v3 = new suffixes ; v4 = OOS suffix.
OUT_SAMPLES_V3 = Path(__file__).parent / "asia_london_sample_trades_v3.html"
OUT_EQUITY_V3 = Path(__file__).parent / "asia_london_equity_v3.html"
OUT_TRADES_V4 = Path(__file__).parent / "asia_london_trades_v4_oos.json"
OUT_EQUITY_V4 = Path(__file__).parent / "asia_london_equity_v4_oos.html"
OUT_SAMPLES_V4 = Path(__file__).parent / "asia_london_sample_trades_v4_oos.html"


def out_trades_v3(cfg_name: str) -> Path:
    return Path(__file__).parent / f"asia_london_trades_v3_{cfg_name}.json"


# V3 grid: H3 filter cap on fvg/asia_range ratio. cap=inf reproduces v2.
CONFIGS = {
    "v2_relax_all": {"rr_floor": 1.2, "fvg_window": 10, "bos_window": 16,
                     "fvg_ratio_cap": float("inf")},
    "v3_h3_strict": {"rr_floor": 1.2, "fvg_window": 10, "bos_window": 16,
                     "fvg_ratio_cap": 0.10},
    "v3_h3_loose":  {"rr_floor": 1.2, "fvg_window": 10, "bos_window": 16,
                     "fvg_ratio_cap": 0.15},
}
FOCUS_CFG = "v3_h3_strict"

# OOS test (v4): frozen spec = v3_h3_strict. New pairs added below.
OOS_CFG = CONFIGS["v3_h3_strict"]

PAIRS = ["EURUSD", "GBPUSD", "USDJPY"]  # IS pairs (v3 grid runs on these)
OOS_PAIRS = ["AUDUSD", "NZDUSD", "USDCAD"]  # v4 OOS pairs
PIP = {
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "USDJPY": 0.01,
    "AUDUSD": 0.0001, "NZDUSD": 0.0001, "USDCAD": 0.0001,
}
# OOS asia ranges chosen by vol-comparability to IS calibration. Spec frozen ;
# slight over/under-fit acceptable since we test the SPEC, not the band tuning.
ASIA_BAND = {
    "EURUSD": (15, 50), "GBPUSD": (20, 70), "USDJPY": (15, 50),
    "AUDUSD": (15, 50), "NZDUSD": (15, 55), "USDCAD": (15, 50),
}

FILES = {
    "EURUSD": {
        "M5":  "eurusd-m5-bid-2019-01-01-2026-01-01.csv",
        "M15": "eurusd-m15-bid-2019-01-01-2026-01-01.csv",
        "H1":  "eurusd-h1-bid-2019-01-01-2026-01-01.csv",
    },
    "GBPUSD": {
        "M5":  "gbpusd-m5-bid-2019-01-01-2026-01-01.csv",
        "M15": "gbpusd-m15-bid-2019-01-01-2026-01-01.csv",
        "H1":  "gbpusd-h1-bid-2019-01-01-2026-01-01.csv",
    },
    "USDJPY": {
        "M5":  "usdjpy-m5-bid-2019-01-01-2026-01-01.csv",
        "M15": "usdjpy-m15-bid-2019-01-01-2026-01-01.csv",
        "H1":  "usdjpy-h1-bid-2019-01-01-2026-01-01.csv",
    },
    # OOS pairs: only M5 CSV provided. M15/H1 resampled from M5 at load time
    # (prior user authorization: "oui jautorise" resample M5->M15/H1).
    "AUDUSD": {
        "M5":  "audusd-m5-bid-2019-01-01-2026-01-01.csv",
        "M15": None,
        "H1":  None,
    },
    "NZDUSD": {
        "M5":  "nzdusd-m5-bid-2019-01-01-2026-01-01.csv",
        "M15": None,
        "H1":  None,
    },
    "USDCAD": {
        "M5":  "usdcad-m5-bid-2019-01-01-2026-01-01.csv",
        "M15": None,
        "H1":  None,
    },
}


# =====================================================================
# Loading
# =====================================================================
def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    # drop exact dup timestamps if any
    df = df[~df.index.duplicated(keep="first")]
    return df


def resample_ohlc(m5: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Resample M5 OHLC into freq bins ('15min' or '1h'). Drops empty bins (gaps)."""
    agg = m5.resample(freq, label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
    })
    return agg.dropna()


def load_pair_frames(pair: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load M5+M15+H1 for a pair. If M15/H1 file missing in FILES, resample from M5.
    Used in OOS mode for AUD/NZD/CAD (M5-only provided)."""
    paths = FILES[pair]
    m5 = load(DATA_DIR / paths["M5"])
    if paths["M15"] is None:
        m15 = resample_ohlc(m5, "15min")
    else:
        m15 = load(DATA_DIR / paths["M15"])
    if paths["H1"] is None:
        h1 = resample_ohlc(m5, "1h")
    else:
        h1 = load(DATA_DIR / paths["H1"])
    return m5, m15, h1


# =====================================================================
# Geometry helpers
# =====================================================================
def fractal_pivots(highs: list[float], lows: list[float]) -> tuple[list[int], list[int]]:
    """2L/2R fractal pivots. Returns (high_pivot_idx, low_pivot_idx)."""
    hi: list[int] = []
    lo: list[int] = []
    n = len(highs)
    for i in range(2, n - 2):
        if (highs[i] > highs[i - 1] and highs[i] > highs[i - 2]
                and highs[i] > highs[i + 1] and highs[i] > highs[i + 2]):
            hi.append(i)
        if (lows[i] < lows[i - 1] and lows[i] < lows[i - 2]
                and lows[i] < lows[i + 1] and lows[i] < lows[i + 2]):
            lo.append(i)
    return hi, lo


def pick_sweep(london: pd.DataFrame, asia_high: float, asia_low: float, pip: float):
    """First qualifying sweep: wick >= 2 pips beyond Asia extreme AND close back inside.
    Returns (ts, direction, extreme) or None."""
    margin = 2 * pip
    for ts, c in london.iterrows():
        if c["high"] >= asia_high + margin and asia_low <= c["close"] <= asia_high:
            return ts, "short", c["high"]
        if c["low"] <= asia_low - margin and asia_low <= c["close"] <= asia_high:
            return ts, "long", c["low"]
    return None


def h1_context_ok(h1_recent: pd.DataFrame, current_price: float, direction: str):
    """Returns (bool ok, swing_low, swing_high). Needs >=50 H1 + at least 1 pivot of each kind."""
    if len(h1_recent) < 50:
        return False, None, None
    highs = h1_recent["high"].tolist()
    lows = h1_recent["low"].tolist()
    hi_idx, lo_idx = fractal_pivots(highs, lows)
    if not hi_idx or not lo_idx:
        return False, None, None
    swing_high = highs[hi_idx[-1]]
    swing_low = lows[lo_idx[-1]]
    if swing_high <= swing_low:
        # degenerate: skip
        return False, swing_low, swing_high
    mid = (swing_high + swing_low) / 2
    if direction == "short" and current_price >= mid:
        return True, swing_low, swing_high
    if direction == "long" and current_price <= mid:
        return True, swing_low, swing_high
    return False, swing_low, swing_high


def m15_bos(m15_pre: pd.DataFrame, m15_window: pd.DataFrame, direction: str):
    """BOS within 12 M15 candles (1h) post-sweep. Returns M15 close ts or None."""
    if len(m15_pre) < 50:
        return None
    highs = m15_pre["high"].tolist()
    lows = m15_pre["low"].tolist()
    hi_idx, lo_idx = fractal_pivots(highs, lows)
    if not hi_idx or not lo_idx:
        return None
    swing_high = highs[hi_idx[-1]]
    swing_low = lows[lo_idx[-1]]
    for ts, c in m15_window.iterrows():
        if direction == "short" and c["close"] < swing_low:
            return ts
        if direction == "long" and c["close"] > swing_high:
            return ts
    return None


def first_fvg(m5_post: pd.DataFrame, direction: str):
    """First FVG within 6 M5 candles post-BOS. Returns (low_band, high_band, mid_ts) or None.
       Long: bullish FVG  high[N-1] < low[N+1]  ->  gap = [high[N-1], low[N+1]]
       Short: bearish FVG low[N-1] > high[N+1]  ->  gap = [high[N+1], low[N-1]]
    """
    if len(m5_post) < 3:
        return None
    hs = m5_post["high"].tolist()
    ls = m5_post["low"].tolist()
    ts = m5_post.index.tolist()
    for n in range(1, len(m5_post) - 1):
        if direction == "long":
            if hs[n - 1] < ls[n + 1]:
                return hs[n - 1], ls[n + 1], ts[n]
        else:
            if ls[n - 1] > hs[n + 1]:
                return hs[n + 1], ls[n - 1], ts[n]
    return None


def pick_tp(entry: float, direction: str, asia_high: float, asia_low: float,
            sl_dist: float, pdh: float | None, pdl: float | None,
            h1_swing_low: float, h1_swing_high: float):
    """Walk hierarchy; pick CLOSEST valid in direction.
    Interpretation: "first hit going outward" = first one price reaches as it travels outward
    = closest in direction. Conservative (smaller R but earlier-hit TP).
    """
    candidates: list[float] = []
    # 1. opposite Asia
    opp_asia = asia_low if direction == "short" else asia_high
    if (direction == "short" and opp_asia < entry) or (direction == "long" and opp_asia > entry):
        candidates.append(opp_asia)
    # 2. PDH/PDL in direction if within 4xSL distance
    target2 = pdl if direction == "short" else pdh
    if target2 is not None:
        if direction == "short" and target2 < entry and (entry - target2) <= 4 * sl_dist:
            candidates.append(target2)
        elif direction == "long" and target2 > entry and (target2 - entry) <= 4 * sl_dist:
            candidates.append(target2)
    # 3. opposite H1 swing (always "reachable" by spec wording)
    opp_swing = h1_swing_low if direction == "short" else h1_swing_high
    if (direction == "short" and opp_swing < entry) or (direction == "long" and opp_swing > entry):
        candidates.append(opp_swing)
    if not candidates:
        return None
    # Closest to entry in trade direction
    return max(candidates) if direction == "short" else min(candidates)


def attempt_fill(m5_window: pd.DataFrame, entry: float, direction: str,
                 sweep_extreme: float, deadline) -> "datetime | None":
    """Limit fill: triggers when M5 candle range covers entry. Cancel rules:
       - clock > deadline
       - close beyond sweep extreme (price gave up the setup)
    """
    for ts, c in m5_window.iterrows():
        if ts > deadline:
            return None
        # cancel if close beyond sweep extreme
        if direction == "short" and c["close"] > sweep_extreme:
            return None
        if direction == "long" and c["close"] < sweep_extreme:
            return None
        if c["low"] <= entry <= c["high"]:
            return ts
    return None


def simulate_trade(m5_24h: pd.DataFrame, entry: float, sl: float, tp_final: float,
                   tp_partial: float | None, direction: str, sl_dist: float):
    """Simulate trade on M5 bars (fill bar excluded). Conservative: same-bar SL+TP -> SL first.
    Partial 50% at tp_partial: hit -> +2R on that half; remaining 50% rides to tp_final or SL.
    Timeout (24h, no exit) -> close at last close.
    Returns (exit_ts, exit_price, exit_reason, r_realized, hold_minutes).
    """
    if m5_24h.empty:
        return None, None, "no_data", 0.0, 0.0
    partial_filled = False
    entry_ts = m5_24h.index[0]
    for ts, c in m5_24h.iterrows():
        if direction == "short":
            sl_hit = c["high"] >= sl
            tp_p_hit = (tp_partial is not None and not partial_filled and c["low"] <= tp_partial)
            tp_f_hit = c["low"] <= tp_final
        else:
            sl_hit = c["low"] <= sl
            tp_p_hit = (tp_partial is not None and not partial_filled and c["high"] >= tp_partial)
            tp_f_hit = c["high"] >= tp_final

        if sl_hit:
            # if partial already booked: +1R on half + (-1R on half) = +0.0; but partial = 2R on half
            # net: +0.5 * 2R - 0.5 * 1R = +0.5
            r = (0.5 * 2.0 - 0.5 * 1.0) if partial_filled else -1.0
            hold = (ts - entry_ts).total_seconds() / 60
            return ts, sl, "sl", r, hold
        if tp_p_hit and not partial_filled:
            partial_filled = True
            # continue scanning for tp_final / sl
        if tp_f_hit:
            rr_final = abs(tp_final - entry) / sl_dist
            r = (0.5 * 2.0 + 0.5 * rr_final) if partial_filled else rr_final
            hold = (ts - entry_ts).total_seconds() / 60
            return ts, tp_final, "tp", r, hold

    # timeout: close at last close
    last_ts = m5_24h.index[-1]
    last_close = m5_24h.iloc[-1]["close"]
    if direction == "short":
        pl = (entry - last_close) / sl_dist
    else:
        pl = (last_close - entry) / sl_dist
    r = (0.5 * 2.0 + 0.5 * pl) if partial_filled else pl
    hold = (last_ts - entry_ts).total_seconds() / 60
    return last_ts, last_close, "timeout", r, hold


# =====================================================================
# Backtest per pair
# =====================================================================
def backtest_pair(pair: str, m5_df: pd.DataFrame, m15_df: pd.DataFrame, h1_df: pd.DataFrame,
                  *, rr_floor: float, fvg_window: int, bos_window: int,
                  fvg_ratio_cap: float = float("inf"),
                  ) -> tuple[list[dict], dict]:
    pip = PIP[pair]
    band_lo, band_hi = ASIA_BAND[pair]
    trades: list[dict] = []
    funnel: dict = defaultdict(int)

    dates = sorted(set(m5_df.index.date))
    last_year = None
    t0 = time.time()

    for date in dates:
        funnel["days_total"] += 1
        if date.year != last_year:
            print(f"  [{pair}] year {date.year} (elapsed {time.time() - t0:.1f}s, trades so far={len(trades)})")
            last_year = date.year

        d_start = datetime.combine(date, datetime.min.time(), tzinfo=timezone.utc)
        asia_start = d_start - timedelta(hours=1)               # prev_day 23:00
        asia_end_excl = d_start + timedelta(hours=6)             # exclusive: 06:00 not included in Asia
        london_start = d_start + timedelta(hours=6)
        london_end_excl = d_start + timedelta(hours=9)
        deadline = d_start + timedelta(hours=11, minutes=30)

        # ASIA: M5 in [asia_start, asia_end_excl)
        asia = m5_df.loc[asia_start:asia_end_excl - timedelta(minutes=5)]
        if len(asia) < 30:
            continue  # weekend/holiday/gap
        asia_high = float(asia["high"].max())
        asia_low = float(asia["low"].min())
        asia_range = (asia_high - asia_low) / pip
        if not (band_lo <= asia_range <= band_hi):
            continue
        funnel["asia_qualified"] += 1

        london = m5_df.loc[london_start:london_end_excl - timedelta(minutes=5)]
        if london.empty:
            continue
        sweep = pick_sweep(london, asia_high, asia_low, pip)
        if sweep is None:
            continue
        funnel["sweeps"] += 1
        sweep_ts, direction, sweep_extreme = sweep
        sweep_close = float(london.loc[sweep_ts, "close"])

        # H1 context: last 50 H1 bars at or before sweep
        h1_recent = h1_df.loc[:sweep_ts].tail(50)
        ok, h1_swing_low, h1_swing_high = h1_context_ok(h1_recent, sweep_close, direction)
        if not ok:
            continue
        funnel["h1_ok"] += 1

        # M15 BOS: pre = last 50 fully-closed M15 strictly before sweep's M15 bar
        m15_floor = sweep_ts.floor("15min")
        m15_pre = m15_df.loc[:m15_floor - timedelta(minutes=15)].tail(50)
        # m15 search window: bos_window bars starting from the M15 bar right after sweep's M15
        m15_window = m15_df.loc[m15_floor + timedelta(minutes=15):].head(bos_window)
        bos_ts = m15_bos(m15_pre, m15_window, direction)
        if bos_ts is None:
            continue
        funnel["bos_m15_ok"] += 1

        # FVG: fvg_window M5 bars starting right after bos_ts (M5 bar AFTER the BOS M15 close)
        m5_post = m5_df.loc[bos_ts + timedelta(minutes=5):].head(fvg_window)
        fvg = first_fvg(m5_post, direction)
        if fvg is None:
            continue
        funnel["fvg_ok"] += 1
        fvg_low, fvg_high, fvg_ts = fvg
        # H3 filter (v3): cap on FVG size relative to Asia range
        fvg_size_pips = (fvg_high - fvg_low) / pip
        fvg_ratio = fvg_size_pips / asia_range if asia_range > 0 else float("inf")
        if fvg_ratio > fvg_ratio_cap:
            continue
        funnel["fvg_ratio_ok"] += 1
        entry = (fvg_low + fvg_high) / 2

        sl = (asia_high + 2 * pip) if direction == "short" else (asia_low - 2 * pip)
        sl_dist = abs(entry - sl)
        if sl_dist <= 0:
            continue

        # Prior day full M5 (D-1 00:00 → D-1 23:59)
        prior_start = d_start - timedelta(days=1)
        prior_end = d_start - timedelta(minutes=5)
        prior = m5_df.loc[prior_start:prior_end]
        if prior.empty:
            pdh, pdl = None, None
        else:
            pdh = float(prior["high"].max())
            pdl = float(prior["low"].min())

        tp_final = pick_tp(entry, direction, asia_high, asia_low, sl_dist,
                           pdh, pdl, h1_swing_low, h1_swing_high)
        if tp_final is None:
            continue
        reward_dist = abs(tp_final - entry)
        rr = reward_dist / sl_dist
        if rr < rr_floor:
            continue
        funnel["rr_ok"] += 1
        if rr > 3:
            tp_partial = entry + (2 * sl_dist if direction == "long" else -2 * sl_dist)
        else:
            tp_partial = None

        # Fill: M5 bars starting right after the FVG mid-bar, until deadline
        m5_fill = m5_df.loc[fvg_ts + timedelta(minutes=5):deadline]
        fill_ts = attempt_fill(m5_fill, entry, direction, sweep_extreme, deadline)
        if fill_ts is None:
            continue
        funnel["filled"] += 1

        # Sim 24h from bar after fill
        sim = m5_df.loc[fill_ts + timedelta(minutes=5):fill_ts + timedelta(hours=24)]
        if sim.empty:
            continue
        exit_ts, exit_price, exit_reason, r_realized, hold_min = simulate_trade(
            sim, entry, sl, tp_final, tp_partial, direction, sl_dist,
        )
        if exit_reason == "no_data":
            continue
        funnel["closed"] += 1

        trades.append({
            "pair": pair,
            "date": date.isoformat(),
            "direction": direction,
            "asia_high": asia_high,
            "asia_low": asia_low,
            "asia_range_pips": asia_range,
            "sweep_time": sweep_ts.isoformat(),
            "sweep_extreme": sweep_extreme,
            "bos_m15_time": bos_ts.isoformat(),
            "fvg_zone": [fvg_low, fvg_high],
            "fvg_size_pips": fvg_size_pips,
            "fvg_ratio": fvg_ratio,
            "entry_time": fill_ts.isoformat(),
            "entry_price": entry,
            "sl": sl,
            "tp_final": tp_final,
            "tp_partial": tp_partial,
            "rr_planned": rr,
            "exit_time": exit_ts.isoformat(),
            "exit_price": float(exit_price),
            "exit_reason": exit_reason,
            "r_realized": r_realized,
            "hold_minutes": hold_min,
            "h1_context_ok": True,
        })

    return trades, dict(funnel)


# =====================================================================
# Stats / reporting
# =====================================================================
def stats_block(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "exp": 0.0, "max_dd": 0.0, "avg_hold": 0.0}
    rs = [t["r_realized"] for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    wr = 100 * len(wins) / len(rs)
    if losses and sum(losses) < 0:
        pf = sum(wins) / abs(sum(losses))
    else:
        pf = float("inf") if wins else 0.0
    exp = sum(rs) / len(rs)
    cum = 0.0
    peak = -float("inf")
    max_dd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        dd = cum - peak
        max_dd = min(max_dd, dd)
    avg_hold = sum(t["hold_minutes"] for t in trades) / len(trades)
    return {"n": len(trades), "wr": wr, "pf": pf, "exp": exp, "max_dd": max_dd, "avg_hold": avg_hold}


def decomp_print(trades, key_fn, label):
    g: dict = defaultdict(list)
    for t in trades:
        g[key_fn(t)].append(t)
    print(f"\n### by {label}")
    print("| key | n | wr% | pf | exp |")
    print("|---|---|---|---|---|")
    for k in sorted(g.keys(), key=lambda x: str(x)):
        s = stats_block(g[k])
        pf_str = f"{s['pf']:.2f}" if s['pf'] != float("inf") else "inf"
        print(f"| {k} | {s['n']} | {s['wr']:.1f} | {pf_str} | {s['exp']:+.3f} |")


def histogram(values: list[float], bin_size: float, label: str, fmt: str = "{:+.1f}"):
    print(f"\n### histogram — {label} (bin {bin_size})")
    if not values:
        print("  (empty)")
        return
    bins: dict = defaultdict(int)
    for v in values:
        k = (int(v // bin_size)) * bin_size
        bins[round(k, 4)] += 1
    mx = max(bins.values())
    for k in sorted(bins.keys()):
        bar = "#" * int(bins[k] / mx * 40) if mx else ""
        print(f"  [{fmt.format(k)}, {fmt.format(k + bin_size)})  {bins[k]:4d}  {bar}")


# =====================================================================
# Viz: equity + samples
# =====================================================================
def write_equity(trades_by_pair: dict, out_path: Path, title: str):
    fig = go.Figure()
    for pair, trs in trades_by_pair.items():
        if not trs:
            continue
        cum = []
        c = 0.0
        for t in trs:
            c += t["r_realized"]
            cum.append(c)
        # max DD marker
        peak = -float("inf")
        dd_val = 0.0
        dd_idx = 0
        for i, e in enumerate(cum):
            peak = max(peak, e)
            d = e - peak
            if d < dd_val:
                dd_val = d
                dd_idx = i
        fig.add_trace(go.Scatter(y=cum, mode="lines", name=f"{pair} cum R ({len(trs)} trades)"))
        fig.add_trace(go.Scatter(
            x=[dd_idx], y=[cum[dd_idx]],
            mode="markers",
            marker=dict(symbol="x", size=14, color="red"),
            name=f"{pair} maxDD={dd_val:.1f}R",
        ))
    fig.update_layout(
        title=title,
        xaxis_title="trade #",
        yaxis_title="cumulative R",
        template="plotly_white",
        height=700,
    )
    fig.write_html(str(out_path), include_plotlyjs="cdn")


def write_samples(trades: list[dict], m5_data: dict, out_path: Path):
    if not trades:
        print("  (no trades for samples)")
        return
    wins = [t for t in trades if t["r_realized"] > 0]
    losses = [t for t in trades if t["r_realized"] <= 0]
    random.seed(42)
    pick_w = random.sample(wins, min(5, len(wins))) if wins else []
    pick_l = random.sample(losses, min(5, len(losses))) if losses else []
    picks = pick_w + pick_l
    if not picks:
        return
    rows = max(1, (len(picks) + 1) // 2)
    cols = 2
    titles = [
        f"{t['pair']} {t['date']} {t['direction']} R={t['r_realized']:+.2f} ({t['exit_reason']})"
        for t in picks
    ]
    while len(titles) < rows * cols:
        titles.append("")
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=titles)

    for i, t in enumerate(picks):
        row = i // 2 + 1
        col = i % 2 + 1
        pair = t["pair"]
        m5 = m5_data[pair]
        date = pd.to_datetime(t["date"]).date()
        d_start = datetime.combine(date, datetime.min.time(), tzinfo=timezone.utc)
        win_start = d_start - timedelta(hours=2)
        win_end = pd.to_datetime(t["exit_time"]) + timedelta(hours=2)
        sl = m5.loc[win_start:win_end]
        if sl.empty:
            continue
        fig.add_trace(go.Candlestick(
            x=sl.index, open=sl["open"], high=sl["high"], low=sl["low"], close=sl["close"],
            showlegend=False, name="m5",
            increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        ), row=row, col=col)
        # asia range box
        fig.add_shape(
            type="rect",
            x0=d_start - timedelta(hours=1), x1=d_start + timedelta(hours=6),
            y0=t["asia_low"], y1=t["asia_high"],
            line=dict(color="gray", width=1), fillcolor="gray", opacity=0.1,
            row=row, col=col,
        )
        # sweep marker
        fig.add_trace(go.Scatter(
            x=[pd.to_datetime(t["sweep_time"])], y=[t["sweep_extreme"]],
            mode="markers", marker=dict(symbol="diamond", size=11, color="orange"),
            name="sweep", showlegend=False,
        ), row=row, col=col)
        # BOS marker (at entry price level for placement)
        fig.add_trace(go.Scatter(
            x=[pd.to_datetime(t["bos_m15_time"])], y=[t["entry_price"]],
            mode="markers", marker=dict(symbol="x", size=10, color="purple"),
            name="BOS", showlegend=False,
        ), row=row, col=col)
        # FVG zone (span: bos -> entry)
        fig.add_shape(
            type="rect",
            x0=pd.to_datetime(t["bos_m15_time"]), x1=pd.to_datetime(t["entry_time"]),
            y0=t["fvg_zone"][0], y1=t["fvg_zone"][1],
            line=dict(color="blue", width=1), fillcolor="blue", opacity=0.2,
            row=row, col=col,
        )
        # entry/SL/TP lines (subplot-scoped via add_hline with row/col)
        fig.add_hline(y=t["entry_price"], line_dash="dash", line_color="black", row=row, col=col)
        fig.add_hline(y=t["sl"], line_dash="dash", line_color="red", row=row, col=col)
        fig.add_hline(y=t["tp_final"], line_dash="dash", line_color="green", row=row, col=col)
        if t["tp_partial"] is not None:
            fig.add_hline(y=t["tp_partial"], line_dash="dot", line_color="green", row=row, col=col)

    fig.update_layout(
        height=rows * 350,
        template="plotly_white",
        title="Asia London Sweep — sample trades (5 wins / 5 losses)",
        showlegend=False,
    )
    fig.update_xaxes(rangeslider_visible=False)
    fig.write_html(str(out_path), include_plotlyjs="cdn")


# =====================================================================
# Robustness probes
# =====================================================================
def bootstrap_ci(trades: list[dict], n_resample: int = 1000, ci_level: float = 0.95,
                 seed: int = 42) -> dict | None:
    if not trades:
        return None
    rng = random.Random(seed)
    rs = [t["r_realized"] for t in trades]
    n = len(rs)
    pfs: list[float] = []
    exps: list[float] = []
    wrs: list[float] = []
    for _ in range(n_resample):
        sample = [rng.choice(rs) for _ in range(n)]
        wins = [r for r in sample if r > 0]
        losses = [r for r in sample if r <= 0]
        sum_losses = sum(losses)
        if sum_losses < 0:
            pf = sum(wins) / abs(sum_losses)
        else:
            pf = 999.0 if wins else 0.0  # sentinel; sample with zero net loss
        pfs.append(pf)
        exps.append(sum(sample) / n)
        wrs.append(100 * len(wins) / n)
    pfs.sort()
    exps.sort()
    wrs.sort()
    lo_i = int(n_resample * (1 - ci_level) / 2)
    hi_i = int(n_resample * (1 + ci_level) / 2) - 1
    return {
        "pf": (pfs[lo_i], pfs[hi_i]),
        "exp": (exps[lo_i], exps[hi_i]),
        "wr": (wrs[lo_i], wrs[hi_i]),
    }


def walk_forward_halves(trades: list[dict]):
    """Chronological split by entry_time. Returns (h1_stats, h2_stats, h1_range, h2_range)."""
    if len(trades) < 4:
        return None
    sorted_t = sorted(trades, key=lambda t: t["entry_time"])
    mid = len(sorted_t) // 2
    h1 = sorted_t[:mid]
    h2 = sorted_t[mid:]
    s1 = stats_block(h1)
    s2 = stats_block(h2)
    rng1 = (h1[0]["date"], h1[-1]["date"])
    rng2 = (h2[0]["date"], h2[-1]["date"])
    return s1, s2, rng1, rng2


def per_year(trades: list[dict]) -> dict:
    g: dict = defaultdict(list)
    for t in trades:
        y = pd.to_datetime(t["date"]).year
        g[y].append(t)
    return g


# =====================================================================
# Verdict / recommendation
# =====================================================================
def aggregate_global(trades_by_pair: dict, funnel_by_pair: dict) -> dict:
    all_t: list[dict] = []
    for p in trades_by_pair:
        all_t.extend(trades_by_pair[p])
    s = stats_block(all_t)
    for key in ("fvg_ok", "fvg_ratio_ok", "rr_ok", "filled", "closed"):
        s[key] = sum(funnel_by_pair[p].get(key, 0) for p in funnel_by_pair)
    return s


def dxy_regime(trade: dict, h1_df: pd.DataFrame, pip: float) -> str | None:
    """EURUSD trend over last 120 H1 bars (~5 days). Threshold ±50 pips."""
    sweep_ts = pd.to_datetime(trade["sweep_time"])
    window = h1_df.loc[:sweep_ts].tail(120)
    if len(window) < 100:
        return None
    last_close = float(window.iloc[-1]["close"])
    first_close = float(window.iloc[0]["close"])
    delta_pips = (last_close - first_close) / pip
    if delta_pips > 50:
        return "EUR_up_DXY_down"
    if delta_pips < -50:
        return "EUR_down_DXY_up"
    return "ranging"


def verdict(cfg_agg: dict, base_agg: dict) -> str:
    """GROW: n grows >=1.3x AND total_R grows. DEGRADE: total_R drops <0.7x OR negative.
    STABLE: anything else.
    """
    base_total = base_agg["n"] * base_agg["exp"]
    cfg_total = cfg_agg["n"] * cfg_agg["exp"]
    n_ratio = cfg_agg["n"] / base_agg["n"] if base_agg["n"] else 0.0
    # if exp goes negative while baseline was positive => DEGRADE
    if base_total > 0:
        if cfg_total <= 0:
            return "DEGRADE"
        if cfg_total < base_total * 0.7:
            return "DEGRADE"
        if n_ratio >= 1.3 and cfg_total > base_total:
            return "GROW"
    else:
        # baseline non-profitable; growth = becoming profitable
        if cfg_total > 0 and n_ratio >= 1.3:
            return "GROW"
        if cfg_total < base_total:
            return "DEGRADE"
    return "STABLE"


def recommend(results: dict) -> str:
    """Pick best config: n>=30 AND pf>=1.2 AND exp>0, maximizing total_R. Else NONE."""
    cands = []
    for name, r in results.items():
        if name == "baseline":
            continue
        a = r["agg"]
        if a["n"] < 30:
            continue
        if a["pf"] < 1.2 or a["pf"] == float("inf"):
            # inf is fine (no losses), but very small n likely. Keep filter loose: require pf<inf only if it would dominate.
            if a["pf"] == float("inf"):
                pass
            else:
                continue
        if a["exp"] <= 0:
            continue
        cands.append((name, a["n"] * a["exp"], a))
    if not cands:
        return "NONE"
    cands.sort(key=lambda x: -x[1])
    return cands[0][0]


# =====================================================================
# V3 reporting helpers
# =====================================================================
def _pf_str(pf: float) -> str:
    return f"{pf:.2f}" if pf != float("inf") else "inf"


def _trade_key(t: dict) -> tuple:
    """Stable identity for a trade across configs: (pair, date, sweep_time)."""
    return (t["pair"], t["date"], t["sweep_time"])


def run_one(cfg_name: str, cfg: dict, m5_data: dict, m15_data: dict, h1_data: dict
            ) -> tuple[dict, dict]:
    trades_by_pair: dict = {}
    funnel_by_pair: dict = {}
    for p in PAIRS:
        trs, fn = backtest_pair(
            p, m5_data[p], m15_data[p], h1_data[p],
            rr_floor=cfg["rr_floor"],
            fvg_window=cfg["fvg_window"],
            bos_window=cfg["bos_window"],
            fvg_ratio_cap=cfg["fvg_ratio_cap"],
        )
        trades_by_pair[p] = trs
        funnel_by_pair[p] = fn
        print(f"  [{cfg_name}/{p}] n={len(trs)} "
              f"fvg_ok={fn.get('fvg_ok',0)} fvg_ratio_ok={fn.get('fvg_ratio_ok',0)} "
              f"rr_ok={fn.get('rr_ok',0)} filled={fn.get('filled',0)}")
    return trades_by_pair, funnel_by_pair


def print_comparative_global(results: dict) -> None:
    print("\n=== COMPARATIVE (global) ===")
    print("| config        | n   | wr%   | pf    | exp_R   | maxDD  | total_R |")
    print("|---------------|-----|-------|-------|---------|--------|---------|")
    for name, r in results.items():
        a = r["agg"]
        total = a["n"] * a["exp"]
        print(f"| {name:<13} | {a['n']:>3} | {a['wr']:>5.1f} | {_pf_str(a['pf']):>5} | "
              f"{a['exp']:+.3f} | {a['max_dd']:>+6.1f} | {total:+7.2f} |")


def print_comparative_per_pair(results: dict, base_cfg_name: str) -> None:
    """Per-pair table with trades_removed + exp_on_removed columns
    (removed = trades present in base_cfg, absent here)."""
    base = results[base_cfg_name]["trades_by_pair"]
    for p in PAIRS:
        base_trades = base[p]
        base_keys = {_trade_key(t): t for t in base_trades}
        print(f"\n=== COMPARATIVE — {p} ===")
        print("| config        | n   | wr%   | pf    | exp_R   | trades_removed | exp_on_removed |")
        print("|---------------|-----|-------|-------|---------|----------------|----------------|")
        for name, r in results.items():
            trs = r["trades_by_pair"][p]
            s = stats_block(trs)
            if name == base_cfg_name:
                removed_str = "-"
                exp_removed_str = "-"
            else:
                cfg_keys = {_trade_key(t) for t in trs}
                removed = [base_keys[k] for k in base_keys if k not in cfg_keys]
                if removed:
                    exp_rm = sum(t["r_realized"] for t in removed) / len(removed)
                    removed_str = f"{len(removed)}"
                    exp_removed_str = f"{exp_rm:+.3f}"
                else:
                    removed_str = "0"
                    exp_removed_str = "n/a"
            print(f"| {name:<13} | {s['n']:>3} | {s['wr']:>5.1f} | "
                  f"{_pf_str(s['pf']):>5} | {s['exp']:+.3f} | "
                  f"{removed_str:>14} | {exp_removed_str:>14} |")


def print_funnel(funnel_by_pair: dict, cfg_label: str) -> None:
    funnel_keys = ["days_total", "asia_qualified", "sweeps", "h1_ok",
                   "bos_m15_ok", "fvg_ok", "fvg_ratio_ok", "rr_ok", "filled", "closed"]
    print(f"\n=== FUNNEL ({cfg_label}) ===")
    print("| stage | EURUSD | GBPUSD | USDJPY | global |")
    print("|---|---|---|---|---|")
    for k in funnel_keys:
        e = funnel_by_pair["EURUSD"].get(k, 0)
        g = funnel_by_pair["GBPUSD"].get(k, 0)
        u = funnel_by_pair["USDJPY"].get(k, 0)
        print(f"| {k} | {e} | {g} | {u} | {e + g + u} |")


def print_focus_decomps(trades_by_pair: dict, cfg_label: str) -> None:
    all_t: list[dict] = []
    for p in PAIRS:
        all_t.extend(trades_by_pair[p])
    print(f"\n=== STATS per pair ({cfg_label}) ===")
    for p in PAIRS:
        s = stats_block(trades_by_pair[p])
        print(f"  {p}: n={s['n']:4d}  wr={s['wr']:5.1f}%  pf={_pf_str(s['pf']):>5}  "
              f"exp={s['exp']:+.3f}R  maxDD={s['max_dd']:.1f}R  avg_hold={s['avg_hold']:.0f}min")
    sg = stats_block(all_t)
    print(f"  GLOBAL: n={sg['n']:4d}  wr={sg['wr']:5.1f}%  pf={_pf_str(sg['pf']):>5}  "
          f"exp={sg['exp']:+.3f}R  maxDD={sg['max_dd']:.1f}R  avg_hold={sg['avg_hold']:.0f}min")

    decomp_print(all_t, lambda t: pd.to_datetime(t["date"]).strftime("%Y-%m"), "month")
    decomp_print(all_t, lambda t: pd.to_datetime(t["date"]).strftime("%A"), "weekday")
    decomp_print(all_t, lambda t: pd.to_datetime(t["sweep_time"]).hour, "sweep hour (UTC)")

    print("\n### by asia range pip-bucket (tertiles per pair)")
    for p in PAIRS:
        trs = trades_by_pair[p]
        if len(trs) < 3:
            continue
        sorted_r = sorted(t["asia_range_pips"] for t in trs)
        n = len(sorted_r)
        b1 = sorted_r[n // 3]
        b2 = sorted_r[2 * n // 3]
        def _bucket(r, b1=b1, b2=b2):
            if r < b1:
                return f"low(<{b1:.0f})"
            if r < b2:
                return f"mid({b1:.0f}-{b2:.0f})"
            return f"hi(>{b2:.0f})"
        g: dict = defaultdict(list)
        for t in trs:
            g[_bucket(t["asia_range_pips"])].append(t)
        print(f"  {p}:")
        for k in sorted(g.keys()):
            s = stats_block(g[k])
            print(f"    {k}: n={s['n']:3d} wr={s['wr']:5.1f}% pf={_pf_str(s['pf']):>5} exp={s['exp']:+.3f}")

    def rr_bucket(t):
        rr = t["rr_planned"]
        if rr < 2:
            return "<2"
        if rr < 3:
            return "2-3"
        return "3+"
    decomp_print(all_t, rr_bucket, "RR bucket")

    histogram([t["r_realized"] for t in all_t], 0.5, "R realized", "{:+.1f}")
    histogram([t["hold_minutes"] for t in all_t], 60.0, "hold minutes", "{:.0f}")


def print_robustness(trades_by_pair: dict, cfg_label: str) -> None:
    all_t: list[dict] = []
    for p in PAIRS:
        all_t.extend(trades_by_pair[p])

    print(f"\n=== ROBUSTNESS — bootstrap 95% CI ({cfg_label}, n_resample=1000) ===")
    ci = bootstrap_ci(all_t)
    if ci is None:
        print("  (empty)")
    else:
        pf_lo, pf_hi = ci["pf"]
        ex_lo, ex_hi = ci["exp"]
        wr_lo, wr_hi = ci["wr"]
        print(f"  PF   : [{pf_lo:.2f}, {pf_hi:.2f}]"
              + ("  (upper bound is sentinel 999 = some resamples had zero losses)"
                 if pf_hi >= 999 else ""))
        print(f"  exp_R: [{ex_lo:+.3f}, {ex_hi:+.3f}]")
        print(f"  wr%  : [{wr_lo:.1f}, {wr_hi:.1f}]")

    print(f"\n=== ROBUSTNESS — walk-forward halves (chronological, {cfg_label}) ===")
    wf = walk_forward_halves(all_t)
    if wf is None:
        print("  (insufficient trades)")
    else:
        s1, s2, rng1, rng2 = wf
        print(f"  H1 ({rng1[0]} → {rng1[1]}): n={s1['n']} pf={_pf_str(s1['pf'])} exp={s1['exp']:+.3f}R wr={s1['wr']:.1f}%")
        print(f"  H2 ({rng2[0]} → {rng2[1]}): n={s2['n']} pf={_pf_str(s2['pf'])} exp={s2['exp']:+.3f}R wr={s2['wr']:.1f}%")
        if s1["pf"] not in (0.0, float("inf")) and s2["pf"] not in (0.0, float("inf")):
            drop = (s1["pf"] - s2["pf"]) / s1["pf"] * 100
            if drop > 30:
                print(f"  WARN: PF drops {drop:.0f}% H1→H2 (>30% threshold). Edge may be regime-dependent.")
            elif drop < -30:
                print(f"  NOTE: PF improves {-drop:.0f}% H1→H2 (edge strengthening).")
            else:
                print(f"  OK: PF change H1→H2 = {-drop:+.0f}% (within ±30%).")

    print(f"\n=== ROBUSTNESS — per-year breakdown ({cfg_label}) ===")
    print("| year | n | wr% | pf | exp_R |")
    print("|---|---|---|---|---|")
    py = per_year(all_t)
    for y in sorted(py.keys()):
        s = stats_block(py[y])
        print(f"| {y} | {s['n']} | {s['wr']:.1f} | {_pf_str(s['pf']):>5} | {s['exp']:+.3f} |")


def eurusd_post_filter_diagnostic(eurusd_trades: list[dict], h1_eur: pd.DataFrame) -> dict:
    """Bootstrap CI on EURUSD subset + H7 regime split. Returns dict with verdict."""
    print("\n=== EURUSD POST-H3 DIAGNOSTIC ===")
    n = len(eurusd_trades)
    if n == 0:
        print("  (no EURUSD trades survived filter)")
        return {"n": 0, "exp": 0.0, "ci": None, "regimes": {}, "verdict": "INSUFFICIENT_N"}
    s = stats_block(eurusd_trades)
    print(f"  n={n}  wr={s['wr']:.1f}%  pf={_pf_str(s['pf'])}  exp_R={s['exp']:+.3f}")
    ci = bootstrap_ci(eurusd_trades)
    if ci is not None:
        ex_lo, ex_hi = ci["exp"]
        print(f"  bootstrap CI 95% exp_R: [{ex_lo:+.3f}, {ex_hi:+.3f}]")
    print("  H7 régimes (DXY proxy) on filtered subset:")
    regimes_buckets: dict = defaultdict(list)
    for t in eurusd_trades:
        reg = dxy_regime(t, h1_eur, PIP["EURUSD"])
        if reg is not None:
            regimes_buckets[reg].append(t)
    regime_stats = {}
    for reg in ("EUR_up_DXY_down", "ranging", "EUR_down_DXY_up"):
        bs = stats_block(regimes_buckets.get(reg, []))
        regime_stats[reg] = bs
        print(f"    {reg:<18}: n={bs['n']:3d} exp={bs['exp']:+.3f}R wr={bs['wr']:5.1f}%")
    # verdict
    if n < 20:
        v = "INSUFFICIENT_N"
    elif ci is not None and s["exp"] >= 0.10 and ci["exp"][0] > 0:
        v = "REPAIRED"
    else:
        v = "STILL_BROKEN"
    print(f"  verdict: {v}")
    return {"n": n, "exp": s["exp"], "ci": ci, "regimes": regime_stats, "verdict": v}


def write_equity_v3(results: dict, out_path: Path) -> None:
    """3 panels (one per pair), 3 superposed lines (one per config)."""
    fig = make_subplots(rows=3, cols=1, subplot_titles=PAIRS, shared_xaxes=False,
                        vertical_spacing=0.08)
    COLOR = {"v2_relax_all": "#888888", "v3_h3_strict": "#2e7d32", "v3_h3_loose": "#1976d2"}
    for row, p in enumerate(PAIRS, start=1):
        for cfg_name in CONFIGS:
            trs = results[cfg_name]["trades_by_pair"][p]
            if not trs:
                continue
            cum = []
            c = 0.0
            for t in trs:
                c += t["r_realized"]
                cum.append(c)
            fig.add_trace(go.Scatter(
                y=cum, mode="lines",
                name=f"{cfg_name} ({len(trs)})",
                line=dict(color=COLOR.get(cfg_name, "#000000")),
                legendgroup=cfg_name,
                showlegend=(row == 1),
            ), row=row, col=1)
        fig.update_yaxes(title_text="cum R", row=row, col=1)
    fig.update_xaxes(title_text="trade #", row=3, col=1)
    fig.update_layout(
        title="Asia London Sweep v3 — H3 filter comparison (cum R per pair, 3 configs superposed)",
        template="plotly_white",
        height=1100,
        hovermode="x",
    )
    fig.write_html(str(out_path), include_plotlyjs="cdn")


# =====================================================================
# V4 (OOS) reporting helpers
# =====================================================================
def print_oos_per_pair_table(trades_by_pair: dict, is_set: set, oos_set: set) -> None:
    print("\n=== PER-PAIR (IS vs OOS), config=v3_h3_strict ===")
    print("| pair    | sample | n  | wr%   | pf    | exp_R   | maxDD  | total_R |")
    print("|---------|--------|----|-------|-------|---------|--------|---------|")
    ordered = [p for p in trades_by_pair if p in is_set] + [p for p in trades_by_pair if p in oos_set]
    for p in ordered:
        s = stats_block(trades_by_pair[p])
        tag = "IS" if p in is_set else "OOS"
        total = s["n"] * s["exp"]
        print(f"| {p:<7} | {tag:<6} | {s['n']:>3} | {s['wr']:>5.1f} | "
              f"{_pf_str(s['pf']):>5} | {s['exp']:+.3f} | {s['max_dd']:>+6.1f} | {total:+7.2f} |")


def print_is_vs_oos_aggregate(trades_by_pair: dict, is_pairs: list, oos_pairs: list) -> tuple[dict, dict]:
    is_t: list[dict] = []
    oos_t: list[dict] = []
    for p in is_pairs:
        is_t.extend(trades_by_pair.get(p, []))
    for p in oos_pairs:
        oos_t.extend(trades_by_pair.get(p, []))
    is_s = stats_block(is_t)
    oos_s = stats_block(oos_t)
    print("\n=== AGGREGATE IS vs OOS ===")
    print("| group | n  | wr%   | pf    | exp_R   | total_R |")
    print("|-------|----|-------|-------|---------|---------|")
    for tag, s in (("IS", is_s), ("OOS", oos_s)):
        print(f"| {tag:<5} | {s['n']:>3} | {s['wr']:>5.1f} | "
              f"{_pf_str(s['pf']):>5} | {s['exp']:+.3f} | {s['n'] * s['exp']:+7.2f} |")
    # ratios
    if is_s["exp"] != 0:
        ratio_exp = oos_s["exp"] / is_s["exp"]
        print(f"  ratio OOS/IS exp_R: {ratio_exp:+.2f}x")
    else:
        ratio_exp = 0.0
        print("  ratio OOS/IS exp_R: undefined (IS exp_R = 0)")
    if is_s["pf"] not in (0.0, float("inf")) and oos_s["pf"] not in (0.0, float("inf")):
        ratio_pf = oos_s["pf"] / is_s["pf"]
        print(f"  ratio OOS/IS pf   : {ratio_pf:+.2f}x")
    else:
        ratio_pf = 0.0
        print("  ratio OOS/IS pf   : undefined")
    is_s["_trades"] = is_t
    oos_s["_trades"] = oos_t
    is_s["_ratio_exp"] = None
    oos_s["_ratio_exp"] = ratio_exp
    oos_s["_ratio_pf"] = ratio_pf
    return is_s, oos_s


def print_bootstrap_oos(oos_trades: list[dict]) -> dict | None:
    print("\n=== BOOTSTRAP 95% CI — OOS aggregate (n_resample=1000) ===")
    ci = bootstrap_ci(oos_trades)
    if ci is None:
        print("  (empty)")
        return None
    pf_lo, pf_hi = ci["pf"]
    ex_lo, ex_hi = ci["exp"]
    wr_lo, wr_hi = ci["wr"]
    print(f"  PF   : [{pf_lo:.2f}, {pf_hi:.2f}]"
          + ("  (upper bound sentinel 999)" if pf_hi >= 999 else ""))
    print(f"  exp_R: [{ex_lo:+.3f}, {ex_hi:+.3f}]")
    print(f"  wr%  : [{wr_lo:.1f}, {wr_hi:.1f}]")
    return ci


def print_oos_funnels(funnel_by_pair: dict, oos_pairs: list) -> None:
    funnel_keys = ["days_total", "asia_qualified", "sweeps", "h1_ok",
                   "bos_m15_ok", "fvg_ok", "fvg_ratio_ok", "rr_ok", "filled", "closed"]
    print("\n=== PER-PAIR OOS FUNNELS ===")
    hdr_pairs = " | ".join(oos_pairs)
    print(f"| stage | {hdr_pairs} |")
    print("|---|" + "|".join("---" for _ in oos_pairs) + "|")
    for k in funnel_keys:
        row = [f"{funnel_by_pair[p].get(k, 0)}" for p in oos_pairs]
        print(f"| {k} | " + " | ".join(row) + " |")


def print_oos_per_pair_decomp(trades_by_pair: dict, oos_pairs: list) -> None:
    for p in oos_pairs:
        trs = trades_by_pair.get(p, [])
        print(f"\n### OOS decomp — {p} (n={len(trs)})")
        if not trs:
            print("  (no trades)")
            continue
        # sweep hour
        print("  sweep hour:")
        g_hr: dict = defaultdict(list)
        for t in trs:
            g_hr[pd.to_datetime(t["sweep_time"]).hour].append(t)
        for h in sorted(g_hr):
            s = stats_block(g_hr[h])
            print(f"    h={h}: n={s['n']} wr={s['wr']:.1f}% pf={_pf_str(s['pf'])} exp={s['exp']:+.3f}")
        # asia range tertiles
        if len(trs) >= 3:
            sorted_r = sorted(t["asia_range_pips"] for t in trs)
            b1 = sorted_r[len(sorted_r) // 3]
            b2 = sorted_r[2 * len(sorted_r) // 3]
            print(f"  asia range tertiles (b1={b1:.0f}, b2={b2:.0f}):")
            g_ar: dict = defaultdict(list)
            for t in trs:
                v = t["asia_range_pips"]
                k = "low" if v < b1 else ("mid" if v < b2 else "hi")
                g_ar[k].append(t)
            for k in ("low", "mid", "hi"):
                s = stats_block(g_ar[k])
                print(f"    {k}: n={s['n']} wr={s['wr']:.1f}% pf={_pf_str(s['pf'])} exp={s['exp']:+.3f}")
        # RR bucket
        print("  RR bucket:")
        g_rr: dict = defaultdict(list)
        for t in trs:
            rr = t["rr_planned"]
            k = "<2" if rr < 2 else ("2-3" if rr < 3 else "3+")
            g_rr[k].append(t)
        for k in ("<2", "2-3", "3+"):
            s = stats_block(g_rr[k])
            print(f"    {k}: n={s['n']} wr={s['wr']:.1f}% pf={_pf_str(s['pf'])} exp={s['exp']:+.3f}")
        # direction
        print("  direction:")
        g_dir: dict = defaultdict(list)
        for t in trs:
            g_dir[t["direction"]].append(t)
        for k in ("short", "long"):
            s = stats_block(g_dir[k])
            print(f"    {k}: n={s['n']} wr={s['wr']:.1f}% pf={_pf_str(s['pf'])} exp={s['exp']:+.3f}")


def write_equity_oos(trades_by_pair: dict, ordered_pairs: list, out_path: Path) -> None:
    """6 panels (one per pair) cum R. IS pairs colored gray, OOS colored green."""
    fig = make_subplots(rows=6, cols=1, subplot_titles=ordered_pairs, shared_xaxes=False,
                        vertical_spacing=0.04)
    for row, p in enumerate(ordered_pairs, start=1):
        trs = trades_by_pair.get(p, [])
        if not trs:
            continue
        cum = []
        c = 0.0
        for t in trs:
            c += t["r_realized"]
            cum.append(c)
        color = "#2e7d32" if any(t["is_oos"] for t in trs) else "#888888"
        fig.add_trace(go.Scatter(
            y=cum, mode="lines", line=dict(color=color),
            name=f"{p} (n={len(trs)})",
        ), row=row, col=1)
        fig.update_yaxes(title_text="cum R", row=row, col=1)
    fig.update_xaxes(title_text="trade #", row=6, col=1)
    fig.update_layout(
        title="Asia London Sweep v4 OOS — cum R per pair (IS gray, OOS green)",
        template="plotly_white",
        height=1800,
        showlegend=False,
    )
    fig.write_html(str(out_path), include_plotlyjs="cdn")


def oos_pair_verdict(stats_pair: dict) -> str:
    if stats_pair["n"] < 15:
        return "INCONCLUSIVE"
    if stats_pair["exp"] > 0 and stats_pair["pf"] > 1.0:
        return "GENERALIZE"
    return "FAIL"


def final_oos_verdict(oos_exp: float, is_exp: float, pair_verdicts: dict) -> str:
    """Per spec thresholds:
       - OOS exp_R >= 0.5x IS exp_R AND >=2/3 pairs GENERALIZE -> STRATEGY_VALIDATED
       - OOS exp_R in [0.2x, 0.5x) IS                          -> PARTIAL_GENERALIZATION
       - OOS exp_R < 0.2x IS OR 0/3 GENERALIZE                 -> OVERFIT_CONFIRMED
    """
    gen_count = sum(1 for v in pair_verdicts.values() if v == "GENERALIZE")
    if is_exp <= 0:
        # IS not profitable: rare; degenerate case
        return "PARTIAL_GENERALIZATION" if oos_exp > 0 else "OVERFIT_CONFIRMED"
    ratio = oos_exp / is_exp
    if ratio >= 0.5 and gen_count >= 2:
        return "STRATEGY_VALIDATED"
    if 0.2 <= ratio < 0.5:
        return "PARTIAL_GENERALIZATION"
    if ratio < 0.2 or gen_count == 0:
        return "OVERFIT_CONFIRMED"
    return "PARTIAL_GENERALIZATION"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["single", "grid", "oos"], default="grid")
    p.add_argument("--rr_floor", type=float, default=1.2)
    p.add_argument("--fvg_window", type=int, default=10)
    p.add_argument("--bos_window", type=int, default=16)
    p.add_argument("--fvg_ratio_cap", type=float, default=0.10)
    return p.parse_args()


def main():
    args = parse_args()
    t0 = time.time()
    print("Loading data...")
    m5_data, m15_data, h1_data = {}, {}, {}
    for p in PAIRS:
        m5_data[p] = load(DATA_DIR / FILES[p]["M5"])
        m15_data[p] = load(DATA_DIR / FILES[p]["M15"])
        h1_data[p] = load(DATA_DIR / FILES[p]["H1"])
        print(f"  {p}: M5={len(m5_data[p])} M15={len(m15_data[p])} H1={len(h1_data[p])}")
    print(f"Load done in {time.time() - t0:.1f}s")

    if args.mode == "single":
        cfg = {
            "rr_floor": args.rr_floor,
            "fvg_window": args.fvg_window,
            "bos_window": args.bos_window,
            "fvg_ratio_cap": args.fvg_ratio_cap,
        }
        cfg_name = "custom"
        print(f"\n=== SINGLE config: {cfg_name} ({cfg}) ===")
        tbp, fbp = run_one(cfg_name, cfg, m5_data, m15_data, h1_data)
        all_t: list[dict] = []
        for p in PAIRS:
            all_t.extend(tbp[p])
        with open(out_trades_v3(cfg_name), "w") as f:
            json.dump(all_t, f, indent=2, default=str)
        agg = aggregate_global(tbp, fbp)
        print("\ndone")
        print(f"files: {out_trades_v3(cfg_name)}")
        print(f"global: trades={agg['n']} wr={agg['wr']:.1f}% pf={_pf_str(agg['pf'])} "
              f"exp={agg['exp']:+.3f}R maxDD={agg['max_dd']:.1f}R")
        print(f"\nTotal runtime: {time.time() - t0:.1f}s")
        return

    if args.mode == "oos":
        # Load OOS pair frames (resample M5->M15/H1 since absent)
        present_oos: list[str] = []
        for p in OOS_PAIRS:
            m5_path = DATA_DIR / FILES[p]["M5"]
            if not m5_path.exists():
                print(f"  [SKIP] {p}: M5 CSV missing at {m5_path}")
                continue
            present_oos.append(p)
            t_l = time.time()
            m5x, m15x, h1x = load_pair_frames(p)
            m5_data[p] = m5x
            m15_data[p] = m15x
            h1_data[p] = h1x
            print(f"  {p}: M5={len(m5x)} M15={len(m15x)} (resampled) H1={len(h1x)} (resampled) "
                  f"[{time.time() - t_l:.1f}s]")
        all_pairs = PAIRS + present_oos
        if not present_oos:
            print("\nNo OOS pairs available — nothing to test.")
            return

        # Run v3_h3_strict on all 6 pairs
        cfg = OOS_CFG
        cfg_name = "v4_oos_v3_h3_strict"
        print(f"\n=== OOS run: {cfg_name} ({cfg}) over pairs {all_pairs} ===")
        trades_by_pair: dict = {}
        funnel_by_pair: dict = {}
        for p in all_pairs:
            print(f"  --- {p} ---")
            trs, fn = backtest_pair(
                p, m5_data[p], m15_data[p], h1_data[p],
                rr_floor=cfg["rr_floor"],
                fvg_window=cfg["fvg_window"],
                bos_window=cfg["bos_window"],
                fvg_ratio_cap=cfg["fvg_ratio_cap"],
            )
            is_oos = p in OOS_PAIRS
            for t in trs:
                t["is_oos"] = is_oos
            trades_by_pair[p] = trs
            funnel_by_pair[p] = fn
            print(f"  [{p}] n={len(trs)} (is_oos={is_oos}) "
                  f"fvg_ok={fn.get('fvg_ok',0)} fvg_ratio_ok={fn.get('fvg_ratio_ok',0)} "
                  f"rr_ok={fn.get('rr_ok',0)} filled={fn.get('filled',0)}")

        # Persist all trades
        all_trades: list[dict] = []
        for p in all_pairs:
            all_trades.extend(trades_by_pair[p])
        with open(OUT_TRADES_V4, "w") as f:
            json.dump(all_trades, f, indent=2, default=str)

        # Equity HTML (6 panels)
        write_equity_oos(trades_by_pair, all_pairs, OUT_EQUITY_V4)

        # Samples HTML: OOS trades only
        oos_only = [t for t in all_trades if t["is_oos"]]
        write_samples(oos_only, m5_data, OUT_SAMPLES_V4)

        # ----- Reporting -----
        is_set = set(PAIRS)
        oos_set = set(present_oos)
        print_oos_per_pair_table(trades_by_pair, is_set, oos_set)
        is_agg, oos_agg = print_is_vs_oos_aggregate(trades_by_pair, PAIRS, present_oos)
        oos_ci = print_bootstrap_oos(oos_agg["_trades"])
        print_oos_funnels(funnel_by_pair, present_oos)
        print_oos_per_pair_decomp(trades_by_pair, present_oos)

        # Verdicts
        pair_verdicts: dict = {}
        for p in present_oos:
            s = stats_block(trades_by_pair[p])
            pair_verdicts[p] = oos_pair_verdict(s)

        print("\n## OOS VERDICT")
        for p in present_oos:
            s = stats_block(trades_by_pair[p])
            print(f"  {p}: n={s['n']} exp={s['exp']:+.3f}R pf={_pf_str(s['pf'])} verdict={pair_verdicts[p]}")
        ratio_exp = oos_agg.get("_ratio_exp", 0.0) or 0.0
        print(f"\n  aggregate OOS: n={oos_agg['n']} exp_R={oos_agg['exp']:+.3f} pf={_pf_str(oos_agg['pf'])}")
        print(f"  vs IS exp_R: IS={is_agg['exp']:+.3f}, OOS/IS ratio={ratio_exp:+.2f}x")

        final_v = final_oos_verdict(oos_agg["exp"], is_agg["exp"], pair_verdicts)
        print(f"\n  FINAL VERDICT: {final_v}")

        # Next action
        if final_v == "STRATEGY_VALIDATED":
            next_action = "advance to live paper-trading prep on top-3 pairs (highest OOS exp_R)"
        elif final_v == "PARTIAL_GENERALIZATION":
            next_action = "signal real but weak ; investigate pair-conditional filters before live"
        else:
            next_action = "abandon current spec ; rework feature set or accept setup is regime/pair specific"

        # ----- Deliverable block -----
        print("\n\ndone")
        print("files:")
        print(f"  [trades] {OUT_TRADES_V4}")
        print(f"  [equity 6 panels] {OUT_EQUITY_V4}")
        print(f"  [samples OOS] {OUT_SAMPLES_V4}")
        print("oos_summary:")
        for p in present_oos:
            s = stats_block(trades_by_pair[p])
            print(f"  {p}: n={s['n']} exp={s['exp']:+.3f} verdict={pair_verdicts[p]}")
        print(f"aggregate_oos: n={oos_agg['n']} exp={oos_agg['exp']:+.3f} pf={_pf_str(oos_agg['pf'])}")
        print(f"is_vs_oos_ratio_exp: {ratio_exp:+.2f}x")
        if oos_ci is not None:
            print(f"  (OOS bootstrap CI 95% exp_R: [{oos_ci['exp'][0]:+.3f}, {oos_ci['exp'][1]:+.3f}])")
        print(f"final_verdict: {final_v}")
        print(f"next_action: {next_action}")
        print(f"\nTotal runtime: {time.time() - t0:.1f}s")
        return

    # ----- GRID v3 -----
    results: dict = {}
    for cfg_name, cfg in CONFIGS.items():
        print(f"\n=== Running config: {cfg_name} ({cfg}) ===")
        tbp, fbp = run_one(cfg_name, cfg, m5_data, m15_data, h1_data)
        all_t: list[dict] = []
        for p in PAIRS:
            all_t.extend(tbp[p])
        with open(out_trades_v3(cfg_name), "w") as f:
            json.dump(all_t, f, indent=2, default=str)
        agg = aggregate_global(tbp, fbp)
        results[cfg_name] = {
            "config": cfg,
            "trades_by_pair": tbp,
            "funnel_by_pair": fbp,
            "agg": agg,
        }
        print(f"  -> n={agg['n']} pf={_pf_str(agg['pf'])} exp={agg['exp']:+.3f}R "
              f"total_R={agg['n'] * agg['exp']:+.2f}")

    # Equity HTML (single file, 3 panels)
    write_equity_v3(results, OUT_EQUITY_V3)

    # Samples HTML: focus config only
    focus_trades: list[dict] = []
    for p in PAIRS:
        focus_trades.extend(results[FOCUS_CFG]["trades_by_pair"][p])
    write_samples(focus_trades, m5_data, OUT_SAMPLES_V3)

    # ----- Reporting -----
    print_comparative_global(results)
    print_comparative_per_pair(results, base_cfg_name="v2_relax_all")
    print_funnel(results[FOCUS_CFG]["funnel_by_pair"], FOCUS_CFG)
    print_focus_decomps(results[FOCUS_CFG]["trades_by_pair"], FOCUS_CFG)

    # EURUSD post-filter diagnostic
    eurusd_focus = results[FOCUS_CFG]["trades_by_pair"]["EURUSD"]
    eur_diag = eurusd_post_filter_diagnostic(eurusd_focus, h1_data["EURUSD"])

    # Global robustness on focus config
    print_robustness(results[FOCUS_CFG]["trades_by_pair"], FOCUS_CFG)

    # ----- H3 effect summary + recommendation -----
    base_a = results["v2_relax_all"]["agg"]
    focus_a = results[FOCUS_CFG]["agg"]
    d_n = focus_a["n"] - base_a["n"]
    d_exp = focus_a["exp"] - base_a["exp"]
    d_pf = focus_a["pf"] - base_a["pf"] if base_a["pf"] != float("inf") and focus_a["pf"] != float("inf") else None
    d_total = focus_a["n"] * focus_a["exp"] - base_a["n"] * base_a["exp"]

    per_pair_delta = {}
    for p in PAIRS:
        bs = stats_block(results["v2_relax_all"]["trades_by_pair"][p])
        fs = stats_block(results[FOCUS_CFG]["trades_by_pair"][p])
        per_pair_delta[p] = {
            "d_n": fs["n"] - bs["n"],
            "d_exp": fs["exp"] - bs["exp"],
            "n_focus": fs["n"],
            "exp_focus": fs["exp"],
        }

    eu_verdict = eur_diag["verdict"]
    # Global recommendation logic
    if eu_verdict == "REPAIRED" and d_total > 0:
        reco = "KEEP_H3"
    elif eu_verdict == "STILL_BROKEN" and d_total > 0:
        reco = "KEEP_H3_PLUS_H7_NEEDED"
    elif eu_verdict == "STILL_BROKEN" and d_total <= 0:
        reco = "DROP_H3"
    elif eu_verdict == "INSUFFICIENT_N":
        reco = "KEEP_H3_PLUS_H7_NEEDED"
    else:
        reco = "KEEP_H3"

    # ----- Final deliverable block -----
    print("\n\ndone")
    print("files:")
    for name in CONFIGS:
        print(f"  [{name}] {out_trades_v3(name)}")
    print(f"  [equity] {OUT_EQUITY_V3}")
    print(f"  [samples {FOCUS_CFG}] {OUT_SAMPLES_V3}")
    print("h3_effect:")
    dpf_s = f"{d_pf:+.2f}" if d_pf is not None else "n/a"
    print(f"  global: Δn={d_n:+d}, Δexp_R={d_exp:+.3f}, Δpf={dpf_s}, Δtotal_R={d_total:+.2f}")
    print(f"  EURUSD: Δn={per_pair_delta['EURUSD']['d_n']:+d}, "
          f"Δexp_R={per_pair_delta['EURUSD']['d_exp']:+.3f}, "
          f"verdict={eu_verdict}")
    for p in ("GBPUSD", "USDJPY"):
        print(f"  {p}: Δn={per_pair_delta[p]['d_n']:+d}, Δexp_R={per_pair_delta[p]['d_exp']:+.3f}")
    print(f"recommendation: {reco}")
    print(f"\nTotal runtime: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
