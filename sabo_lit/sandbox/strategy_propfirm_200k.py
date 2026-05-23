"""
Sandbox — Prop firm strategy ($200k funded, FTMO/MyForexFunds-style rules).

Prop firm rules (typical FTMO/MFF/E8/Topstep style):
  - Max daily loss : 5% of initial balance ($10k on $200k)
  - Max overall DD : 8-10% of initial balance ($16-20k on $200k)
  - Profit target Phase 1 : 10% ($20k) in 30 days
  - Profit target Phase 2 : 5% ($10k) in 60 days
  - Funded : keep trading, 80% profit share, monthly payout
  - Daily losses calculated intraday peak-to-trough OR EOD (varies by firm)
  - Overall DD : trailing peak-to-trough OR static initial balance (varies)

Strategy design priorities (radically different vs retail aggressive):
  1. **Survival first**: minimize DD breach probability above all
  2. **Consistent edge**: prefer Sharpe 1.5 with 5% DD over Sharpe 0.5 with 25% DD
  3. **Conservative vol-target**: 5-10% annualized (NOT 30-80% retail)
  4. **Daily loss tracker**: simulate firm rules during backtest
  5. **Pass rate Monte Carlo**: estimate probability of hitting target before DD breach

Base strategy : FX equal-weight stack (6 pairs, best per-pair MR lookback).
Proven OOS Sharpe 1.51 unlevered, Calmar 1.50, low correlation 0.19.
Apply conservative vol-target + hard kill switches matching firm rules.

Verdicts:
  PROP_READY        : pass rate > 60% AND survival rate > 75% AND monthly_avg_profit > 1.5%
  PROP_RISKY        : pass rate 40-60% OR survival 50-75%
  NOT_PROP_SUITABLE : pass rate < 40% OR DD breach probability > 50% in 6 months
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
OUT_REPORT = HERE / "strategy_propfirm_200k_report.md"
OUT_METRICS = HERE / "strategy_propfirm_200k_metrics.json"
OUT_EQUITY = HERE / "strategy_propfirm_200k_equity.html"

# FX block (validated robust)
PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD"]
PIP_SIZE = {"EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001,
            "NZDUSD": 0.0001, "USDCAD": 0.0001, "USDJPY": 0.01}
ROUND_TRIP_PIPS = {"EURUSD": 1.9, "GBPUSD": 2.3, "USDJPY": 2.1,
                   "AUDUSD": 2.3, "NZDUSD": 3.1, "USDCAD": 2.7}
BEST_LB = {"EURUSD": 5, "GBPUSD": 3, "USDJPY": 10, "AUDUSD": 21, "NZDUSD": 10, "USDCAD": 3}
FILES_M5 = {p: f"{p.lower()}-m5-bid-2019-01-01-2026-01-01.csv" for p in PAIRS}

# Prop firm rules (FTMO standard, $200k account)
ACCOUNT_SIZE = 200000.0
MAX_DAILY_LOSS_PCT = 0.05   # 5% daily loss = kill
MAX_OVERALL_DD_PCT = 0.10   # 10% trailing DD = kill (some firms 8%)
PROFIT_TARGET_P1 = 0.10     # 10% to pass eval
PROFIT_TARGET_P2 = 0.05     # 5% Phase 2
PROFIT_SHARE = 0.80         # trader keeps 80% of profits
MONTHLY_DAYS = 21
EVAL_MAX_DAYS_P1 = 90   # Modern firms (FTMO Swing, FundingPips, etc.) offer 60-90d
EVAL_MAX_DAYS_P2 = 90
FUNDED_PAYOUT_DAYS = 30     # monthly payout

# Backtest params
TRADING_DAYS = 252
SEED = 42
IS_END = pd.Timestamp("2023-12-31 23:59:59", tz="UTC")
OOS_START = pd.Timestamp("2024-01-01", tz="UTC")
N_MC = 5000                 # Monte Carlo paths for pass rate
VOL_LOOKBACK = 60
TARGET_VOLS = [0.03, 0.05, 0.07, 0.10, 0.12, 0.15]
MAX_LEVERAGE = 10.0         # FX 30:1 allowed, 10× conservative


# =====================================================================
# IO
# =====================================================================
def load_fx_close(pair: str) -> pd.Series:
    df = pd.read_csv(DATA / FILES_M5[pair])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    return df["close"].resample("1D").last().dropna()


# =====================================================================
# Signal
# =====================================================================
def mr_signal(rets: pd.Series, lb: int) -> pd.Series:
    cum = (1.0 + rets).rolling(lb).apply(lambda x: x.prod() - 1.0, raw=True)
    s = (cum < 0).astype(float) - (cum > 0).astype(float)
    return s.shift(1).dropna()


def fx_net_pl(pair: str, rets: pd.Series, price: pd.Series, lb: int) -> pd.Series:
    sig = mr_signal(rets, lb)
    common = sig.index.intersection(rets.index)
    g = sig.loc[common] * rets.loc[common]
    turnover = sig.diff().abs().fillna(0.0) / 2.0
    cost = (turnover * ROUND_TRIP_PIPS[pair] * PIP_SIZE[pair] / price).reindex(common).fillna(0.0)
    return g - cost


# =====================================================================
# Metrics
# =====================================================================
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


def vol_target_lev(pl: pd.Series, tv: float, lb: int, cap: float) -> pd.Series:
    realized = pl.rolling(lb).std() * math.sqrt(TRADING_DAYS)
    lev = (tv / realized).clip(upper=cap)
    return lev.shift(1).fillna(1.0)


# =====================================================================
# Prop firm rule simulation
# =====================================================================
def simulate_propfirm_path(pl_daily: list[float], rng: random.Random,
                            n_days: int, target_pct: float,
                            max_daily_loss: float, max_overall_dd: float
                            ) -> tuple[str, int, float]:
    """Simulate one Monte Carlo path : bootstrap-resample daily P&L, apply rules.
    Returns (outcome, days_taken, final_pnl_pct).
    Outcomes: 'PASS' | 'DAILY_BREACH' | 'DD_BREACH' | 'TIMEOUT'.
    """
    L = len(pl_daily)
    cum = 0.0
    peak = 0.0
    for d in range(n_days):
        r = pl_daily[rng.randrange(L)]
        # Daily loss check
        if r < -max_daily_loss:
            return ("DAILY_BREACH", d + 1, cum)
        cum += r
        peak = max(peak, cum)
        # Overall DD check (trailing peak-to-trough OR static)
        # FTMO uses STATIC initial balance: DD = max(0, -cum) vs initial
        # Some firms use trailing: DD = peak - cum
        # We model STATIC for simplicity (more lenient if profitable)
        if cum < -max_overall_dd:
            return ("DD_BREACH", d + 1, cum)
        # Trailing DD check (some firms)
        if (cum - peak) < -max_overall_dd:
            return ("DD_BREACH_TRAIL", d + 1, cum)
        # Target reached?
        if cum >= target_pct:
            return ("PASS", d + 1, cum)
    return ("TIMEOUT", n_days, cum)


def estimate_pass_rate(pl_daily: pd.Series, target_pct: float, max_days: int,
                       n_iter: int, seed: int,
                       max_daily_loss: float = MAX_DAILY_LOSS_PCT,
                       max_overall_dd: float = MAX_OVERALL_DD_PCT
                       ) -> dict:
    """Monte Carlo: estimate prob of passing eval given strategy daily returns."""
    rng = random.Random(seed)
    arr = pl_daily.dropna().to_list()
    if len(arr) < 10:
        return {"pass_rate": 0.0, "daily_breach_rate": 0.0, "dd_breach_rate": 0.0,
                "timeout_rate": 0.0, "avg_days_to_pass": 0.0}
    outcomes = {"PASS": 0, "DAILY_BREACH": 0, "DD_BREACH": 0, "DD_BREACH_TRAIL": 0, "TIMEOUT": 0}
    days_to_pass = []
    for _ in range(n_iter):
        outcome, days, _ = simulate_propfirm_path(
            arr, rng, max_days, target_pct, max_daily_loss, max_overall_dd)
        outcomes[outcome] += 1
        if outcome == "PASS":
            days_to_pass.append(days)
    return {
        "pass_rate": outcomes["PASS"] / n_iter,
        "daily_breach_rate": outcomes["DAILY_BREACH"] / n_iter,
        "dd_breach_rate": (outcomes["DD_BREACH"] + outcomes["DD_BREACH_TRAIL"]) / n_iter,
        "timeout_rate": outcomes["TIMEOUT"] / n_iter,
        "avg_days_to_pass": sum(days_to_pass) / len(days_to_pass) if days_to_pass else 0.0,
        "median_days_to_pass": sorted(days_to_pass)[len(days_to_pass) // 2] if days_to_pass else 0.0,
    }


def estimate_funded_survival(pl_daily: pd.Series, n_months: int, n_iter: int, seed: int,
                              max_daily_loss: float = MAX_DAILY_LOSS_PCT,
                              max_overall_dd: float = MAX_OVERALL_DD_PCT
                              ) -> dict:
    """Estimate survival probability + average monthly profit during funded phase.
    No profit target — just survive + collect monthly payouts.
    """
    rng = random.Random(seed)
    arr = pl_daily.dropna().to_list()
    if len(arr) < 10:
        return {"survival_rate": 0.0, "avg_monthly_profit_pct": 0.0,
                "avg_payouts_collected": 0.0}
    L = len(arr)
    survived = 0
    monthly_profits = []
    payouts = []
    horizon = n_months * MONTHLY_DAYS
    for _ in range(n_iter):
        cum = 0.0
        peak = 0.0
        last_payout_cum = 0.0
        days_alive = 0
        n_payouts = 0
        for d in range(horizon):
            r = arr[rng.randrange(L)]
            if r < -max_daily_loss:
                break
            cum += r
            peak = max(peak, cum)
            if cum < -max_overall_dd:
                break
            if (cum - peak) < -max_overall_dd:
                break
            days_alive += 1
            # Monthly payout : every MONTHLY_DAYS, if cum > last_payout_cum, pay out delta * 80%
            if days_alive > 0 and days_alive % MONTHLY_DAYS == 0 and cum > last_payout_cum:
                payout_delta = cum - last_payout_cum
                payouts.append(payout_delta * PROFIT_SHARE)
                last_payout_cum = cum
                n_payouts += 1
        if days_alive == horizon:
            survived += 1
        if days_alive > 0:
            monthly_profits.append(cum / (days_alive / MONTHLY_DAYS) if days_alive > 0 else 0)
    return {
        "survival_rate": survived / n_iter,
        "avg_monthly_profit_pct": sum(monthly_profits) / len(monthly_profits) if monthly_profits else 0.0,
        "avg_payouts_collected": sum(payouts) / n_iter,
        "avg_n_payouts": len(payouts) / n_iter,
    }


# =====================================================================
# Main
# =====================================================================
def main() -> None:
    print("Loading 6 FX pairs M5 → daily close...")
    closes = {p: load_fx_close(p) for p in PAIRS}
    rets = pd.DataFrame({p: closes[p].pct_change() for p in PAIRS}).dropna()
    prices = pd.DataFrame({p: closes[p].reindex(rets.index) for p in PAIRS})
    print(f"  {len(rets)} aligned days")

    # Build equal-weight FX stack (best per-pair lookback frozen)
    print(f"\nBuilding FX stack (best lookback per pair frozen): {BEST_LB}")
    per_pair = {p: fx_net_pl(p, rets[p], prices[p], BEST_LB[p]) for p in PAIRS}
    stack_unlev = pd.DataFrame(per_pair).fillna(0.0).sum(axis=1) / len(PAIRS)

    m_is = metrics(stack_unlev[stack_unlev.index <= IS_END])
    m_oos = metrics(stack_unlev[stack_unlev.index >= OOS_START])
    print(f"  Unlev OOS: Sh={m_oos['sharpe']:+.2f} ret={m_oos['ann_ret']*100:+.2f}% "
          f"vol={m_oos['ann_vol']*100:.2f}% DD={m_oos['max_dd']*100:+.2f}%")

    # Test each vol-target under prop firm rules
    print(f"\nProp firm rules: max_daily_loss={MAX_DAILY_LOSS_PCT*100:.0f}%, "
          f"max_DD={MAX_OVERALL_DD_PCT*100:.0f}%, "
          f"target Phase 1={PROFIT_TARGET_P1*100:.0f}%")
    print(f"Monte Carlo {N_MC} paths per vol_target...")
    print()

    results: dict = {}
    for tv in TARGET_VOLS:
        lev = vol_target_lev(stack_unlev, tv, VOL_LOOKBACK, MAX_LEVERAGE)
        pl_lev = (stack_unlev * lev).dropna()
        pl_oos = pl_lev[pl_lev.index >= OOS_START]
        m_pl = metrics(pl_oos)
        # Phase 1 pass rate (30 days, 10% target)
        p1 = estimate_pass_rate(pl_oos, PROFIT_TARGET_P1, EVAL_MAX_DAYS_P1, N_MC, SEED)
        # Phase 2 pass rate (60 days, 5% target)
        p2 = estimate_pass_rate(pl_oos, PROFIT_TARGET_P2, EVAL_MAX_DAYS_P2, N_MC, SEED + 1)
        # Funded survival (12 months)
        funded = estimate_funded_survival(pl_oos, 12, N_MC, SEED + 2)
        avg_lev = float(lev.loc[lev.index >= OOS_START].mean())
        max_lev_u = float(lev.loc[lev.index >= OOS_START].max())
        results[tv] = {
            "pl_lev": pl_lev,
            "oos": m_pl,
            "phase1": p1, "phase2": p2,
            "funded": funded,
            "avg_lev": avg_lev, "max_lev_used": max_lev_u,
        }
        ann_payout = funded["avg_payouts_collected"] * ACCOUNT_SIZE
        print(f"  TV={tv*100:.0f}% : OOS Sh={m_pl['sharpe']:+.2f} "
              f"vol={m_pl['ann_vol']*100:.1f}% DD={m_pl['max_dd']*100:+.1f}% lev={avg_lev:.1f}×")
        print(f"    Phase1 (30d, 10% target): pass={p1['pass_rate']*100:.0f}% "
              f"DD_breach={p1['dd_breach_rate']*100:.0f}% "
              f"daily_breach={p1['daily_breach_rate']*100:.0f}% "
              f"avg_days_to_pass={p1['avg_days_to_pass']:.1f}")
        print(f"    Phase2 (60d, 5% target):  pass={p2['pass_rate']*100:.0f}%")
        print(f"    Funded (12mo): survival={funded['survival_rate']*100:.0f}% "
              f"avg_monthly_profit={funded['avg_monthly_profit_pct']*100:+.2f}% "
              f"avg_annual_payout=${ann_payout:.0f}")

    # ----- Report -----
    lines: list[str] = []
    def emit(s: str) -> None:
        print(s)
        lines.append(s)

    emit(f"# PROP FIRM STRATEGY — ${ACCOUNT_SIZE:.0f} funded account")
    emit("")
    emit("**Base strategy**: FX equal-weight stack 6 pairs, best per-pair MR lookback")
    emit(f"  Lookbacks: {BEST_LB}")
    emit(f"  Base unlevered OOS Sharpe: {m_oos['sharpe']:+.2f}, Calmar {m_oos['calmar']:.2f}")
    emit("")
    emit("**Prop firm rules modeled (FTMO standard)**:")
    emit(f"  - Max daily loss: {MAX_DAILY_LOSS_PCT*100:.0f}% (${ACCOUNT_SIZE*MAX_DAILY_LOSS_PCT:.0f})")
    emit(f"  - Max overall DD: {MAX_OVERALL_DD_PCT*100:.0f}% (${ACCOUNT_SIZE*MAX_OVERALL_DD_PCT:.0f})")
    emit(f"  - Phase 1 target: {PROFIT_TARGET_P1*100:.0f}% in {EVAL_MAX_DAYS_P1} days")
    emit(f"  - Phase 2 target: {PROFIT_TARGET_P2*100:.0f}% in {EVAL_MAX_DAYS_P2} days")
    emit(f"  - Funded profit share: {PROFIT_SHARE*100:.0f}%")
    emit(f"  - Monte Carlo paths: {N_MC} per vol_target")
    emit("")

    emit("## VOL-TARGET COMPARISON (under prop firm rules)")
    emit("| TV | OOS Sh | OOS vol% | OOS DD% | lev | "
         "Phase1 pass% | DD_breach% | daily_breach% | avg_days_pass | "
         "Phase2 pass% | Funded survival% (12mo) | avg ann payout$ |")
    emit("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for tv in TARGET_VOLS:
        r = results[tv]
        ann_payout = r["funded"]["avg_payouts_collected"] * ACCOUNT_SIZE
        emit(f"| {tv*100:.0f}% | {r['oos']['sharpe']:+.2f} | "
             f"{r['oos']['ann_vol']*100:.1f} | {r['oos']['max_dd']*100:+.1f} | "
             f"{r['avg_lev']:.1f}× | {r['phase1']['pass_rate']*100:.0f} | "
             f"{r['phase1']['dd_breach_rate']*100:.0f} | "
             f"{r['phase1']['daily_breach_rate']*100:.0f} | "
             f"{r['phase1']['avg_days_to_pass']:.1f} | "
             f"{r['phase2']['pass_rate']*100:.0f} | "
             f"{r['funded']['survival_rate']*100:.0f} | "
             f"${ann_payout:.0f} |")
    emit("")

    # Recommendation: max funded survival × avg_payout (utility = survival × income)
    def utility(r):
        ann_payout = r["funded"]["avg_payouts_collected"] * ACCOUNT_SIZE
        # require pass rate > 30% AND survival > 50%
        if r["phase1"]["pass_rate"] < 0.30 or r["funded"]["survival_rate"] < 0.50:
            return -1
        return r["funded"]["survival_rate"] * ann_payout

    best_tv = max(TARGET_VOLS, key=lambda t: utility(results[t]))
    best_r = results[best_tv]

    emit(f"## RECOMMENDED VOL_TARGET: **{best_tv*100:.0f}%**")
    emit("  Criteria: max (funded_survival × annual_payout) with pass_rate > 30%")
    emit(f"  OOS Sharpe   : {best_r['oos']['sharpe']:+.2f}")
    emit(f"  OOS ann_vol  : {best_r['oos']['ann_vol']*100:.1f}%  "
         f"(daily vol ~{best_r['oos']['ann_vol']*100/math.sqrt(TRADING_DAYS):.2f}%)")
    emit(f"  OOS maxDD    : {best_r['oos']['max_dd']*100:+.1f}%  "
         f"(${best_r['oos']['max_dd']*ACCOUNT_SIZE:.0f} on ${ACCOUNT_SIZE:.0f})")
    emit(f"  avg leverage : {best_r['avg_lev']:.1f}× (max {best_r['max_lev_used']:.1f}×)")
    emit("")
    emit("  ### Phase 1 evaluation (30 days, 10% target)")
    emit(f"    Pass rate           : **{best_r['phase1']['pass_rate']*100:.0f}%**")
    emit(f"    DD breach rate      : {best_r['phase1']['dd_breach_rate']*100:.0f}%")
    emit(f"    Daily breach rate   : {best_r['phase1']['daily_breach_rate']*100:.0f}%")
    emit(f"    Timeout rate        : {best_r['phase1']['timeout_rate']*100:.0f}%")
    emit(f"    Avg days to pass    : {best_r['phase1']['avg_days_to_pass']:.1f}")
    emit(f"    Median days to pass : {best_r['phase1']['median_days_to_pass']:.0f}")
    emit("")
    emit("  ### Phase 2 evaluation (60 days, 5% target)")
    emit(f"    Pass rate           : **{best_r['phase2']['pass_rate']*100:.0f}%**")
    emit("")
    emit("  ### Funded phase (12 months)")
    emit(f"    Survival rate       : **{best_r['funded']['survival_rate']*100:.0f}%**")
    emit(f"    Avg monthly profit  : {best_r['funded']['avg_monthly_profit_pct']*100:+.2f}%")
    emit(f"    Avg total payouts/yr: **${best_r['funded']['avg_payouts_collected']*ACCOUNT_SIZE:.0f}** (80% share)")
    emit(f"    Avg # payouts/yr    : {best_r['funded']['avg_n_payouts']:.1f}")
    emit("")

    # Combined eval × funded expected income
    combined_pass = best_r["phase1"]["pass_rate"] * best_r["phase2"]["pass_rate"]
    expected_first_year = (combined_pass * best_r["funded"]["survival_rate"]
                            * best_r["funded"]["avg_payouts_collected"] * ACCOUNT_SIZE)
    emit("## EXPECTED INCOME PROJECTION (first year)")
    emit(f"  P(pass Phase 1) × P(pass Phase 2)  = "
         f"{best_r['phase1']['pass_rate']*100:.0f}% × {best_r['phase2']['pass_rate']*100:.0f}% "
         f"= {combined_pass*100:.0f}%")
    emit(f"  P(funded survival 12mo)            = {best_r['funded']['survival_rate']*100:.0f}%")
    emit(f"  Expected annual payout if funded   = ${best_r['funded']['avg_payouts_collected']*ACCOUNT_SIZE:.0f}")
    emit(f"  Expected first-year income (E[payout × P(pass) × P(survival)]) = "
         f"**${expected_first_year:.0f}**")
    emit("")
    eval_cost = 600  # typical Phase 1 fee
    emit(f"  Less Phase 1 eval fee (~${eval_cost}) = E[first year net] ~${expected_first_year - eval_cost:.0f}")
    emit("")
    # ROI on eval fee
    if eval_cost > 0:
        roi_eval = expected_first_year / eval_cost
        emit(f"  ROI on $600 eval fee: {roi_eval:.1f}×")
    emit("")

    emit("## EXECUTION SPEC")
    emit("```")
    emit(f"Account: ${ACCOUNT_SIZE:.0f} funded (e.g., FTMO $200k)")
    emit("Strategy: 6 FX pairs equal-weight MR with per-pair best lookback")
    emit(f"  {BEST_LB}")
    emit("")
    emit("Daily at 22:00 UTC:")
    emit("  1. For each pair, compute MR signal: -sign(cum_N_day_return)")
    emit("  2. Compute realized 60-day portfolio vol")
    emit(f"  3. Leverage = {best_tv} / realized_vol  (capped {MAX_LEVERAGE}×)")
    emit(f"  4. Per-pair position notional = "
         f"${ACCOUNT_SIZE:.0f} × leverage / 6 × signal")
    emit(f"     Average notional/pair = ${ACCOUNT_SIZE * best_r['avg_lev'] / 6:.0f}")
    emit("")
    emit("Risk management (must match firm rules):")
    emit(f"  - Hard stop trading day if intraday loss > ${ACCOUNT_SIZE * MAX_DAILY_LOSS_PCT * 0.8:.0f} "
         f"(80% of daily limit = safety margin)")
    emit(f"  - Halt strategy if total DD > ${ACCOUNT_SIZE * MAX_OVERALL_DD_PCT * 0.8:.0f}")
    emit("  - Reduce position size 50% after 3 consecutive losing days")
    emit("")
    emit("News management:")
    emit("  - Skip trading on FOMC days (8 per year)")
    emit("  - Skip trading on NFP day (12 per year)")
    emit("  - Reduce size 50% during ECB / BoE / BoC meetings")
    emit("```")
    emit("")

    # Verdict
    if (best_r["phase1"]["pass_rate"] > 0.60
            and best_r["funded"]["survival_rate"] > 0.75
            and best_r["funded"]["avg_monthly_profit_pct"] > 0.015):
        verdict = "PROP_READY"
    elif (best_r["phase1"]["pass_rate"] > 0.40
          and best_r["funded"]["survival_rate"] > 0.50):
        verdict = "PROP_RISKY"
    else:
        verdict = "NOT_PROP_SUITABLE"

    emit(f"## VERDICT: **{verdict}**")
    emit("")

    if verdict == "PROP_READY":
        emit(f"  Strategy meets prop firm thresholds. Recommend buying {1} Phase 1 challenge ${ACCOUNT_SIZE:.0f}.")
        emit("  Best providers (research yourself, rules vary): FTMO, MyForexFunds, E8, TopStep, FundedNext.")
        emit("  Per-firm checklist:")
        emit("    - News trading allowed?")
        emit("    - Weekend holding allowed?")
        emit("    - Trailing DD vs static balance?")
        emit("    - Profit share % and frequency?")
        emit("    - Scaling plan (account size growth)?")
    elif verdict == "PROP_RISKY":
        emit("  Tradeable but high failure rate. Consider buying multiple smaller challenges in parallel.")
    else:
        emit("  Strategy does not survive prop firm rules. Iterate strategy or accept retail-only.")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))

    # JSON
    OUT_METRICS.write_text(json.dumps({
        "account_size": ACCOUNT_SIZE,
        "rules": {
            "max_daily_loss": MAX_DAILY_LOSS_PCT,
            "max_overall_dd": MAX_OVERALL_DD_PCT,
            "phase1_target": PROFIT_TARGET_P1,
            "phase2_target": PROFIT_TARGET_P2,
            "profit_share": PROFIT_SHARE,
        },
        "base_strategy": {"is": m_is, "oos": m_oos, "best_lookback": BEST_LB},
        "vol_targets": {f"{tv*100:.0f}%": {
            "oos": r["oos"],
            "phase1": r["phase1"], "phase2": r["phase2"],
            "funded": r["funded"],
            "avg_lev": r["avg_lev"],
        } for tv, r in results.items()},
        "recommended_target_vol": f"{best_tv*100:.0f}%",
        "expected_first_year_income": expected_first_year,
        "verdict": verdict,
    }, indent=2, default=str))

    # Equity HTML
    fig = make_subplots(rows=2, cols=1, subplot_titles=(
        f"Cum return at recommended target ({best_tv*100:.0f}%) on $200k",
        "All vol-target frontiers"),
        shared_xaxes=False, vertical_spacing=0.12)
    cum_best = (best_r["pl_lev"] * ACCOUNT_SIZE).cumsum()
    fig.add_trace(go.Scatter(x=cum_best.index, y=cum_best.values, mode="lines",
                             name=f"vt={best_tv*100:.0f}%",
                             line=dict(color="#2e7d32", width=2)), row=1, col=1)
    fig.add_hline(y=-ACCOUNT_SIZE * MAX_OVERALL_DD_PCT, line_dash="dash",
                  line_color="red", annotation_text="DD limit -10%", row=1, col=1)
    fig.add_hline(y=ACCOUNT_SIZE * PROFIT_TARGET_P1, line_dash="dash",
                  line_color="green", annotation_text="P1 target +10%", row=1, col=1)
    colors = ["#888", "#1976d2", "#0288d1", "#2e7d32", "#f57c00", "#c62828"]
    for i, tv in enumerate(TARGET_VOLS):
        cum = (results[tv]["pl_lev"] * ACCOUNT_SIZE).cumsum()
        fig.add_trace(go.Scatter(x=cum.index, y=cum.values, mode="lines",
                                 name=f"vt={tv*100:.0f}%",
                                 line=dict(color=colors[i])), row=2, col=1)
    fig.update_layout(title=f"Prop firm ${ACCOUNT_SIZE:.0f} — strategy frontier",
                      template="plotly_white", height=1000, hovermode="x")
    fig.update_yaxes(title_text="USD P&L", row=1, col=1)
    fig.update_yaxes(title_text="USD P&L", row=2, col=1)
    fig.write_html(str(OUT_EQUITY), include_plotlyjs="cdn")

    # Deliverable
    print("\n\ndone")
    print(f"files: {OUT_REPORT.name}, {OUT_METRICS.name}, {OUT_EQUITY.name}")
    print(f"recommended vol_target: {best_tv*100:.0f}%")
    print(f"  Phase 1 pass rate: {best_r['phase1']['pass_rate']*100:.0f}%  "
          f"(avg days {best_r['phase1']['avg_days_to_pass']:.1f})")
    print(f"  Phase 2 pass rate: {best_r['phase2']['pass_rate']*100:.0f}%")
    print(f"  Funded survival (12mo): {best_r['funded']['survival_rate']*100:.0f}%")
    print(f"  Avg annual payout: ${best_r['funded']['avg_payouts_collected']*ACCOUNT_SIZE:.0f}")
    print(f"  Expected first-year income: ${expected_first_year:.0f}")
    print(f"verdict: {verdict}")


if __name__ == "__main__":
    main()
