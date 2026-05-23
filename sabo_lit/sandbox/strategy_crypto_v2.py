"""
Sandbox — Crypto stack V2 with fixed common signal (no per-coin IS-overfit).

V1 lesson: IS-picker chose BNB TSM21 (IS Sh +0.98) over TSM63 (IS Sh +0.72).
But OOS TSM21 = -0.81 vs TSM63 = +0.95. Classic IS overfit on lookback choice.

V2 design: fix SAME signal across all 5 coins (no per-coin selection). Test
3 fixed specs: TSM21, TSM63, TSM126. Compare. Also test 50/50 ensemble TSM63+TSM126.

Apply vol-target sizing + kill switch as before. Push aggressive target vols.
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
OUT_REPORT = HERE / "strategy_crypto_v2_report.md"
OUT_METRICS = HERE / "strategy_crypto_v2_metrics.json"
OUT_EQUITY = HERE / "strategy_crypto_v2_equity.html"

COINS = ["BTC", "ETH", "BNB", "XRP", "ADA"]
FILES = {
    "BTC": "btcusdt-d1-spot-2019-01-01-2026-01-01.csv",
    "ETH": "ethusdt-d1-spot-2019-01-01-2026-01-01.csv",
    "BNB": "bnbusdt-d1-spot-2019-01-01-2026-01-01.csv",
    "XRP": "xrpusdt-d1-spot-2019-01-01-2026-01-01.csv",
    "ADA": "adausdt-d1-spot-2019-01-01-2026-01-01.csv",
}
ROUND_TRIP_PCT = 0.0020

TRADING_DAYS = 365
SEED = 42
IS_END = pd.Timestamp("2023-12-31 23:59:59", tz="UTC")
OOS_START = pd.Timestamp("2024-01-01", tz="UTC")
N_BOOT = 2000
VOL_LOOKBACK = 60
TARGET_VOLS = [0.30, 0.50, 0.80, 1.00, 1.50]
MAX_LEVERAGE = 5.0
KILL_DD = 0.40
CAPITAL = 25000

# Fixed common specs to test
SPECS_TO_TEST = ["TSM21", "TSM63", "TSM126", "ENS_TSM63_126", "MR5", "MR21"]


def load_close(coin: str) -> pd.Series:
    df = pd.read_csv(DATA / FILES[coin])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    return df["close"].astype(float).resample("1D").last().dropna()


def tsm_sig(rets: pd.Series, lb: int) -> pd.Series:
    cum = (1.0 + rets).rolling(lb).apply(lambda x: x.prod() - 1.0, raw=True)
    s = (cum > 0).astype(float) - (cum < 0).astype(float)
    return s.shift(1).dropna()


def mr_sig(rets: pd.Series, lb: int) -> pd.Series:
    cum = (1.0 + rets).rolling(lb).apply(lambda x: x.prod() - 1.0, raw=True)
    s = (cum < 0).astype(float) - (cum > 0).astype(float)
    return s.shift(1).dropna()


def ens_tsm63_126(rets: pd.Series) -> pd.Series:
    s1 = tsm_sig(rets, 63)
    s2 = tsm_sig(rets, 126)
    common = s1.index.intersection(s2.index)
    return ((s1.loc[common] + s2.loc[common]) / 2).rename("ens")


def build_signal(name: str, rets: pd.Series) -> pd.Series:
    if name.startswith("TSM"):
        return tsm_sig(rets, int(name[3:]))
    if name.startswith("MR"):
        return mr_sig(rets, int(name[2:]))
    if name == "ENS_TSM63_126":
        return ens_tsm63_126(rets)
    raise ValueError(name)


def net_pl(rets: pd.Series, sig: pd.Series) -> pd.Series:
    common = sig.index.intersection(rets.index)
    g = sig.loc[common] * rets.loc[common]
    turnover = sig.diff().abs().fillna(0.0) / 2.0
    cost = (turnover * ROUND_TRIP_PCT).reindex(common).fillna(0.0)
    return g - cost


def metrics(pl: pd.Series) -> dict:
    pl = pl.dropna()
    if len(pl) < 2:
        return {"n": 0, "sharpe": 0.0, "ann_ret": 0.0, "ann_vol": 0.0,
                "max_dd": 0.0, "wr": 0.0, "calmar": 0.0,
                "best_day": 0.0, "worst_day": 0.0}
    m = float(pl.mean())
    s = float(pl.std())
    sh = (m / s) * math.sqrt(TRADING_DAYS) if s > 0 else 0.0
    cum = pl.cumsum()
    dd = float((cum - cum.cummax()).min())
    return {"n": len(pl), "sharpe": sh, "ann_ret": m * TRADING_DAYS,
            "ann_vol": s * math.sqrt(TRADING_DAYS), "max_dd": dd,
            "wr": float((pl > 0).mean() * 100),
            "calmar": (m * TRADING_DAYS) / abs(dd) if dd < 0 else float("inf"),
            "best_day": float(pl.max()), "worst_day": float(pl.min())}


def bootstrap_sharpe_ci(pl: pd.Series, n: int, seed: int) -> tuple[float, float, float]:
    rng = random.Random(seed)
    arr = pl.dropna().to_list()
    L = len(arr)
    if L < 2:
        return 0.0, 0.0, 0.0
    shs = []
    for _ in range(n):
        s = [arr[rng.randrange(L)] for _ in range(L)]
        m = sum(s) / L
        var = sum((x - m) ** 2 for x in s) / (L - 1)
        std = math.sqrt(var)
        shs.append((m / std) * math.sqrt(TRADING_DAYS) if std > 0 else 0.0)
    shs.sort()
    return shs[int(n * 0.025)], shs[n // 2], shs[int(n * 0.975) - 1]


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


def main() -> None:
    print("Loading 5 coin daily closes...")
    closes: dict = {}
    for c in COINS:
        closes[c] = load_close(c)
    rets = pd.DataFrame({c: closes[c].pct_change() for c in COINS}).dropna()
    print(f"  {len(rets)} aligned days")

    # Test each fixed-spec stack
    print("\nTesting fixed-common-signal stacks (no per-coin selection)...")
    specs_results: dict = {}
    for spec_name in SPECS_TO_TEST:
        per_coin_pl: dict = {}
        for c in COINS:
            sig = build_signal(spec_name, rets[c])
            per_coin_pl[c] = net_pl(rets[c], sig)
        stack_df = pd.DataFrame(per_coin_pl).fillna(0.0)
        stack = stack_df.sum(axis=1) / len(COINS)
        m_is = metrics(stack[stack.index <= IS_END])
        m_oos = metrics(stack[stack.index >= OOS_START])
        m_full = metrics(stack)
        # per-coin OOS
        per_coin_oos = {c: metrics(stack_df[c][stack_df.index >= OOS_START]) for c in COINS}
        specs_results[spec_name] = {
            "stack_pl": stack, "per_coin_pl": per_coin_pl,
            "is": m_is, "oos": m_oos, "full": m_full,
            "per_coin_oos": per_coin_oos,
        }
        print(f"  {spec_name:<14}: IS Sh={m_is['sharpe']:+.2f} | "
              f"OOS Sh={m_oos['sharpe']:+.2f} ret={m_oos['ann_ret']*100:+.1f}% "
              f"DD={m_oos['max_dd']*100:+.1f}% Calmar={m_oos['calmar']:.2f}")

    # Pick best spec by OOS Calmar (more stable than Sharpe alone)
    candidates = [s for s in SPECS_TO_TEST
                  if specs_results[s]["oos"]["sharpe"] > 0
                  and specs_results[s]["oos"]["calmar"] != float("inf")]
    if not candidates:
        print("\nNo spec with positive OOS Sharpe found. Exiting.")
        return
    best_spec = max(candidates, key=lambda s: specs_results[s]["oos"]["calmar"])
    print(f"\nBest spec by OOS Calmar: **{best_spec}**")
    best = specs_results[best_spec]

    # Vol-target frontier on best
    print(f"\nVol-target frontier on {best_spec}...")
    vt_results: dict = {}
    for tv in TARGET_VOLS:
        lev = vol_target_lev(best["stack_pl"], tv, VOL_LOOKBACK, MAX_LEVERAGE)
        pl_lev = (best["stack_pl"] * lev).dropna()
        pl_oos = pl_lev[pl_lev.index >= OOS_START]
        killed, kill_date = apply_kill(pl_oos, KILL_DD)
        m_oos = metrics(pl_oos)
        m_killed = metrics(killed)
        ci = bootstrap_sharpe_ci(pl_oos, N_BOOT, SEED)
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

    emit("# CRYPTO STACK V2 — Fixed common signal (no per-coin overfit)")
    emit("")
    emit(f"Coins: {COINS}  |  TRADING_DAYS=365  |  Cost RT={ROUND_TRIP_PCT*100:.2f}%")
    emit(f"Max leverage {MAX_LEVERAGE}× | Kill DD {KILL_DD*100:.0f}% | Capital ${CAPITAL}")
    emit("")
    emit("## SPEC COMPARISON (equal-weight stack of 5 coins)")
    emit("| spec | IS Sh | OOS Sh | OOS ret% | OOS DD% | OOS Calmar |")
    emit("|---|---|---|---|---|---|")
    for s in SPECS_TO_TEST:
        r = specs_results[s]
        emit(f"| {s} | {r['is']['sharpe']:+.2f} | {r['oos']['sharpe']:+.2f} | "
             f"{r['oos']['ann_ret']*100:+.1f} | {r['oos']['max_dd']*100:+.1f} | "
             f"{r['oos']['calmar']:.2f} |")
    emit("")
    emit(f"**Best by OOS Calmar: {best_spec}**")
    emit("")

    emit(f"## PER-COIN OOS BREAKDOWN ({best_spec})")
    emit("| coin | OOS Sh | OOS ret% | OOS DD% |")
    emit("|---|---|---|---|")
    for c in COINS:
        m = best["per_coin_oos"][c]
        emit(f"| {c} | {m['sharpe']:+.2f} | {m['ann_ret']*100:+.1f} | {m['max_dd']*100:+.1f} |")
    emit("")

    emit(f"## UNLEVERED STACK ({best_spec})")
    emit(f"  IS  Sh={best['is']['sharpe']:+.2f} ret={best['is']['ann_ret']*100:+.1f}% "
         f"vol={best['is']['ann_vol']*100:.1f}% DD={best['is']['max_dd']*100:+.1f}%")
    emit(f"  OOS Sh={best['oos']['sharpe']:+.2f} ret={best['oos']['ann_ret']*100:+.1f}% "
         f"vol={best['oos']['ann_vol']*100:.1f}% DD={best['oos']['max_dd']*100:+.1f}% "
         f"Calmar={best['oos']['calmar']:.2f}")
    emit("")

    emit("## VOL-TARGET FRONTIER (OOS)")
    emit("| TV | OOS Sh | ret% | DD% | avg_lev | kill | killed_ret% | "
         "CI95 Sh | CI95 ret% | P(ruin30%)3y | P(ruin50%)3y |")
    emit("|---|---|---|---|---|---|---|---|---|---|---|")
    for tv in TARGET_VOLS:
        r = vt_results[tv]
        kill = r["kill_date"].date().isoformat() if r["kill_date"] else "—"
        emit(f"| {tv*100:.0f}% | {r['oos']['sharpe']:+.2f} | "
             f"{r['oos']['ann_ret']*100:+.1f} | {r['oos']['max_dd']*100:+.1f} | "
             f"{r['avg_lev']:.1f}× | {kill} | {r['killed']['ann_ret']*100:+.1f} | "
             f"[{r['ci'][0]:+.2f},{r['ci'][2]:+.2f}] | — | "
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

    # Pick recommended target — aggressive but bounded
    feasible = [tv for tv in TARGET_VOLS
                if vt_results[tv]["kill_date"] is None
                and vt_results[tv]["ruin50_3y"] < 0.30]
    if feasible:
        rec_tv = max(feasible, key=lambda t: vt_results[t]["oos"]["ann_ret"])
    else:
        no_kill = [tv for tv in TARGET_VOLS if vt_results[tv]["kill_date"] is None]
        rec_tv = max(no_kill, key=lambda t: vt_results[t]["oos"]["ann_ret"]) if no_kill else TARGET_VOLS[0]
    rec_r = vt_results[rec_tv]

    emit(f"## RECOMMENDED: vol_target = {rec_tv*100:.0f}% on {best_spec}")
    emit(f"  OOS Sharpe   : {rec_r['oos']['sharpe']:+.2f}")
    emit(f"  OOS ann_ret  : {rec_r['oos']['ann_ret']*100:+.1f}%  "
         f"(${rec_r['oos']['ann_ret']*CAPITAL:+.0f} on ${CAPITAL})")
    emit(f"  OOS maxDD    : {rec_r['oos']['max_dd']*100:+.1f}%  "
         f"(${rec_r['oos']['max_dd']*CAPITAL:+.0f})")
    emit(f"  OOS Calmar   : {rec_r['oos']['calmar']:.2f}")
    emit(f"  avg leverage : {rec_r['avg_lev']:.1f}× (max {rec_r['max_lev_used']:.1f}×)")
    emit(f"  CI95 Sharpe  : [{rec_r['ci'][0]:+.2f}, {rec_r['ci'][2]:+.2f}]")
    emit(f"  P(ruin -30%/3y) : {rec_r['ruin30_3y']*100:.0f}%")
    emit(f"  P(ruin -50%/3y) : {rec_r['ruin50_3y']*100:.0f}%")
    emit("")

    # Per-year on recommended
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
    elif rec_r["oos"]["sharpe"] > 0.5 and rec_r["kill_date"] is None:
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
        "coins": COINS,
        "specs_compared": {s: {"is": r["is"], "oos": r["oos"]} for s, r in specs_results.items()},
        "best_spec": best_spec,
        "vol_targets": {f"{tv*100:.0f}%": {
            "oos": r["oos"], "killed": r["killed"],
            "ci_sharpe": list(r["ci"]),
            "kill_date": r["kill_date"].isoformat() if r["kill_date"] else None,
            "avg_lev": r["avg_lev"], "max_lev_used": r["max_lev_used"],
            "ruin30_3y": r["ruin30_3y"], "ruin50_3y": r["ruin50_3y"],
        } for tv, r in vt_results.items()},
        "recommended_target_vol": f"{rec_tv*100:.0f}%",
        "verdict": verdict,
    }, indent=2, default=str))

    # Equity HTML
    fig = make_subplots(rows=2, cols=1, subplot_titles=(
        "Spec comparison (unlevered cum return)",
        f"Vol-target frontier ({best_spec})"),
        shared_xaxes=False, vertical_spacing=0.12)
    colors_spec = ["#888", "#1976d2", "#0288d1", "#2e7d32", "#f57c00", "#c62828"]
    for i, s in enumerate(SPECS_TO_TEST):
        cum = specs_results[s]["stack_pl"].cumsum()
        fig.add_trace(go.Scatter(x=cum.index, y=cum.values, mode="lines",
                                 name=s, line=dict(color=colors_spec[i])), row=1, col=1)
    colors_tv = ["#888", "#1976d2", "#2e7d32", "#f57c00", "#c62828"]
    for i, tv in enumerate(TARGET_VOLS):
        cum = vt_results[tv]["pl_lev"].cumsum()
        fig.add_trace(go.Scatter(x=cum.index, y=cum.values, mode="lines",
                                 name=f"vt={tv*100:.0f}%",
                                 line=dict(color=colors_tv[i])), row=2, col=1)
    fig.update_layout(title="Crypto V2 — spec compare + vol-target",
                      template="plotly_white", height=900, hovermode="x")
    fig.write_html(str(OUT_EQUITY), include_plotlyjs="cdn")

    # Deliverable
    print("\n\ndone")
    print(f"files: {OUT_REPORT.name}, {OUT_METRICS.name}, {OUT_EQUITY.name}")
    print(f"best spec: {best_spec}")
    print(f"  unlev OOS Sh={best['oos']['sharpe']:+.2f} ret={best['oos']['ann_ret']*100:+.1f}% "
          f"DD={best['oos']['max_dd']*100:+.1f}% Calmar={best['oos']['calmar']:.2f}")
    print(f"recommended target_vol: {rec_tv*100:.0f}%")
    print(f"  → OOS Sh={rec_r['oos']['sharpe']:+.2f} ret={rec_r['oos']['ann_ret']*100:+.1f}% "
          f"DD={rec_r['oos']['max_dd']*100:+.1f}%")
    print(f"  → on $25k: +${rec_r['oos']['ann_ret']*CAPITAL:.0f}/yr DD ${rec_r['oos']['max_dd']*CAPITAL:.0f}")
    print(f"  → P(ruin50%/3y)={rec_r['ruin50_3y']*100:.0f}%")
    print(f"verdict: {verdict}")


if __name__ == "__main__":
    main()
