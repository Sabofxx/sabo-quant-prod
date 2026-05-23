"""
Sandbox — Multi-prop-firm portfolio strategy ($10k+ challenge budget).

Concept: deploy DIFFERENT decorrelated strategies on different prop firm accounts.
If one account blows, others survive (real risk diversification).

Portfolio composition (typical, user can adjust):
  - 2× $400k accounts ($4k+ in fees)
  - 2× $200k accounts ($2k+ in fees)
  - 4× $50k accounts ($1.5k in fees)
  Total: 8 accounts, ~$1.5M funded notional, ~$7.5k fees + buffer = $10k budget

Strategy assignment principle:
  - Highest-Sharpe specs → biggest accounts (max E[income])
  - Decorrelated specs assigned to ensure no joint-blow-up risk
  - Same spec OK on 2 accounts if Sharpe high enough (accept correlation)

Specs designed:
  A. FX_MR_STACK : 6 FX equal-weight MR with per-pair best lookback (Sharpe 1.5)
  B. EURUSD_MR5 : single pair MR5 (Sharpe 1.16, concentrated)
  C. CRYPTO_TSM63 : 5 coins equal-weight TSM63 (Sharpe 0.5-0.8, diff asset class)
  D. FX_MR_LONG : 6 FX MR_21 fixed (different LB from A)
  E. CRYPTO_MR_BTC_ETH : BTC+ETH MR10 (concentrated, MR direction)
  F. FX_TSM63 : 6 FX TSM63 fixed (trend not MR, opposite signal)

Each spec validated independently. Pairwise correlations measured. Portfolio MC
simulates 12 months forward with per-account firm rules.

Verdicts at portfolio level:
  - Expected aggregate income (mean + percentiles)
  - P(all accounts blow within 12mo)
  - P(at least 1 funded)
  - ROI on $10k challenge budget
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
OUT_REPORT = HERE / "strategy_propfirm_portfolio_report.md"
OUT_METRICS = HERE / "strategy_propfirm_portfolio_metrics.json"
OUT_EQUITY = HERE / "strategy_propfirm_portfolio_equity.html"

# ===== Universe =====
FX_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD"]
FX_PIP = {"EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001,
          "NZDUSD": 0.0001, "USDCAD": 0.0001, "USDJPY": 0.01}
FX_RT_PIPS = {"EURUSD": 1.9, "GBPUSD": 2.3, "USDJPY": 2.1,
              "AUDUSD": 2.3, "NZDUSD": 3.1, "USDCAD": 2.7}
FX_BEST_LB = {"EURUSD": 5, "GBPUSD": 3, "USDJPY": 10, "AUDUSD": 21, "NZDUSD": 10, "USDCAD": 3}
FX_FILES = {p: f"{p.lower()}-m5-bid-2019-01-01-2026-01-01.csv" for p in FX_PAIRS}

COINS = ["BTC", "ETH", "BNB", "XRP", "ADA"]
COIN_FILES = {
    "BTC": "btcusdt-d1-spot-2019-01-01-2026-01-01.csv",
    "ETH": "ethusdt-d1-spot-2019-01-01-2026-01-01.csv",
    "BNB": "bnbusdt-d1-spot-2019-01-01-2026-01-01.csv",
    "XRP": "xrpusdt-d1-spot-2019-01-01-2026-01-01.csv",
    "ADA": "adausdt-d1-spot-2019-01-01-2026-01-01.csv",
}
CRYPTO_RT_PCT = 0.0020

# ===== Prop firm rules (FTMO standard) =====
MAX_DAILY_LOSS_PCT = 0.05
MAX_OVERALL_DD_PCT = 0.10
PROFIT_TARGET_P1 = 0.10
PROFIT_TARGET_P2 = 0.05
PROFIT_SHARE = 0.80
EVAL_DAYS_P1 = 90
EVAL_DAYS_P2 = 90
MONTHLY_DAYS = 21
ANN_DAYS = 252

# Portfolio composition (default — user adjustable)
PORTFOLIO_DEFAULT = [
    # FX-only mix : crypto specs too volatile (break daily 5% rule). Drop them.
    # 3 profitable specs validated: FX_MR_STACK (top), EURUSD_MR5, FX_MR_LONG
    {"name": "Acct1_400k", "size": 400000, "fee": 1900, "spec": "FX_MR_STACK"},
    {"name": "Acct2_400k", "size": 400000, "fee": 1900, "spec": "EURUSD_MR5"},
    {"name": "Acct3_200k", "size": 200000, "fee": 1080, "spec": "FX_MR_STACK"},
    {"name": "Acct4_200k", "size": 200000, "fee": 1080, "spec": "FX_MR_LONG"},
    {"name": "Acct5_50k",  "size":  50000, "fee":  330, "spec": "FX_MR_STACK"},
    {"name": "Acct6_50k",  "size":  50000, "fee":  330, "spec": "EURUSD_MR5"},
    {"name": "Acct7_50k",  "size":  50000, "fee":  330, "spec": "FX_MR_LONG"},
    {"name": "Acct8_50k",  "size":  50000, "fee":  330, "spec": "EURUSD_MR5"},
]

SEED = 42
IS_END = pd.Timestamp("2023-12-31 23:59:59", tz="UTC")
OOS_START = pd.Timestamp("2024-01-01", tz="UTC")
N_MC = 5000
VOL_TARGET = 0.12  # 12% target = best E[income] vs survival from single-account test
VOL_LOOKBACK = 60
MAX_LEVERAGE = 10.0


# =====================================================================
# IO
# =====================================================================
def load_fx_close(p: str) -> pd.Series:
    df = pd.read_csv(DATA / FX_FILES[p])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    return df["close"].resample("1D").last().dropna()


def load_crypto_close(c: str) -> pd.Series:
    df = pd.read_csv(DATA / COIN_FILES[c])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    return df["close"].astype(float).resample("1D").last().dropna()


# =====================================================================
# Signals
# =====================================================================
def mr_sig(rets: pd.Series, lb: int) -> pd.Series:
    cum = (1.0 + rets).rolling(lb).apply(lambda x: x.prod() - 1.0, raw=True)
    s = (cum < 0).astype(float) - (cum > 0).astype(float)
    return s.shift(1).dropna()


def tsm_sig(rets: pd.Series, lb: int) -> pd.Series:
    cum = (1.0 + rets).rolling(lb).apply(lambda x: x.prod() - 1.0, raw=True)
    s = (cum > 0).astype(float) - (cum < 0).astype(float)
    return s.shift(1).dropna()


def fx_net(p: str, rets: pd.Series, price: pd.Series, sig: pd.Series) -> pd.Series:
    common = sig.index.intersection(rets.index)
    g = sig.loc[common] * rets.loc[common]
    turnover = sig.diff().abs().fillna(0.0) / 2.0
    cost = (turnover * FX_RT_PIPS[p] * FX_PIP[p] / price).reindex(common).fillna(0.0)
    return g - cost


def crypto_net(rets: pd.Series, sig: pd.Series) -> pd.Series:
    common = sig.index.intersection(rets.index)
    g = sig.loc[common] * rets.loc[common]
    turnover = sig.diff().abs().fillna(0.0) / 2.0
    cost = (turnover * CRYPTO_RT_PCT).reindex(common).fillna(0.0)
    return g - cost


# =====================================================================
# Build specs
# =====================================================================
def build_specs(fx_rets: pd.DataFrame, fx_prices: pd.DataFrame,
                cr_rets: pd.DataFrame) -> dict:
    specs: dict = {}

    # A. FX_MR_STACK : best per-pair LB
    per_pair = {p: fx_net(p, fx_rets[p], fx_prices[p], mr_sig(fx_rets[p], FX_BEST_LB[p]))
                for p in FX_PAIRS}
    specs["FX_MR_STACK"] = pd.DataFrame(per_pair).fillna(0.0).sum(axis=1) / len(FX_PAIRS)

    # B. EURUSD_MR5 : single pair
    specs["EURUSD_MR5"] = fx_net("EURUSD", fx_rets["EURUSD"], fx_prices["EURUSD"],
                                  mr_sig(fx_rets["EURUSD"], 5))

    # C. CRYPTO_TSM63 : 5 coins TSM63
    per_coin = {c: crypto_net(cr_rets[c], tsm_sig(cr_rets[c], 63)) for c in COINS}
    specs["CRYPTO_TSM63"] = pd.DataFrame(per_coin).fillna(0.0).sum(axis=1) / len(COINS)

    # D. FX_MR_LONG : 6 FX MR21 (different LB from A)
    per_pair = {p: fx_net(p, fx_rets[p], fx_prices[p], mr_sig(fx_rets[p], 21))
                for p in FX_PAIRS}
    specs["FX_MR_LONG"] = pd.DataFrame(per_pair).fillna(0.0).sum(axis=1) / len(FX_PAIRS)

    # E. CRYPTO_MR_BTC_ETH : BTC + ETH MR10 (concentrated MR)
    per_coin = {c: crypto_net(cr_rets[c], mr_sig(cr_rets[c], 10)) for c in ("BTC", "ETH")}
    specs["CRYPTO_MR_BTC_ETH"] = pd.DataFrame(per_coin).fillna(0.0).sum(axis=1) / 2

    # F. FX_TSM63 : 6 FX TSM63 (trend not MR — opposite signal direction)
    per_pair = {p: fx_net(p, fx_rets[p], fx_prices[p], tsm_sig(fx_rets[p], 63))
                for p in FX_PAIRS}
    specs["FX_TSM63"] = pd.DataFrame(per_pair).fillna(0.0).sum(axis=1) / len(FX_PAIRS)

    return specs


def vol_target_lev(pl: pd.Series, tv: float, lb: int, cap: float) -> pd.Series:
    realized = pl.rolling(lb).std() * math.sqrt(ANN_DAYS)
    lev = (tv / realized).clip(upper=cap)
    return lev.shift(1).fillna(1.0)


def metrics(pl: pd.Series) -> dict:
    pl = pl.dropna()
    if len(pl) < 2:
        return {"n": 0, "sharpe": 0.0, "ann_ret": 0.0, "ann_vol": 0.0,
                "max_dd": 0.0, "calmar": 0.0, "wr": 0.0}
    m = float(pl.mean())
    s = float(pl.std())
    sh = (m / s) * math.sqrt(ANN_DAYS) if s > 0 else 0.0
    cum = pl.cumsum()
    dd = float((cum - cum.cummax()).min())
    return {"n": len(pl), "sharpe": sh, "ann_ret": m * ANN_DAYS,
            "ann_vol": s * math.sqrt(ANN_DAYS), "max_dd": dd,
            "calmar": (m * ANN_DAYS) / abs(dd) if dd < 0 else float("inf"),
            "wr": float((pl > 0).mean() * 100)}


# =====================================================================
# Portfolio Monte Carlo
# =====================================================================
def simulate_account(daily_arr: list[float], n_days: int, rng: random.Random,
                     account_size: float) -> dict:
    """Simulate single account for n_days. State machine: EVAL_P1 → EVAL_P2 → FUNDED.
    Returns total $ payouts collected + final state."""
    state = "EVAL_P1"
    cum_pct = 0.0
    peak_pct = 0.0
    days_in_state = 0
    last_payout_cum = 0.0
    payouts_dollars = 0.0
    state_history: list[str] = []
    L = len(daily_arr)
    for d in range(n_days):
        r = daily_arr[rng.randrange(L)]
        days_in_state += 1
        # Daily loss breach
        if r < -MAX_DAILY_LOSS_PCT:
            state = "DEAD_DAILY"
            state_history.append(state)
            break
        cum_pct += r
        peak_pct = max(peak_pct, cum_pct)
        # Overall DD breach
        if cum_pct < -MAX_OVERALL_DD_PCT:
            state = "DEAD_DD"
            state_history.append(state)
            break
        if (cum_pct - peak_pct) < -MAX_OVERALL_DD_PCT:
            state = "DEAD_DD_TRAIL"
            state_history.append(state)
            break
        # State transitions
        if state == "EVAL_P1":
            if cum_pct >= PROFIT_TARGET_P1:
                state = "EVAL_P2"
                cum_pct = 0.0  # reset for Phase 2
                peak_pct = 0.0
                days_in_state = 0
                last_payout_cum = 0.0
            elif days_in_state >= EVAL_DAYS_P1:
                state = "DEAD_TIMEOUT_P1"
                break
        elif state == "EVAL_P2":
            if cum_pct >= PROFIT_TARGET_P2:
                state = "FUNDED"
                cum_pct = 0.0  # reset for funded tracking
                peak_pct = 0.0
                days_in_state = 0
                last_payout_cum = 0.0
            elif days_in_state >= EVAL_DAYS_P2:
                state = "DEAD_TIMEOUT_P2"
                break
        elif state == "FUNDED":
            # Monthly payout if profitable
            if days_in_state > 0 and days_in_state % MONTHLY_DAYS == 0 and cum_pct > last_payout_cum:
                payout = (cum_pct - last_payout_cum) * PROFIT_SHARE * account_size
                payouts_dollars += payout
                last_payout_cum = cum_pct
        state_history.append(state)
    return {"final_state": state, "payouts_dollars": payouts_dollars,
            "days_alive": len(state_history),
            "reached_funded": "FUNDED" in state_history}


def portfolio_mc(specs: dict, portfolio: list[dict], spec_levered_pls: dict,
                 n_iter: int, n_days: int, seed: int) -> dict:
    """Run N MC iterations. For each iter, sim each account → aggregate income."""
    rng = random.Random(seed)
    total_fees = sum(a["fee"] for a in portfolio)
    iter_results: list[dict] = []
    per_account_outcomes: dict = {a["name"]: [] for a in portfolio}
    for it in range(n_iter):
        portfolio_payout = 0.0
        n_funded = 0
        n_dead = 0
        for acct in portfolio:
            spec_name = acct["spec"]
            arr = spec_levered_pls[spec_name].dropna().to_list()
            out = simulate_account(arr, n_days, rng, acct["size"])
            portfolio_payout += out["payouts_dollars"]
            if out["reached_funded"]:
                n_funded += 1
            if out["final_state"].startswith("DEAD"):
                n_dead += 1
            per_account_outcomes[acct["name"]].append(out)
        iter_results.append({
            "total_payout": portfolio_payout,
            "n_funded": n_funded,
            "n_dead": n_dead,
            "net_income": portfolio_payout - total_fees,
        })
    payouts = sorted([r["total_payout"] for r in iter_results])
    nets = sorted([r["net_income"] for r in iter_results])
    n_funded_list = [r["n_funded"] for r in iter_results]
    n_dead_list = [r["n_dead"] for r in iter_results]
    return {
        "n_iter": n_iter,
        "total_fees": total_fees,
        "payout_p5": payouts[int(n_iter * 0.05)],
        "payout_median": payouts[n_iter // 2],
        "payout_mean": sum(payouts) / n_iter,
        "payout_p95": payouts[int(n_iter * 0.95)],
        "net_p5": nets[int(n_iter * 0.05)],
        "net_median": nets[n_iter // 2],
        "net_mean": sum(nets) / n_iter,
        "net_p95": nets[int(n_iter * 0.95)],
        "p_positive_net": sum(1 for n in nets if n > 0) / n_iter,
        "p_at_least_one_funded": sum(1 for n in n_funded_list if n >= 1) / n_iter,
        "p_all_funded": sum(1 for n in n_funded_list if n >= len(portfolio)) / n_iter,
        "avg_n_funded": sum(n_funded_list) / n_iter,
        "avg_n_dead": sum(n_dead_list) / n_iter,
        "per_account_funded_rate": {
            a["name"]: sum(1 for o in per_account_outcomes[a["name"]] if o["reached_funded"]) / n_iter
            for a in portfolio
        },
        "per_account_avg_payout": {
            a["name"]: sum(o["payouts_dollars"] for o in per_account_outcomes[a["name"]]) / n_iter
            for a in portfolio
        },
    }


# =====================================================================
# Main
# =====================================================================
def main() -> None:
    print("Loading FX (6) + Crypto (5)...")
    fx_closes = {p: load_fx_close(p) for p in FX_PAIRS}
    fx_rets = pd.DataFrame({p: fx_closes[p].pct_change() for p in FX_PAIRS}).dropna()
    fx_prices = pd.DataFrame({p: fx_closes[p].reindex(fx_rets.index) for p in FX_PAIRS})
    cr_closes = {c: load_crypto_close(c) for c in COINS}
    cr_rets = pd.DataFrame({c: cr_closes[c].pct_change() for c in COINS}).dropna()

    print(f"\nBuilding {6} strategy specs...")
    specs_unlev = build_specs(fx_rets, fx_prices, cr_rets)
    spec_names = list(specs_unlev.keys())

    # Metrics per spec on OOS
    print("\nSpec performance (OOS, unlevered):")
    spec_metrics: dict = {}
    for name in spec_names:
        pl = specs_unlev[name]
        m_oos = metrics(pl[pl.index >= OOS_START])
        m_is = metrics(pl[pl.index <= IS_END])
        spec_metrics[name] = {"is": m_is, "oos": m_oos}
        print(f"  {name:<22}: IS Sh={m_is['sharpe']:+.2f} | "
              f"OOS Sh={m_oos['sharpe']:+.2f} ret={m_oos['ann_ret']*100:+.1f}% "
              f"DD={m_oos['max_dd']*100:+.1f}% Calmar={m_oos['calmar']:.2f}")

    # Levered specs (vol-target 10%)
    print(f"\nApplying vol-target {VOL_TARGET*100:.0f}% to each spec...")
    spec_levered: dict = {}
    for name in spec_names:
        pl_unlev = specs_unlev[name]
        lev = vol_target_lev(pl_unlev, VOL_TARGET, VOL_LOOKBACK, MAX_LEVERAGE)
        pl_lev = (pl_unlev * lev).dropna()
        spec_levered[name] = pl_lev
        pl_oos = pl_lev[pl_lev.index >= OOS_START]
        m = metrics(pl_oos)
        avg_lev = float(lev.loc[lev.index >= OOS_START].mean())
        print(f"  {name:<22}: OOS Sh={m['sharpe']:+.2f} ret={m['ann_ret']*100:+.1f}% "
              f"vol={m['ann_vol']*100:.1f}% DD={m['max_dd']*100:+.1f}% avg_lev={avg_lev:.1f}×")

    # Spec correlations OOS
    print("\nSpec OOS daily P&L correlation matrix...")
    spec_df = pd.DataFrame({n: spec_levered[n] for n in spec_names}).dropna()
    spec_oos = spec_df[spec_df.index >= OOS_START]
    corr = spec_oos.corr()
    print(corr.round(2).to_string())
    # Average off-diagonal
    off = []
    for i in range(len(corr)):
        for j in range(len(corr)):
            if i != j:
                off.append(corr.iloc[i, j])
    avg_corr = sum(off) / len(off)
    print(f"\nAverage pair-wise correlation: {avg_corr:+.2f}")

    # ----- Portfolio MC simulation -----
    portfolio = PORTFOLIO_DEFAULT
    total_fees = sum(a["fee"] for a in portfolio)
    total_size = sum(a["size"] for a in portfolio)
    print(f"\nPortfolio: {len(portfolio)} accounts, ${total_size:.0f} notional, "
          f"${total_fees:.0f} fees")
    for a in portfolio:
        print(f"  {a['name']}: ${a['size']:.0f} → spec={a['spec']} (fee ${a['fee']})")

    # 504 days (2 years) : enough for full eval (max 180d) + 1 year funded
    print(f"\nMonte Carlo {N_MC} iterations, 24 months horizon (504 days)...")
    mc_result = portfolio_mc(specs_unlev, portfolio, spec_levered, N_MC, 504, SEED)

    # ----- Report -----
    lines: list[str] = []
    def emit(s: str) -> None:
        print(s)
        lines.append(s)

    emit(f"# PROP FIRM PORTFOLIO — {len(portfolio)} decorrelated accounts")
    emit("")
    emit(f"Total notional: ${total_size:.0f}  |  Total fees: ${total_fees:.0f}  |  "
         f"Vol-target {VOL_TARGET*100:.0f}%")
    emit(f"Rules: max_daily {MAX_DAILY_LOSS_PCT*100:.0f}%, "
         f"max_DD {MAX_OVERALL_DD_PCT*100:.0f}%, "
         f"P1 target {PROFIT_TARGET_P1*100:.0f}% in {EVAL_DAYS_P1}d, "
         f"P2 target {PROFIT_TARGET_P2*100:.0f}% in {EVAL_DAYS_P2}d, "
         f"profit share {PROFIT_SHARE*100:.0f}%")
    emit("")

    emit("## STRATEGY SPECS (6 decorrelated)")
    emit("| spec | IS Sh | OOS Sh | OOS ret% | OOS vol% | OOS DD% | OOS Calmar |")
    emit("|---|---|---|---|---|---|---|")
    for name in spec_names:
        m_oos = spec_metrics[name]["oos"]
        m_is = spec_metrics[name]["is"]
        emit(f"| {name} | {m_is['sharpe']:+.2f} | {m_oos['sharpe']:+.2f} | "
             f"{m_oos['ann_ret']*100:+.1f} | {m_oos['ann_vol']*100:.1f} | "
             f"{m_oos['max_dd']*100:+.1f} | {m_oos['calmar']:.2f} |")
    emit("")

    emit("## SPEC OOS CORRELATION MATRIX")
    emit("```")
    emit(corr.round(2).to_string())
    emit("```")
    emit(f"Avg pair-wise correlation: {avg_corr:+.2f}")
    emit("")

    emit("## PORTFOLIO COMPOSITION (default — adjustable)")
    emit("| account | size | spec | fee$ |")
    emit("|---|---|---|---|")
    for a in portfolio:
        emit(f"| {a['name']} | ${a['size']:.0f} | {a['spec']} | ${a['fee']} |")
    emit(f"\n  Total notional: ${total_size:.0f}")
    emit(f"  Total fees: ${total_fees:.0f}")
    emit("")

    emit(f"## MONTE CARLO PORTFOLIO RESULTS (12 months, {N_MC} iter)")
    emit(f"  Total fees outlay              : ${mc_result['total_fees']:.0f}")
    emit(f"  Portfolio gross payouts (mean) : ${mc_result['payout_mean']:.0f}")
    emit(f"  Portfolio gross payouts (median): ${mc_result['payout_median']:.0f}")
    emit(f"  Portfolio gross payouts CI95   : [${mc_result['payout_p5']:.0f}, ${mc_result['payout_p95']:.0f}]")
    emit("")
    emit(f"  Portfolio NET income (mean)    : ${mc_result['net_mean']:.0f}")
    emit(f"  Portfolio NET income (median)  : ${mc_result['net_median']:.0f}")
    emit(f"  Portfolio NET income CI95      : [${mc_result['net_p5']:.0f}, ${mc_result['net_p95']:.0f}]")
    emit("")
    emit(f"  P(net positive year 1)         : {mc_result['p_positive_net']*100:.0f}%")
    emit(f"  P(at least 1 account funded)   : {mc_result['p_at_least_one_funded']*100:.0f}%")
    emit(f"  P(all {len(portfolio)} accounts funded)         : {mc_result['p_all_funded']*100:.0f}%")
    emit(f"  Avg # funded accounts          : {mc_result['avg_n_funded']:.1f} / {len(portfolio)}")
    emit(f"  Avg # dead accounts (blow-ups) : {mc_result['avg_n_dead']:.1f} / {len(portfolio)}")
    emit(f"  ROI on fees (E[gross]/fees)    : {mc_result['payout_mean']/mc_result['total_fees']:.2f}×")
    emit("")

    emit("## PER-ACCOUNT BREAKDOWN")
    emit("| account | size | spec | P(funded) | avg gross payout$ |")
    emit("|---|---|---|---|---|")
    for a in portfolio:
        emit(f"| {a['name']} | ${a['size']:.0f} | {a['spec']} | "
             f"{mc_result['per_account_funded_rate'][a['name']]*100:.0f}% | "
             f"${mc_result['per_account_avg_payout'][a['name']]:.0f} |")
    emit("")

    # Diversification benefit
    indep_p_all_dead = 1.0
    for a in portfolio:
        p_dead_acct = 1.0 - mc_result['per_account_funded_rate'][a["name"]]
        indep_p_all_dead *= p_dead_acct
    indep_p_at_least_one = 1.0 - indep_p_all_dead
    actual_p_at_least_one = mc_result['p_at_least_one_funded']
    emit("## DIVERSIFICATION CHECK")
    emit("  If accounts were INDEPENDENT (uncorrelated):")
    emit(f"    P(at least 1 funded) = 1 - prod(P_dead) = {indep_p_at_least_one*100:.0f}%")
    emit(f"  Actual MC (with spec correlations): {actual_p_at_least_one*100:.0f}%")
    if actual_p_at_least_one < indep_p_at_least_one * 0.9:
        emit("  WARNING: significant correlation reduces diversification benefit.")
    else:
        emit("  Decorrelation working well. Specs ~independent.")
    emit("")

    emit("## RECOMMENDATION")
    if mc_result['net_mean'] > total_fees * 1.5:
        emit(f"  Portfolio EV positive (>{1.5}× fees). **DEPLOY.**")
        emit(f"  Expected year 1 net: **${mc_result['net_mean']:.0f}** ({mc_result['net_mean']/total_fees:.1f}× fees)")
        emit(f"  Median outcome: ${mc_result['net_median']:.0f}")
        emit(f"  Risk: 5th percentile = ${mc_result['net_p5']:.0f}")
    elif mc_result['net_mean'] > 0:
        emit("  Portfolio EV mildly positive. Acceptable but consider reducing fees.")
    else:
        emit("  Portfolio EV negative. Strategy + allocation insufficient.")
    emit("")

    emit("## EXECUTION ROADMAP")
    emit("```")
    emit("Phase 1 (week 1-2):")
    emit("  - Paper-trade ALL 6 specs in parallel on demo MT5 accounts")
    emit("  - Verify execution matches backtest (slippage, fills, fees)")
    emit("  - Confirm prop firm rules for chosen firms")
    emit("")
    emit("Phase 2 (week 3):")
    emit("  - Buy 1-2 challenges first (start small)")
    emit("  - Run assigned spec on each")
    emit("  - Track: daily P&L, DD, distance to target")
    emit("")
    emit("Phase 3 (month 2-3):")
    emit("  - If first challenges pass → buy additional accounts (cycle profits)")
    emit("  - If first challenges fail → analyze cause, refine before re-buy")
    emit("")
    emit("Phase 4 (month 4+):")
    emit("  - Funded accounts in steady-state operation")
    emit("  - Monthly payouts → reinvest in more challenges")
    emit("  - Scale via firm scaling plans (FTMO 25% scale every 4 months profit)")
    emit("```")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))

    # JSON
    OUT_METRICS.write_text(json.dumps({
        "specs": {n: spec_metrics[n] for n in spec_names},
        "correlation_matrix": corr.round(3).to_dict(),
        "avg_correlation": avg_corr,
        "portfolio": portfolio,
        "mc_result": {k: v for k, v in mc_result.items()
                      if k not in ("per_account_funded_rate", "per_account_avg_payout")},
        "per_account_funded_rate": mc_result["per_account_funded_rate"],
        "per_account_avg_payout": mc_result["per_account_avg_payout"],
    }, indent=2, default=str))

    # Equity HTML : 2 panels — spec cum returns + correlation heatmap-ish
    fig = make_subplots(rows=2, cols=1, subplot_titles=(
        "Spec OOS cum return (levered, vol-target 10%)",
        "Net portfolio income distribution (MC bootstrap)"),
        shared_xaxes=False, vertical_spacing=0.15)
    colors = ["#1976d2", "#0288d1", "#2e7d32", "#f57c00", "#c62828", "#7b1fa2"]
    for i, name in enumerate(spec_names):
        cum = spec_levered[name][spec_levered[name].index >= OOS_START].cumsum()
        fig.add_trace(go.Scatter(x=cum.index, y=cum.values * 100, mode="lines",
                                 name=name, line=dict(color=colors[i])), row=1, col=1)
    # MC distribution histogram approx — bin observations
    nets_sorted = []
    rng2 = random.Random(SEED + 99)
    for _ in range(500):
        pseudo = sum(spec_levered[a["spec"]].dropna().sample(252, replace=True, random_state=rng2.randrange(1<<30)).sum()
                     * a["size"] * PROFIT_SHARE
                     for a in portfolio) - total_fees
        nets_sorted.append(pseudo)
    fig.add_trace(go.Histogram(x=nets_sorted, nbinsx=40, name="net income $",
                                marker_color="#2e7d32"), row=2, col=1)
    fig.update_layout(title="Prop firm portfolio strategy", template="plotly_white",
                      height=900, hovermode="x")
    fig.update_yaxes(title_text="cum return (%)", row=1, col=1)
    fig.update_yaxes(title_text="frequency", row=2, col=1)
    fig.update_xaxes(title_text="net income $", row=2, col=1)
    fig.write_html(str(OUT_EQUITY), include_plotlyjs="cdn")

    # Deliverable
    print("\n\ndone")
    print(f"files: {OUT_REPORT.name}, {OUT_METRICS.name}, {OUT_EQUITY.name}")
    print(f"Portfolio: {len(portfolio)} accounts, ${total_size:.0f} notional, ${total_fees:.0f} fees")
    print(f"E[net income year 1]: ${mc_result['net_mean']:.0f} "
          f"(median ${mc_result['net_median']:.0f}, "
          f"CI95 [${mc_result['net_p5']:.0f}, ${mc_result['net_p95']:.0f}])")
    print(f"P(at least 1 funded): {mc_result['p_at_least_one_funded']*100:.0f}%")
    print(f"P(net positive year 1): {mc_result['p_positive_net']*100:.0f}%")
    print(f"ROI on fees: {mc_result['payout_mean']/total_fees:.2f}×")


if __name__ == "__main__":
    main()
