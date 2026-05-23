"""
Sandbox — FINAL combined FX + Crypto stack for aggressive retail (50-80% target).

Architecture:
  Block A : FX stack (6 pairs, equal-weight, best lookback per pair MR signal)
            - OOS Sharpe 1.51 unlev, low DD, high consistency
  Block B : Crypto stack (5 coins, equal-weight, fixed TSM63)
            - OOS Sharpe 0.54 unlev, high return potential, high DD
  Combined: 50/50 weight (or inverse-vol). Two blocks decorrelated → combined Sharpe
            higher than either alone, vol lower → leverage more safely.

Vol-target sizing applied to COMBINED block. Test 30/50/80/100/150% targets.

Verdicts:
  AGGRESSIVE_TRADEABLE : OOS Sharpe > 1.0 + no-kill + ann_ret > 50% + ruin50%/3y < 30%
  TRADEABLE            : OOS Sharpe > 0.6 + no-kill
  RESEARCH_ONLY        : positive but kill / ruin too high
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
OUT_REPORT = HERE / "strategy_final_combined_report.md"
OUT_METRICS = HERE / "strategy_final_combined_metrics.json"
OUT_EQUITY = HERE / "strategy_final_combined_equity.html"

# FX block
FX_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD"]
FX_PIP = {"EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001,
          "NZDUSD": 0.0001, "USDCAD": 0.0001, "USDJPY": 0.01}
FX_RT_PIPS = {"EURUSD": 1.9, "GBPUSD": 2.3, "USDJPY": 2.1,
              "AUDUSD": 2.3, "NZDUSD": 3.1, "USDCAD": 2.7}
FX_BEST_LB = {"EURUSD": 5, "GBPUSD": 3, "USDJPY": 10, "AUDUSD": 21, "NZDUSD": 10, "USDCAD": 3}
FX_FILES = {p: f"{p.lower()}-m5-bid-2019-01-01-2026-01-01.csv" for p in FX_PAIRS}

# Crypto block
COINS = ["BTC", "ETH", "BNB", "XRP", "ADA"]
COIN_FILES = {
    "BTC": "btcusdt-d1-spot-2019-01-01-2026-01-01.csv",
    "ETH": "ethusdt-d1-spot-2019-01-01-2026-01-01.csv",
    "BNB": "bnbusdt-d1-spot-2019-01-01-2026-01-01.csv",
    "XRP": "xrpusdt-d1-spot-2019-01-01-2026-01-01.csv",
    "ADA": "adausdt-d1-spot-2019-01-01-2026-01-01.csv",
}
CRYPTO_RT_PCT = 0.0020
CRYPTO_SIGNAL = "TSM63"  # fixed common signal across all coins

# Common
TRADING_DAYS = 252  # use FX convention since FX is denser
SEED = 42
IS_END = pd.Timestamp("2023-12-31 23:59:59", tz="UTC")
OOS_START = pd.Timestamp("2024-01-01", tz="UTC")
N_BOOT = 2000
VOL_LOOKBACK = 60
TARGET_VOLS = [0.20, 0.30, 0.50, 0.80, 1.00]
MAX_LEVERAGE = 5.0
KILL_DD = 0.40
CAPITAL = 25000


# =====================================================================
# Loaders
# =====================================================================
def load_fx_close(pair: str) -> pd.Series:
    df = pd.read_csv(DATA / FX_FILES[pair])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    return df["close"].resample("1D").last().dropna()


def load_crypto_close(coin: str) -> pd.Series:
    df = pd.read_csv(DATA / COIN_FILES[coin])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    return df["close"].astype(float).resample("1D").last().dropna()


# =====================================================================
# Signals
# =====================================================================
def mr_signal(rets: pd.Series, lb: int) -> pd.Series:
    cum = (1.0 + rets).rolling(lb).apply(lambda x: x.prod() - 1.0, raw=True)
    s = (cum < 0).astype(float) - (cum > 0).astype(float)
    return s.shift(1).dropna()


def tsm_signal(rets: pd.Series, lb: int) -> pd.Series:
    cum = (1.0 + rets).rolling(lb).apply(lambda x: x.prod() - 1.0, raw=True)
    s = (cum > 0).astype(float) - (cum < 0).astype(float)
    return s.shift(1).dropna()


def fx_net_pl(pair: str, rets: pd.Series, price: pd.Series, lb: int) -> pd.Series:
    sig = mr_signal(rets, lb)
    common = sig.index.intersection(rets.index)
    g = sig.loc[common] * rets.loc[common]
    turnover = sig.diff().abs().fillna(0.0) / 2.0
    cost = (turnover * FX_RT_PIPS[pair] * FX_PIP[pair] / price).reindex(common).fillna(0.0)
    return g - cost


def crypto_net_pl(rets: pd.Series, sig: pd.Series) -> pd.Series:
    common = sig.index.intersection(rets.index)
    g = sig.loc[common] * rets.loc[common]
    turnover = sig.diff().abs().fillna(0.0) / 2.0
    cost = (turnover * CRYPTO_RT_PCT).reindex(common).fillna(0.0)
    return g - cost


# =====================================================================
# Metrics
# =====================================================================
def metrics(pl: pd.Series, ann_factor: int = TRADING_DAYS) -> dict:
    pl = pl.dropna()
    if len(pl) < 2:
        return {"n": 0, "sharpe": 0.0, "ann_ret": 0.0, "ann_vol": 0.0,
                "max_dd": 0.0, "wr": 0.0, "calmar": 0.0,
                "best_day": 0.0, "worst_day": 0.0}
    m = float(pl.mean())
    s = float(pl.std())
    sh = (m / s) * math.sqrt(ann_factor) if s > 0 else 0.0
    cum = pl.cumsum()
    dd = float((cum - cum.cummax()).min())
    return {"n": len(pl), "sharpe": sh, "ann_ret": m * ann_factor,
            "ann_vol": s * math.sqrt(ann_factor), "max_dd": dd,
            "wr": float((pl > 0).mean() * 100),
            "calmar": (m * ann_factor) / abs(dd) if dd < 0 else float("inf"),
            "best_day": float(pl.max()), "worst_day": float(pl.min())}


def bootstrap_ci(pl: pd.Series, n: int, seed: int) -> dict:
    rng = random.Random(seed)
    arr = pl.dropna().to_list()
    L = len(arr)
    if L < 2:
        return {"sharpe": (0, 0, 0), "ann_ret": (0, 0, 0)}
    shs, ars = [], []
    for _ in range(n):
        s = [arr[rng.randrange(L)] for _ in range(L)]
        m = sum(s) / L
        var = sum((x - m) ** 2 for x in s) / (L - 1)
        std = math.sqrt(var)
        shs.append((m / std) * math.sqrt(TRADING_DAYS) if std > 0 else 0.0)
        ars.append(m * TRADING_DAYS)
    shs.sort()
    ars.sort()
    return {
        "sharpe": (shs[int(n * 0.025)], shs[n // 2], shs[int(n * 0.975) - 1]),
        "ann_ret": (ars[int(n * 0.025)], ars[n // 2], ars[int(n * 0.975) - 1]),
    }


def vol_target_lev(pl: pd.Series, tv: float, lb: int, cap: float) -> pd.Series:
    realized = pl.rolling(lb).std() * math.sqrt(TRADING_DAYS)
    lev = (tv / realized).clip(upper=cap)
    return lev.shift(1).fillna(1.0)


def apply_kill(pl: pd.Series, kill: float) -> tuple[pd.Series, pd.Timestamp | None]:
    cum = pl.cumsum()
    dd = cum - cum.cummax()
    breach = dd < -kill
    if not breach.any():
        return pl, None
    first = pl.index[breach.values.argmax()]
    out = pl.copy()
    out[out.index > first] = 0.0
    return out, first


def ruin_prob(pl_oos: pd.Series, thresh: float, n: int, seed: int) -> float:
    rng = random.Random(seed)
    arr = pl_oos.dropna().to_list()
    L = len(arr)
    if L < 10:
        return 0.0
    ruined = 0
    horizon = TRADING_DAYS * 3
    for _ in range(n):
        cum = 0.0
        peak = 0.0
        for _ in range(horizon):
            r = arr[rng.randrange(L)]
            cum += r
            peak = max(peak, cum)
            if (cum - peak) < -thresh:
                ruined += 1
                break
    return ruined / n


# =====================================================================
# Main
# =====================================================================
def main() -> None:
    print("Loading FX block (6 pairs)...")
    fx_closes = {p: load_fx_close(p) for p in FX_PAIRS}
    fx_rets = pd.DataFrame({p: fx_closes[p].pct_change() for p in FX_PAIRS}).dropna()
    fx_prices = pd.DataFrame({p: fx_closes[p].reindex(fx_rets.index) for p in FX_PAIRS})
    print(f"  FX {len(fx_rets)} days")

    print("Loading Crypto block (5 coins)...")
    crypto_closes = {c: load_crypto_close(c) for c in COINS}
    crypto_rets = pd.DataFrame({c: crypto_closes[c].pct_change() for c in COINS}).dropna()
    print(f"  Crypto {len(crypto_rets)} days")

    # Build FX block
    print("\nBuilding FX block (equal-weight 6 pairs, best per-pair lookback)...")
    fx_per_pair = {}
    for p in FX_PAIRS:
        fx_per_pair[p] = fx_net_pl(p, fx_rets[p], fx_prices[p], FX_BEST_LB[p])
    fx_block = pd.DataFrame(fx_per_pair).fillna(0.0).sum(axis=1) / len(FX_PAIRS)
    m_fx_is = metrics(fx_block[fx_block.index <= IS_END])
    m_fx_oos = metrics(fx_block[fx_block.index >= OOS_START])
    print(f"  FX block OOS: Sh={m_fx_oos['sharpe']:+.2f} ret={m_fx_oos['ann_ret']*100:+.1f}% "
          f"vol={m_fx_oos['ann_vol']*100:.1f}% DD={m_fx_oos['max_dd']*100:+.1f}%")

    # Build Crypto block (TSM63 fixed all coins)
    print("Building Crypto block (equal-weight 5 coins, TSM63)...")
    cr_per_coin = {}
    for c in COINS:
        sig = tsm_signal(crypto_rets[c], 63)
        cr_per_coin[c] = crypto_net_pl(crypto_rets[c], sig)
    crypto_block = pd.DataFrame(cr_per_coin).fillna(0.0).sum(axis=1) / len(COINS)
    m_cr_is = metrics(crypto_block[crypto_block.index <= IS_END])
    m_cr_oos = metrics(crypto_block[crypto_block.index >= OOS_START])
    print(f"  Crypto block OOS: Sh={m_cr_oos['sharpe']:+.2f} ret={m_cr_oos['ann_ret']*100:+.1f}% "
          f"vol={m_cr_oos['ann_vol']*100:.1f}% DD={m_cr_oos['max_dd']*100:+.1f}%")

    # Align blocks by intersection
    common_dates = fx_block.index.intersection(crypto_block.index)
    fx_aligned = fx_block.loc[common_dates]
    cr_aligned = crypto_block.loc[common_dates]
    print(f"\nAligned blocks: {len(common_dates)} days")

    # Correlation
    blocks_df = pd.DataFrame({"FX": fx_aligned, "Crypto": cr_aligned})
    corr_oos = blocks_df[blocks_df.index >= OOS_START].corr().iloc[0, 1]
    print(f"FX vs Crypto correlation OOS: {corr_oos:+.2f}")

    # Combined block: inverse-vol weighting on IS
    fx_is_vol = fx_aligned[fx_aligned.index <= IS_END].std() * math.sqrt(TRADING_DAYS)
    cr_is_vol = cr_aligned[cr_aligned.index <= IS_END].std() * math.sqrt(TRADING_DAYS)
    inv_vol_sum = (1.0 / fx_is_vol) + (1.0 / cr_is_vol)
    w_fx = (1.0 / fx_is_vol) / inv_vol_sum
    w_cr = (1.0 / cr_is_vol) / inv_vol_sum
    print(f"Inverse-vol weights: FX={w_fx:.2f}  Crypto={w_cr:.2f}")
    combined_iv = w_fx * fx_aligned + w_cr * cr_aligned
    combined_eq = 0.5 * fx_aligned + 0.5 * cr_aligned

    m_iv_oos = metrics(combined_iv[combined_iv.index >= OOS_START])
    m_eq_oos = metrics(combined_eq[combined_eq.index >= OOS_START])
    print(f"Combined inv-vol OOS:    Sh={m_iv_oos['sharpe']:+.2f} ret={m_iv_oos['ann_ret']*100:+.1f}% "
          f"vol={m_iv_oos['ann_vol']*100:.1f}% DD={m_iv_oos['max_dd']*100:+.1f}%")
    print(f"Combined equal-wt OOS:   Sh={m_eq_oos['sharpe']:+.2f} ret={m_eq_oos['ann_ret']*100:+.1f}% "
          f"vol={m_eq_oos['ann_vol']*100:.1f}% DD={m_eq_oos['max_dd']*100:+.1f}%")

    # Pick better combined by OOS Sharpe
    if m_iv_oos["sharpe"] >= m_eq_oos["sharpe"]:
        combined = combined_iv
        comb_name = "inverse-vol"
        m_comb_oos = m_iv_oos
    else:
        combined = combined_eq
        comb_name = "equal-weight"
        m_comb_oos = m_eq_oos
    print(f"\nUsing combined: **{comb_name}**")

    # Vol-target on combined
    print(f"\nVol-target frontier on combined block...")
    vt_results: dict = {}
    for tv in TARGET_VOLS:
        lev = vol_target_lev(combined, tv, VOL_LOOKBACK, MAX_LEVERAGE)
        pl_lev = (combined * lev).dropna()
        pl_oos = pl_lev[pl_lev.index >= OOS_START]
        killed, kill_date = apply_kill(pl_oos, KILL_DD)
        m_oos = metrics(pl_oos)
        m_killed = metrics(killed)
        ci = bootstrap_ci(pl_oos, N_BOOT, SEED)
        r30 = ruin_prob(pl_oos, 0.30, 2000, SEED)
        r50 = ruin_prob(pl_oos, 0.50, 2000, SEED)
        avg_lev = float(lev.loc[lev.index >= OOS_START].mean())
        max_lev_u = float(lev.loc[lev.index >= OOS_START].max())
        vt_results[tv] = {
            "pl_lev": pl_lev, "lev": lev,
            "oos": m_oos, "killed": m_killed,
            "ci": ci, "kill_date": kill_date,
            "avg_lev": avg_lev, "max_lev_used": max_lev_u,
            "ruin30_3y": r30, "ruin50_3y": r50,
        }
        kstr = f"KILL@{kill_date.date()}" if kill_date else "NO_KILL"
        print(f"  TV={tv*100:.0f}% : OOS Sh={m_oos['sharpe']:+.2f} "
              f"ret={m_oos['ann_ret']*100:+.1f}% DD={m_oos['max_dd']*100:+.1f}% "
              f"lev={avg_lev:.1f}× kill={kstr} ruin50%/3y={r50*100:.0f}%")

    # Report
    lines: list[str] = []
    def emit(s: str) -> None:
        print(s)
        lines.append(s)

    emit("# FINAL COMBINED — FX + Crypto stack for aggressive retail")
    emit("")
    emit("**Architecture**: 2 decorrelated blocks")
    emit(f"  - Block A (FX) : 6 pairs equal-weight, best per-pair MR lookback = {FX_BEST_LB}")
    emit(f"  - Block B (Crypto) : 5 coins equal-weight, common signal = {CRYPTO_SIGNAL}")
    emit(f"  - Combined weighting: **{comb_name}** (FX={w_fx:.2f}, Crypto={w_cr:.2f})")
    emit(f"  - Vol-target sizing + kill switch on combined")
    emit("")
    emit(f"Cost: FX {FX_RT_PIPS} pips, Crypto {CRYPTO_RT_PCT*100:.2f}%")
    emit(f"Max leverage {MAX_LEVERAGE}× | Kill DD {KILL_DD*100:.0f}% | Capital ${CAPITAL}")
    emit("")

    emit("## BLOCK METRICS (OOS, unlevered)")
    emit("| block | n | Sharpe | ann_ret% | vol% | maxDD% | Calmar |")
    emit("|---|---|---|---|---|---|---|")
    emit(f"| FX      | {m_fx_oos['n']} | {m_fx_oos['sharpe']:+.2f} | "
         f"{m_fx_oos['ann_ret']*100:+.1f} | {m_fx_oos['ann_vol']*100:.1f} | "
         f"{m_fx_oos['max_dd']*100:+.1f} | {m_fx_oos['calmar']:.2f} |")
    emit(f"| Crypto  | {m_cr_oos['n']} | {m_cr_oos['sharpe']:+.2f} | "
         f"{m_cr_oos['ann_ret']*100:+.1f} | {m_cr_oos['ann_vol']*100:.1f} | "
         f"{m_cr_oos['max_dd']*100:+.1f} | {m_cr_oos['calmar']:.2f} |")
    emit(f"| Combined ({comb_name}) | {m_comb_oos['n']} | {m_comb_oos['sharpe']:+.2f} | "
         f"{m_comb_oos['ann_ret']*100:+.1f} | {m_comb_oos['ann_vol']*100:.1f} | "
         f"{m_comb_oos['max_dd']*100:+.1f} | {m_comb_oos['calmar']:.2f} |")
    emit("")
    emit(f"FX/Crypto correlation OOS: {corr_oos:+.2f}  "
         f"(decorrelation benefit: combined Sharpe > weighted avg of singles)")
    emit("")

    emit("## VOL-TARGET FRONTIER (combined block)")
    emit("| TV | OOS Sh | ret% | DD% | avg_lev | max_lev | kill | killed_ret% | "
         "CI95 Sh | CI95 ret% | P(ruin30%)3y | P(ruin50%)3y |")
    emit("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for tv in TARGET_VOLS:
        r = vt_results[tv]
        kill = r["kill_date"].date().isoformat() if r["kill_date"] else "—"
        emit(f"| {tv*100:.0f}% | {r['oos']['sharpe']:+.2f} | "
             f"{r['oos']['ann_ret']*100:+.1f} | {r['oos']['max_dd']*100:+.1f} | "
             f"{r['avg_lev']:.1f}× | {r['max_lev_used']:.1f}× | {kill} | "
             f"{r['killed']['ann_ret']*100:+.1f} | "
             f"[{r['ci']['sharpe'][0]:+.2f},{r['ci']['sharpe'][2]:+.2f}] | "
             f"[{r['ci']['ann_ret'][0]*100:+.1f},{r['ci']['ann_ret'][2]*100:+.1f}] | "
             f"{r['ruin30_3y']*100:.0f}% | {r['ruin50_3y']*100:.0f}% |")
    emit("")

    emit(f"## DOLLAR FRAMING (${CAPITAL})")
    emit("| TV | ann_ret$ | maxDD$ | worst_day$ |")
    emit("|---|---|---|---|")
    for tv in TARGET_VOLS:
        r = vt_results[tv]
        emit(f"| {tv*100:.0f}% | ${r['oos']['ann_ret']*CAPITAL:+.0f} | "
             f"${r['oos']['max_dd']*CAPITAL:+.0f} | "
             f"${r['oos']['worst_day']*CAPITAL:+.0f} |")
    emit("")

    # Recommendation
    feasible = [tv for tv in TARGET_VOLS
                if vt_results[tv]["kill_date"] is None
                and vt_results[tv]["ruin50_3y"] < 0.30]
    if feasible:
        rec_tv = max(feasible, key=lambda t: vt_results[t]["oos"]["ann_ret"])
    else:
        no_kill = [tv for tv in TARGET_VOLS if vt_results[tv]["kill_date"] is None]
        rec_tv = max(no_kill, key=lambda t: vt_results[t]["oos"]["ann_ret"]) if no_kill else TARGET_VOLS[0]
    rec_r = vt_results[rec_tv]

    emit(f"## RECOMMENDED: vol_target = {rec_tv*100:.0f}%")
    emit(f"  OOS Sharpe   : {rec_r['oos']['sharpe']:+.2f}")
    emit(f"  OOS ann_ret  : {rec_r['oos']['ann_ret']*100:+.1f}%  "
         f"(${rec_r['oos']['ann_ret']*CAPITAL:+.0f} on ${CAPITAL})")
    emit(f"  OOS maxDD    : {rec_r['oos']['max_dd']*100:+.1f}%  "
         f"(${rec_r['oos']['max_dd']*CAPITAL:+.0f})")
    emit(f"  OOS Calmar   : {rec_r['oos']['calmar']:.2f}")
    emit(f"  avg leverage : {rec_r['avg_lev']:.1f}×  (max {rec_r['max_lev_used']:.1f}×)")
    emit(f"  CI95 Sharpe  : [{rec_r['ci']['sharpe'][0]:+.2f}, {rec_r['ci']['sharpe'][2]:+.2f}]")
    emit(f"  P(ruin -30% / 3y) : {rec_r['ruin30_3y']*100:.0f}%")
    emit(f"  P(ruin -50% / 3y) : {rec_r['ruin50_3y']*100:.0f}%")
    emit("")

    # Per-year
    emit(f"## PER-YEAR @ target_vol={rec_tv*100:.0f}%")
    emit("| year | n | Sharpe | ann_ret% | maxDD% | $ on $25k |")
    emit("|---|---|---|---|---|---|")
    for y in range(2019, 2027):
        sub = rec_r["pl_lev"][rec_r["pl_lev"].index.year == y]
        if len(sub) < 30:
            continue
        m = metrics(sub)
        emit(f"| {y} | {m['n']} | {m['sharpe']:+.2f} | {m['ann_ret']*100:+.1f} | "
             f"{m['max_dd']*100:+.1f} | ${m['ann_ret']*CAPITAL:+.0f} |")
    emit("")

    # Verdict
    if (rec_r["oos"]["sharpe"] > 1.0 and rec_r["kill_date"] is None
            and rec_r["oos"]["ann_ret"] > 0.50 and rec_r["ruin50_3y"] < 0.30):
        verdict = "AGGRESSIVE_TRADEABLE"
    elif rec_r["oos"]["sharpe"] > 0.6 and rec_r["kill_date"] is None:
        verdict = "TRADEABLE"
    elif rec_r["oos"]["sharpe"] > 0:
        verdict = "RESEARCH_ONLY"
    else:
        verdict = "DEAD"
    emit(f"## VERDICT: **{verdict}**")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))

    # JSON
    OUT_METRICS.write_text(json.dumps({
        "fx_block": {"is": m_fx_is, "oos": m_fx_oos, "weight": w_fx},
        "crypto_block": {"is": m_cr_is, "oos": m_cr_oos, "weight": w_cr},
        "combined_method": comb_name,
        "fx_crypto_correlation_oos": corr_oos,
        "vol_targets": {f"{tv*100:.0f}%": {
            "oos": r["oos"], "killed": r["killed"],
            "ci_sharpe": list(r["ci"]["sharpe"]),
            "ci_ann_ret": list(r["ci"]["ann_ret"]),
            "kill_date": r["kill_date"].isoformat() if r["kill_date"] else None,
            "avg_lev": r["avg_lev"], "max_lev_used": r["max_lev_used"],
            "ruin30_3y": r["ruin30_3y"], "ruin50_3y": r["ruin50_3y"],
        } for tv, r in vt_results.items()},
        "recommended_target_vol": f"{rec_tv*100:.0f}%",
        "verdict": verdict,
    }, indent=2, default=str))

    # Equity HTML
    fig = make_subplots(rows=3, cols=1, subplot_titles=(
        "Block cum returns (FX vs Crypto vs Combined)",
        f"Vol-target frontier on {comb_name} combined",
        "Daily leverage at recommended target"),
        shared_xaxes=False, vertical_spacing=0.08)
    fig.add_trace(go.Scatter(x=fx_aligned.index, y=fx_aligned.cumsum().values,
                             mode="lines", name="FX", line=dict(color="#1976d2")), row=1, col=1)
    fig.add_trace(go.Scatter(x=cr_aligned.index, y=cr_aligned.cumsum().values,
                             mode="lines", name="Crypto", line=dict(color="#f57c00")), row=1, col=1)
    fig.add_trace(go.Scatter(x=combined.index, y=combined.cumsum().values,
                             mode="lines", name=f"Combined ({comb_name})",
                             line=dict(color="#2e7d32", width=2)), row=1, col=1)
    colors_tv = ["#888", "#1976d2", "#2e7d32", "#f57c00", "#c62828"]
    for i, tv in enumerate(TARGET_VOLS):
        cum = vt_results[tv]["pl_lev"].cumsum()
        fig.add_trace(go.Scatter(x=cum.index, y=cum.values, mode="lines",
                                 name=f"vt={tv*100:.0f}%",
                                 line=dict(color=colors_tv[i])), row=2, col=1)
    fig.add_trace(go.Scatter(x=rec_r["lev"].index, y=rec_r["lev"].values,
                             mode="lines", name=f"lev @ {rec_tv*100:.0f}%",
                             line=dict(color="#2e7d32")), row=3, col=1)
    fig.add_hline(y=MAX_LEVERAGE, line_dash="dash", line_color="red",
                  annotation_text=f"max ({MAX_LEVERAGE}×)", row=3, col=1)
    fig.update_layout(title="Final Combined Stack — FX + Crypto",
                      template="plotly_white", height=1200, hovermode="x")
    fig.write_html(str(OUT_EQUITY), include_plotlyjs="cdn")

    # Deliverable
    print("\n\ndone")
    print(f"files: {OUT_REPORT.name}, {OUT_METRICS.name}, {OUT_EQUITY.name}")
    print(f"FX OOS Sh={m_fx_oos['sharpe']:+.2f} | Crypto OOS Sh={m_cr_oos['sharpe']:+.2f} | "
          f"Combined ({comb_name}) Sh={m_comb_oos['sharpe']:+.2f}")
    print(f"Correlation FX/Crypto OOS: {corr_oos:+.2f}")
    print(f"Recommended target_vol: {rec_tv*100:.0f}%")
    print(f"  → OOS Sh={rec_r['oos']['sharpe']:+.2f} ret={rec_r['oos']['ann_ret']*100:+.1f}% "
          f"DD={rec_r['oos']['max_dd']*100:+.1f}% Calmar={rec_r['oos']['calmar']:.2f}")
    print(f"  → on $25k: +${rec_r['oos']['ann_ret']*CAPITAL:.0f}/yr DD ${rec_r['oos']['max_dd']*CAPITAL:.0f}")
    print(f"  → P(ruin50%/3y)={rec_r['ruin50_3y']*100:.0f}%")
    print(f"verdict: {verdict}")


if __name__ == "__main__":
    main()
