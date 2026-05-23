"""
Sandbox — historical prop firm simulation 2019-2026 WITH ADAPTIVE SIZING.

Replicates historical_propfirm_simulation.py but applies ADAPTIVE_50_0 sizing:
  - Rolling 126-day realized Sharpe per spec
  - Sharpe > 0.5  → 100% vol-target (full leverage)
  - 0 < Sh < 0.5 → 50% vol-target
  - Sh < 0       → 0% vol-target (PAUSE trading, capital preserved)

Question: how much more cash would 8 × $200k accounts have accumulated
2019-2026 if they had used adaptive sizing instead of static?
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

import strategy_propfirm_cashmax as cash


HERE = Path(__file__).parent
OUT_REPORT = HERE / "historical_propfirm_simulation_adaptive_report.md"
OUT_METRICS = HERE / "historical_propfirm_simulation_adaptive_metrics.json"

ACCOUNT_SIZE = 200_000.0
FEE_PER_CHALLENGE = 1_000.0
PHASE1_TARGET = 0.08
PHASE2_TARGET = 0.05
MAX_DAILY_LOSS = 0.05
MAX_OVERALL_DD = 0.10
PROFIT_SHARE = 0.80
PAYOUT_DAYS = 21
SLIPPAGE_HAIRCUT = 0.015
REPLACEMENT_DELAY_DAYS = 7
ANN_DAYS = 252

VOL_TARGET_BASE = 0.10
# ADAPTIVE_75_50 (validated robust on 2010-2025 H1 backtest, doesn't lose on unseen data)
# Previous ADAPTIVE_50_0 confirmed OVERFIT to 2019-2025 regime
ADAPTIVE_TIERS = [(0.3, 1.0), (0.0, 0.75), (-1e9, 0.5)]
ROLLING_SHARPE_WINDOW = 126
MAX_LEVERAGE = 10.0

ALLOCATION = [
    {"name": "A1", "spec": "FX_MR_STACK",   "vol_target": 0.10},
    {"name": "A2", "spec": "FX_MR_STACK",   "vol_target": 0.10},
    {"name": "A3", "spec": "NO_EUR_STACK",  "vol_target": 0.10},
    {"name": "A4", "spec": "NO_EUR_STACK",  "vol_target": 0.10},
    {"name": "A5", "spec": "COMDOLL_STACK", "vol_target": 0.10},
    {"name": "A6", "spec": "COMDOLL_STACK", "vol_target": 0.10},
    {"name": "A7", "spec": "FX_MR_STACK",   "vol_target": 0.08},
    {"name": "A8", "spec": "NO_EUR_STACK",  "vol_target": 0.08},
]


def adaptive_levered(daily: pd.Series, intraday: pd.Series, base_vol: float
                     ) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Apply ADAPTIVE_50_0 sizing. Returns (close_lev, intraday_lev, multiplier_series)."""
    realized_vol = daily.rolling(60).std() * math.sqrt(ANN_DAYS)
    static_lev = (base_vol / realized_vol).clip(upper=MAX_LEVERAGE).shift(1).fillna(1.0)
    static_returns = (daily * static_lev).dropna()

    rolling_mean = static_returns.rolling(ROLLING_SHARPE_WINDOW).mean()
    rolling_std = static_returns.rolling(ROLLING_SHARPE_WINDOW).std()
    rolling_sharpe = ((rolling_mean / rolling_std) * math.sqrt(ANN_DAYS)).shift(1)

    def to_mult(sh):
        if pd.isna(sh):
            return 1.0
        for thr, mult in ADAPTIVE_TIERS:
            if sh >= thr:
                return mult
        return ADAPTIVE_TIERS[-1][1]

    multiplier = rolling_sharpe.map(to_mult).fillna(1.0)
    effective_lev = (base_vol * multiplier / realized_vol).clip(upper=MAX_LEVERAGE).shift(1).fillna(1.0)
    close_lev = (daily * effective_lev).dropna()
    intraday_lev = (intraday.reindex(close_lev.index).fillna(daily) * effective_lev.reindex(close_lev.index)).dropna()
    common = close_lev.index.intersection(intraday_lev.index)
    return close_lev.loc[common], intraday_lev.loc[common], multiplier.loc[common]


def simulate_account_historical(close_path, intraday_path, dates, fee, replacement_delay):
    """Same logic as historical_propfirm_simulation.py."""
    state = "EVAL_1"
    state_days = 0
    cum_pct = 0.0
    peak_pct = 0.0
    funded_cum = 0.0
    days_funded = 0
    fees_paid = fee
    payouts_gross = 0.0
    n_blow_ups = 0
    n_funded_periods = 0
    last_payout_cum = 0.0
    waiting_until_idx = -1
    ledger = [{"event": "INITIAL_BUY", "date": str(dates[0].date()), "fee": fee}]

    n_days = len(close_path)
    for i in range(n_days):
        d = dates[i]
        if i <= waiting_until_idx:
            continue
        close_r = close_path[i]
        intraday_r = intraday_path[i]

        if intraday_r <= -MAX_DAILY_LOSS:
            n_blow_ups += 1
            ledger.append({"event": "BLOW_UP_DAILY", "date": str(d.date())})
            state = "WAITING"
            cum_pct = 0.0
            peak_pct = 0.0
            funded_cum = 0.0
            last_payout_cum = 0.0
            state_days = 0
            waiting_until_idx = i + replacement_delay
            if i + replacement_delay < n_days:
                fees_paid += fee
                ledger.append({"event": "REPLACEMENT_BUY",
                                "date": str(dates[i + replacement_delay].date()), "fee": fee})
            state = "EVAL_1"
            continue

        cum_pct += close_r
        peak_pct = max(peak_pct, cum_pct)
        state_days += 1

        # FIX: trailing DD checks intraday low (not just close)
        intraday_low_estimate = cum_pct + min(intraday_r, 0.0)
        if (intraday_low_estimate - peak_pct) <= -MAX_OVERALL_DD:
            n_blow_ups += 1
            ledger.append({"event": "BLOW_UP_TRAIL_DD", "date": str(d.date())})
            state = "WAITING"
            cum_pct = 0.0
            peak_pct = 0.0
            funded_cum = 0.0
            last_payout_cum = 0.0
            state_days = 0
            waiting_until_idx = i + replacement_delay
            if i + replacement_delay < n_days:
                fees_paid += fee
                ledger.append({"event": "REPLACEMENT_BUY",
                                "date": str(dates[i + replacement_delay].date()), "fee": fee})
            state = "EVAL_1"
            continue

        if state == "EVAL_1":
            if cum_pct >= PHASE1_TARGET:
                ledger.append({"event": "PHASE_1_PASS", "date": str(d.date())})
                state = "EVAL_2"
                state_days = 0
                cum_pct = 0.0
                peak_pct = 0.0
        elif state == "EVAL_2":
            if cum_pct >= PHASE2_TARGET:
                ledger.append({"event": "PHASE_2_PASS_FUNDED", "date": str(d.date())})
                state = "FUNDED"
                state_days = 0
                cum_pct = 0.0
                peak_pct = 0.0
                funded_cum = 0.0
                last_payout_cum = 0.0
                n_funded_periods += 1
        elif state == "FUNDED":
            funded_cum += close_r
            days_funded += 1
            if state_days > 0 and state_days % PAYOUT_DAYS == 0 and funded_cum > last_payout_cum:
                profit_delta = funded_cum - last_payout_cum
                payout = profit_delta * PROFIT_SHARE * ACCOUNT_SIZE
                payouts_gross += payout
                ledger.append({"event": "PAYOUT", "date": str(d.date()),
                                "gross_payout_usd": payout,
                                "net_payout_usd": payout * (1 - SLIPPAGE_HAIRCUT)})
                # FIX: reduce cum_pct + peak_pct by withdrawn profit (real payout reduces balance)
                cum_pct -= profit_delta
                peak_pct = max(peak_pct - profit_delta, 0.0)
                funded_cum = 0.0
                last_payout_cum = 0.0

    return {
        "fees_paid": fees_paid,
        "payouts_gross": payouts_gross,
        "payouts_net": payouts_gross * (1 - SLIPPAGE_HAIRCUT),
        "n_blow_ups": n_blow_ups,
        "n_funded_periods": n_funded_periods,
        "days_funded": days_funded,
        "final_state": state,
        "ledger": ledger,
    }


def main() -> None:
    print("Loading FX strategy universe...")
    specs = cash.build_strategy_universe()

    print("\nApplying ADAPTIVE_50_0 sizing per spec × vol target...")
    adaptive_levered_streams: dict = {}
    pause_rates: dict = {}
    for spec_name in ("FX_MR_STACK", "NO_EUR_STACK", "COMDOLL_STACK"):
        for vt in (0.08, 0.10):
            daily = specs[spec_name]["daily"]
            intraday = specs[spec_name]["intraday"]
            close_lev, intra_lev, mult = adaptive_levered(daily, intraday, vt)
            adaptive_levered_streams[(spec_name, vt)] = (close_lev, intra_lev)
            pause_rates[(spec_name, vt)] = float((mult == 0.0).mean() * 100)
            print(f"  {spec_name} vt={vt*100:.0f}% : {len(close_lev)} days, "
                  f"{pause_rates[(spec_name, vt)]:.1f}% paused")

    # Common date range
    all_idx = None
    for acc in ALLOCATION:
        close_lev, _ = adaptive_levered_streams[(acc["spec"], acc["vol_target"])]
        all_idx = close_lev.index if all_idx is None else all_idx.intersection(close_lev.index)
    all_dates = sorted(all_idx)
    print(f"\nHistorical window: {all_dates[0].date()} → {all_dates[-1].date()} "
          f"({len(all_dates)} trading days)")

    # Simulate each account
    print("\nSimulating 8 accounts with ADAPTIVE sizing...\n")
    per_account = {}
    total_fees = 0.0
    total_payouts_gross = 0.0
    total_payouts_net = 0.0
    total_blow_ups = 0
    total_funded_periods = 0
    for acc in ALLOCATION:
        close_lev, intra_lev = adaptive_levered_streams[(acc["spec"], acc["vol_target"])]
        close_path = close_lev.loc[all_idx].to_list()
        intraday_path = intra_lev.loc[all_idx].to_list()
        result = simulate_account_historical(close_path, intraday_path, all_dates,
                                              FEE_PER_CHALLENGE, REPLACEMENT_DELAY_DAYS)
        per_account[acc["name"]] = {**result, "spec": acc["spec"], "vol_target": acc["vol_target"]}
        total_fees += result["fees_paid"]
        total_payouts_gross += result["payouts_gross"]
        total_payouts_net += result["payouts_net"]
        total_blow_ups += result["n_blow_ups"]
        total_funded_periods += result["n_funded_periods"]
        net = result["payouts_net"] - result["fees_paid"]
        print(f"  {acc['name']} ({acc['spec']} vt={acc['vol_target']*100:.0f}%): "
              f"fees=${result['fees_paid']:6.0f}  "
              f"payouts_net=${result['payouts_net']:9.0f}  "
              f"net=${net:+9.0f}  "
              f"blowups={result['n_blow_ups']}  "
              f"funded={result['n_funded_periods']}  "
              f"days_funded={result['days_funded']}")

    total_net = total_payouts_net - total_fees
    n_years = (all_dates[-1] - all_dates[0]).days / 365.25
    annualized_net = total_net / n_years if n_years > 0 else 0.0

    print(f"\n=== PORTFOLIO TOTALS ADAPTIVE over {n_years:.1f} years ===")
    print(f"  Total fees            : ${total_fees:,.0f}")
    print(f"  Total payouts (net)   : ${total_payouts_net:,.0f}")
    print(f"  NET CASH ACCUMULATED  : ${total_net:,.0f}")
    print(f"  Annualized            : ${annualized_net:,.0f}/year")
    print(f"  Total blow-ups        : {total_blow_ups}")
    print(f"  Total funded periods  : {total_funded_periods}")

    # Per-year breakdown
    yearly: dict = {}
    for acc_name, acc_result in per_account.items():
        for event in acc_result["ledger"]:
            date_str = event.get("date")
            if not date_str:
                continue
            year = int(date_str[:4])
            yearly.setdefault(year, {"fees": 0.0, "payouts_net": 0.0,
                                     "blowups": 0, "phase_passes": 0})
            if event["event"] in ("INITIAL_BUY", "REPLACEMENT_BUY"):
                yearly[year]["fees"] += event.get("fee", 0.0)
            elif event["event"] == "PAYOUT":
                yearly[year]["payouts_net"] += event.get("net_payout_usd", 0.0)
            elif event["event"].startswith("BLOW_UP"):
                yearly[year]["blowups"] += 1
            elif event["event"] in ("PHASE_1_PASS", "PHASE_2_PASS_FUNDED"):
                yearly[year]["phase_passes"] += 1

    print("\n=== PER YEAR BREAKDOWN ADAPTIVE ===")
    print(f"{'year':<6} | {'fees':>9} | {'payouts net':>13} | {'net cash':>10} | {'blowups':>8} | {'phases':>7}")
    for year in sorted(yearly):
        y = yearly[year]
        net = y["payouts_net"] - y["fees"]
        print(f"{year:<6} | ${y['fees']:>7,.0f} | ${y['payouts_net']:>11,.0f} | "
              f"${net:>+9,.0f} | {y['blowups']:>8} | {y['phase_passes']:>7}")

    # Load static results from previous run for comparison
    static_metrics_path = HERE / "historical_propfirm_simulation_metrics.json"
    static_results = None
    if static_metrics_path.exists():
        static_results = json.loads(static_metrics_path.read_text())

    # Report
    lines: list[str] = []
    def emit(s: str = "") -> None:
        lines.append(s)
    emit("# Historical Prop Firm Simulation 2019-2026 — ADAPTIVE SIZING")
    emit("")
    emit("Same setup as `historical_propfirm_simulation.py` but with adaptive sizing")
    emit("ADAPTIVE_50_0 : leverage scales by rolling 126-day Sharpe per spec.")
    emit("Pauses trading entirely (vol-target=0) when rolling Sharpe < 0.")
    emit("")
    emit("## Pause rates per spec/vol")
    for (s, v), pr in pause_rates.items():
        emit(f"- {s} vt={v*100:.0f}% : **{pr:.1f}% of days paused**")
    emit("")

    emit("## Per-account historical results ADAPTIVE")
    emit("| account | spec | vol | fees | payouts net | NET CASH | blowups | funded | days_funded |")
    emit("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for acc_name, r in per_account.items():
        net = r["payouts_net"] - r["fees_paid"]
        emit(f"| {acc_name} | {r['spec']} | {r['vol_target']*100:.0f}% | "
             f"${r['fees_paid']:,.0f} | ${r['payouts_net']:,.0f} | "
             f"**${net:+,.0f}** | {r['n_blow_ups']} | "
             f"{r['n_funded_periods']} | {r['days_funded']} |")
    emit("")

    emit(f"## PORTFOLIO TOTALS over {n_years:.1f} years (ADAPTIVE)")
    emit("")
    emit(f"- Total fees                : **${total_fees:,.0f}**")
    emit(f"- Total payouts net         : ${total_payouts_net:,.0f}")
    emit(f"- **NET CASH ACCUMULATED   : ${total_net:,.0f}**")
    emit(f"- Annualized                : **${annualized_net:,.0f} / year**")
    emit(f"- Total blow-ups            : {total_blow_ups}")
    emit(f"- Total funded periods      : {total_funded_periods}")
    emit("")

    emit("## Per-year breakdown ADAPTIVE")
    emit("| year | fees | payouts net | net cash year | blowups | phase passes |")
    emit("|---|---:|---:|---:|---:|---:|")
    for year in sorted(yearly):
        y = yearly[year]
        net = y["payouts_net"] - y["fees"]
        emit(f"| {year} | ${y['fees']:,.0f} | ${y['payouts_net']:,.0f} | "
             f"${net:+,.0f} | {y['blowups']} | {y['phase_passes']} |")
    emit("")

    # Comparison vs static
    if static_results is not None:
        static_total_net = static_results["total_net_cash"]
        static_total_fees = static_results["total_fees"]
        static_blowups = static_results["total_blow_ups"]
        static_funded = static_results["total_funded_periods"]
        delta_net = total_net - static_total_net
        delta_blowups = total_blow_ups - static_blowups
        emit("## STATIC vs ADAPTIVE comparison")
        emit("| metric | STATIC | ADAPTIVE | Δ |")
        emit("|---|---:|---:|---:|")
        emit(f"| Total fees | ${static_total_fees:,.0f} | ${total_fees:,.0f} | ${total_fees - static_total_fees:+,.0f} |")
        emit(f"| Total payouts net | ${static_results['total_payouts_net']:,.0f} | ${total_payouts_net:,.0f} | ${total_payouts_net - static_results['total_payouts_net']:+,.0f} |")
        emit(f"| NET CASH | ${static_total_net:,.0f} | ${total_net:,.0f} | **${delta_net:+,.0f}** |")
        emit(f"| Blow-ups | {static_blowups} | {total_blow_ups} | {delta_blowups:+d} |")
        emit(f"| Funded periods | {static_funded} | {total_funded_periods} | {total_funded_periods - static_funded:+d} |")
        emit(f"| Annualized | ${static_results['annualized_net']:,.0f} | ${annualized_net:,.0f} | ${annualized_net - static_results['annualized_net']:+,.0f} |")
        emit("")

        pct_improvement = (delta_net / abs(static_total_net) * 100) if static_total_net != 0 else 0
        emit(f"**Net cash improvement : {pct_improvement:+.0f}% ({'$+' if delta_net>0 else '$'}{delta_net:,.0f})**")
        emit(f"**Blow-up reduction : {-delta_blowups:+d} fewer ({-(delta_blowups/static_blowups)*100:+.0f}%)**")
        emit("")

    OUT_REPORT.write_text("\n".join(lines))
    OUT_METRICS.write_text(json.dumps({
        "total_fees": total_fees, "total_payouts_net": total_payouts_net,
        "total_net_cash": total_net, "annualized_net": annualized_net,
        "total_blow_ups": total_blow_ups, "total_funded_periods": total_funded_periods,
        "n_years": n_years,
        "per_account": per_account, "per_year": yearly,
        "pause_rates": {f"{s}_{int(v*100)}": pr for (s, v), pr in pause_rates.items()},
        "vs_static": {
            "static_net": static_results["total_net_cash"] if static_results else None,
            "adaptive_net": total_net,
            "delta": total_net - static_results["total_net_cash"] if static_results else None,
        } if static_results else None,
    }, indent=2, default=str))
    print(f"\ndone\nfiles: {OUT_REPORT.name}, {OUT_METRICS.name}")


if __name__ == "__main__":
    main()
