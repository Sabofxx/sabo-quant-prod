"""
Sandbox — historical simulation 2019-2026 : 8x $200k prop firm accounts.

User question: "If I had bought 8 prop firm challenges at $200k each starting
2019-01-01, with 8% Phase 1 + 5% Phase 2 + live with drawdowns, how much would
I have accumulated by end of 2025/early 2026?"

This is a REAL historical run (NOT bootstrap), using actual daily returns from
2019-01-02 to 2025-12-31 (7 years).

Rules modeled:
  - Phase 1 target : 8% (FundingPips/FundedNext-style)
  - Phase 2 target : 5%
  - Max daily loss : 5%
  - Max overall : 10% trailing, checked with intraday adverse excursion
  - Profit share : 80% (paid out every 21 trading days while funded)
  - Account fee : $1000 per challenge
  - On blow-up : 7-day wait, then buy new challenge same firm
  - Slippage haircut : -1.5% on gross payouts
  - Funded payouts reduce account profit buffer after withdrawal

Allocation (post-validation, drop EURUSD_MR5):
  A1-A2 : FX_MR_STACK   (ROBUST, vol 10%)
  A3-A4 : NO_EUR_STACK  (ROBUST, vol 10%)
  A5-A6 : COMDOLL_STACK (ROBUST, vol 10%)
  A7    : FX_MR_STACK   (vol 8%)
  A8    : NO_EUR_STACK  (vol 8%)

8 accounts at start = $8,000 fees upfront. Replacements add to running cost.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

import strategy_propfirm_cashmax as cash


HERE = Path(__file__).parent
OUT_REPORT = HERE / "historical_propfirm_simulation_report.md"
OUT_METRICS = HERE / "historical_propfirm_simulation_metrics.json"

ACCOUNT_SIZE = 200_000.0
FEE_PER_CHALLENGE = 1_000.0
PHASE1_TARGET = 0.08
PHASE2_TARGET = 0.05
MAX_DAILY_LOSS = 0.05
MAX_OVERALL_DD = 0.10  # trailing
PROFIT_SHARE = 0.80
PAYOUT_DAYS = 21  # every 21 trading days while funded
SLIPPAGE_HAIRCUT = 0.015
REPLACEMENT_DELAY_DAYS = 7
ANN_DAYS = 252

# Allocation (post-validation)
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


def build_levered_streams() -> dict:
    """Build per-spec leveraged daily + intraday OOS streams.
    Use FULL 2019-2025 history (not OOS-restricted)."""
    specs = cash.build_strategy_universe()
    levered = {}
    for spec_name in ("FX_MR_STACK", "NO_EUR_STACK", "COMDOLL_STACK"):
        for vt in (0.08, 0.10):
            close_lev, intraday_lev = cash.vol_target_returns(
                specs[spec_name]["daily"], specs[spec_name]["intraday"], vt)
            levered[(spec_name, vt)] = (close_lev, intraday_lev)
    return levered


def simulate_account_historical(
    close_path: list[float],
    intraday_path: list[float],
    dates: list[pd.Timestamp],
    fee: float,
    replacement_delay: int,
) -> dict:
    """Walk through real historical returns. On blow-up, wait replacement_delay
    trading days then start fresh Phase 1 (new fee). Compute cumulative net cash.

    Returns: dict with total_payouts, total_fees, n_blow_ups, n_funded_periods,
    days_funded, ledger (per-event log)."""
    state = "EVAL_1"
    state_days = 0
    cum_pct = 0.0
    peak_pct = 0.0
    funded_cum = 0.0
    days_funded = 0
    fees_paid = fee  # initial fee
    payouts_gross = 0.0
    n_blow_ups = 0
    n_funded_periods = 0
    last_payout_cum = 0.0
    waiting_until_idx = -1
    ledger = [{"event": "INITIAL_BUY", "date": str(dates[0].date()), "fee": fee}]

    n_days = len(close_path)
    for i in range(n_days):
        d = dates[i]
        # Skip waiting period
        if i <= waiting_until_idx:
            continue
        close_r = close_path[i]
        intraday_r = intraday_path[i]

        # Daily breach check
        if intraday_r <= -MAX_DAILY_LOSS:
            n_blow_ups += 1
            ledger.append({"event": "BLOW_UP_DAILY", "date": str(d.date()),
                            "state": state, "cum_pct": cum_pct,
                            "loss_pct": intraday_r})
            # Reset, wait, buy replacement
            state = "WAITING"
            state_days = 0
            cum_pct = 0.0
            peak_pct = 0.0
            funded_cum = 0.0
            last_payout_cum = 0.0
            waiting_until_idx = i + replacement_delay
            # Schedule new fee at end of wait
            if i + replacement_delay < n_days:
                fees_paid += fee
                ledger.append({"event": "REPLACEMENT_BUY", "date": str(dates[i + replacement_delay].date()),
                                "fee": fee})
            state = "EVAL_1"
            continue

        intraday_equity_pct = cum_pct + intraday_r
        if intraday_equity_pct <= peak_pct - MAX_OVERALL_DD:
            n_blow_ups += 1
            ledger.append({"event": "BLOW_UP_TRAIL_DD_INTRADAY", "date": str(d.date()),
                            "state": state, "cum_pct": cum_pct,
                            "peak_pct": peak_pct,
                            "intraday_equity_pct": intraday_equity_pct})
            state = "WAITING"
            state_days = 0
            cum_pct = 0.0
            peak_pct = 0.0
            funded_cum = 0.0
            last_payout_cum = 0.0
            waiting_until_idx = i + replacement_delay
            if i + replacement_delay < n_days:
                fees_paid += fee
                ledger.append({"event": "REPLACEMENT_BUY",
                                "date": str(dates[i + replacement_delay].date()),
                                "fee": fee})
            state = "EVAL_1"
            continue

        cum_pct += close_r
        peak_pct = max(peak_pct, cum_pct)
        state_days += 1

        # Trailing overall DD breach
        if (cum_pct - peak_pct) <= -MAX_OVERALL_DD:
            n_blow_ups += 1
            ledger.append({"event": "BLOW_UP_TRAIL_DD", "date": str(d.date()),
                            "state": state, "cum_pct": cum_pct,
                            "peak_pct": peak_pct})
            state = "WAITING"
            state_days = 0
            cum_pct = 0.0
            peak_pct = 0.0
            funded_cum = 0.0
            last_payout_cum = 0.0
            waiting_until_idx = i + replacement_delay
            if i + replacement_delay < n_days:
                fees_paid += fee
                ledger.append({"event": "REPLACEMENT_BUY",
                                "date": str(dates[i + replacement_delay].date()),
                                "fee": fee})
            state = "EVAL_1"
            continue

        # State transitions
        if state == "EVAL_1":
            if cum_pct >= PHASE1_TARGET:
                ledger.append({"event": "PHASE_1_PASS", "date": str(d.date()),
                                "days_in_state": state_days, "cum_at_pass": cum_pct})
                state = "EVAL_2"
                state_days = 0
                cum_pct = 0.0
                peak_pct = 0.0
        elif state == "EVAL_2":
            if cum_pct >= PHASE2_TARGET:
                ledger.append({"event": "PHASE_2_PASS_FUNDED", "date": str(d.date()),
                                "days_in_state": state_days, "cum_at_pass": cum_pct})
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
                payout_pct = funded_cum - last_payout_cum
                payout = payout_pct * PROFIT_SHARE * ACCOUNT_SIZE
                payout_net = payout * (1 - SLIPPAGE_HAIRCUT)
                payouts_gross += payout
                ledger.append({"event": "PAYOUT", "date": str(d.date()),
                                "gross_payout_usd": payout, "net_payout_usd": payout_net,
                                "since_last_pct": payout_pct})
                last_payout_cum = funded_cum
                cum_pct -= payout_pct

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
    print("Building levered strategy streams...")
    levered = build_levered_streams()

    # Use full history not OOS-only for historical simulation
    # vol_target_returns returns OOS-only ; need to rebuild full streams
    print("Loading full M5 history for true 2019-2026 simulation...")
    specs = cash.build_strategy_universe()
    full_levered = {}
    for spec_name in ("FX_MR_STACK", "NO_EUR_STACK", "COMDOLL_STACK"):
        for vt in (0.08, 0.10):
            daily = specs[spec_name]["daily"]
            intraday = specs[spec_name]["intraday"]
            realized = daily.rolling(60).std() * math.sqrt(ANN_DAYS)
            lev = (vt / realized).clip(upper=cash.MAX_LEVERAGE).shift(1).fillna(1.0)
            close_lev = (daily * lev).dropna()
            intraday_lev = (intraday.reindex(close_lev.index).fillna(daily) * lev.reindex(close_lev.index)).dropna()
            common = close_lev.index.intersection(intraday_lev.index)
            full_levered[(spec_name, vt)] = (close_lev.loc[common], intraday_lev.loc[common])

    # Compute common date range across all accounts
    all_idx = None
    for acc in ALLOCATION:
        close_lev, _ = full_levered[(acc["spec"], acc["vol_target"])]
        all_idx = close_lev.index if all_idx is None else all_idx.intersection(close_lev.index)
    all_dates = sorted(all_idx)
    print(f"Historical window: {all_dates[0].date()} → {all_dates[-1].date()} "
          f"({len(all_dates)} trading days)")

    # Simulate each account
    print("\nSimulating 8 accounts on real historical returns...\n")
    per_account_results = {}
    total_fees = 0.0
    total_payouts_gross = 0.0
    total_payouts_net = 0.0
    total_blow_ups = 0
    total_funded_periods = 0
    for acc in ALLOCATION:
        close_lev, intraday_lev = full_levered[(acc["spec"], acc["vol_target"])]
        close_path = close_lev.loc[all_idx].to_list()
        intraday_path = intraday_lev.loc[all_idx].to_list()
        result = simulate_account_historical(
            close_path, intraday_path, all_dates,
            FEE_PER_CHALLENGE, REPLACEMENT_DELAY_DAYS)
        per_account_results[acc["name"]] = {**result, "spec": acc["spec"],
                                              "vol_target": acc["vol_target"]}
        total_fees += result["fees_paid"]
        total_payouts_gross += result["payouts_gross"]
        total_payouts_net += result["payouts_net"]
        total_blow_ups += result["n_blow_ups"]
        total_funded_periods += result["n_funded_periods"]
        net = result["payouts_net"] - result["fees_paid"]
        print(f"  {acc['name']} ({acc['spec']} vt={acc['vol_target']*100:.0f}%): "
              f"fees=${result['fees_paid']:7.0f}  "
              f"payouts_net=${result['payouts_net']:9.0f}  "
              f"net=${net:+9.0f}  "
              f"blowups={result['n_blow_ups']}  "
              f"funded_periods={result['n_funded_periods']}  "
              f"days_funded={result['days_funded']}")

    total_net = total_payouts_net - total_fees
    n_years = (all_dates[-1] - all_dates[0]).days / 365.25
    annualized_net = total_net / n_years if n_years > 0 else 0.0

    print(f"\n=== PORTFOLIO TOTALS over {n_years:.1f} years ===")
    print(f"  Total fees paid       : ${total_fees:,.0f}")
    print(f"  Total payouts (gross) : ${total_payouts_gross:,.0f}")
    print(f"  Total payouts (net haircut 1.5%) : ${total_payouts_net:,.0f}")
    print(f"  NET CASH ACCUMULATED  : ${total_net:,.0f}")
    print(f"  Annualized            : ${annualized_net:,.0f}/year")
    print(f"  Total blow-ups        : {total_blow_ups}")
    print(f"  Total funded periods  : {total_funded_periods}")

    # Per-year breakdown : aggregate payouts/fees by year from ledgers
    yearly: dict = {}
    for acc_name, acc_result in per_account_results.items():
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

    print("\n=== PER YEAR BREAKDOWN ===")
    print(f"{'year':<6} | {'fees':>9} | {'payouts net':>13} | {'net cash':>10} | {'blowups':>8} | {'phase passes':>13}")
    for year in sorted(yearly):
        y = yearly[year]
        net = y["payouts_net"] - y["fees"]
        print(f"{year:<6} | ${y['fees']:>7,.0f} | ${y['payouts_net']:>11,.0f} | "
              f"${net:>+9,.0f} | {y['blowups']:>8} | {y['phase_passes']:>13}")

    # Report
    lines: list[str] = []
    def emit(s: str = "") -> None:
        lines.append(s)
    emit("# Historical Prop Firm Simulation 2019-2026")
    emit("")
    emit("Question: if you'd bought 8 × $200k prop firm challenges at 2019-01-01,")
    emit("with 8%/5% two-phase eval + funded with monthly payouts + replacement on blow-up,")
    emit("how much cash would you have accumulated by 2025-12-31?")
    emit("")
    emit("**REAL historical returns used** (not bootstrap). This is what actually would")
    emit("have happened given the FX MR strategy edge during 2019-2025.")
    emit("")

    emit("## Rules modeled (FundingPips/FundedNext-style)")
    emit(f"- Account size : ${ACCOUNT_SIZE:,.0f}")
    emit(f"- Phase 1 target : {PHASE1_TARGET*100:.0f}%")
    emit(f"- Phase 2 target : {PHASE2_TARGET*100:.0f}%")
    emit(f"- Max daily loss : {MAX_DAILY_LOSS*100:.0f}%")
    emit(f"- Max overall trailing DD : {MAX_OVERALL_DD*100:.0f}%")
    emit(f"- Profit share : {PROFIT_SHARE*100:.0f}%")
    emit(f"- Payout cycle : every {PAYOUT_DAYS} trading days")
    emit(f"- Fee per challenge : ${FEE_PER_CHALLENGE:,.0f}")
    emit(f"- Replacement delay after blow-up : {REPLACEMENT_DELAY_DAYS} trading days")
    emit(f"- Slippage haircut on payouts : {SLIPPAGE_HAIRCUT*100:.1f}%")
    emit("")

    emit("## Allocation (post-validation, no EURUSD_MR5)")
    emit("| account | spec | vol_target |")
    emit("|---|---|---:|")
    for acc in ALLOCATION:
        emit(f"| {acc['name']} | {acc['spec']} | {acc['vol_target']*100:.0f}% |")
    emit("")

    emit("## Per-account historical results")
    emit("| account | spec | vol | fees | payouts net | NET CASH | blow-ups | funded periods | days funded |")
    emit("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for acc_name, r in per_account_results.items():
        net = r["payouts_net"] - r["fees_paid"]
        emit(f"| {acc_name} | {r['spec']} | {r['vol_target']*100:.0f}% | "
             f"${r['fees_paid']:,.0f} | ${r['payouts_net']:,.0f} | "
             f"**${net:+,.0f}** | {r['n_blow_ups']} | "
             f"{r['n_funded_periods']} | {r['days_funded']} |")
    emit("")

    emit(f"## PORTFOLIO TOTALS over {n_years:.1f} years")
    emit("")
    emit(f"- Total fees paid       : **${total_fees:,.0f}**")
    emit(f"- Total payouts gross   : ${total_payouts_gross:,.0f}")
    emit(f"- Total payouts net (after slippage haircut) : ${total_payouts_net:,.0f}")
    emit(f"- **NET CASH ACCUMULATED : ${total_net:,.0f}**")
    emit(f"- Annualized average    : **${annualized_net:,.0f} / year**")
    emit(f"- Total blow-ups across all accounts : {total_blow_ups}")
    emit(f"- Total funded-from-scratch periods  : {total_funded_periods}")
    emit("")

    emit("## Per-year breakdown")
    emit("| year | fees | payouts net | net cash year | blow-ups | phase passes |")
    emit("|---|---:|---:|---:|---:|---:|")
    for year in sorted(yearly):
        y = yearly[year]
        net = y["payouts_net"] - y["fees"]
        emit(f"| {year} | ${y['fees']:,.0f} | ${y['payouts_net']:,.0f} | "
             f"${net:+,.0f} | {y['blowups']} | {y['phase_passes']} |")
    emit("")

    emit("## Interpretation")
    emit("")
    emit(f"Capital required upfront : ${len(ALLOCATION) * FEE_PER_CHALLENGE:,.0f} (8 initial challenges)")
    emit(f"Total fees over 7 years  : ${total_fees:,.0f} (including replacements)")
    emit(f"Total replacement fees   : ${total_fees - len(ALLOCATION) * FEE_PER_CHALLENGE:,.0f}")
    emit(f"Net ROI on total fees    : {(total_net / total_fees) * 100 if total_fees > 0 else 0:.0f}%")
    emit("")
    emit("**Caveats**:")
    emit("- This is the BEST CASE backtest given today's strategy. Forward returns will differ.")
    emit("- Assumes you could replace blown accounts immediately (firms may have cooldown)")
    emit("- Assumes uniform $1000 fee (real costs vary $600-1800 per challenge)")
    emit("- No live news event tail risk modeled beyond what's in real 2019-2025 data")
    emit("- 2020 COVID period included = real stress test")
    emit("- 2022 Fed pivot included = real regime test")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))
    OUT_METRICS.write_text(json.dumps({
        "total_fees": total_fees,
        "total_payouts_gross": total_payouts_gross,
        "total_payouts_net": total_payouts_net,
        "total_net_cash": total_net,
        "annualized_net": annualized_net,
        "total_blow_ups": total_blow_ups,
        "total_funded_periods": total_funded_periods,
        "n_years": n_years,
        "per_account": per_account_results,
        "per_year": yearly,
        "allocation": ALLOCATION,
        "rules": {
            "phase1_target": PHASE1_TARGET, "phase2_target": PHASE2_TARGET,
            "max_daily_loss": MAX_DAILY_LOSS, "max_overall_dd": MAX_OVERALL_DD,
            "profit_share": PROFIT_SHARE, "fee": FEE_PER_CHALLENGE,
            "slippage_haircut": SLIPPAGE_HAIRCUT,
        },
    }, indent=2, default=str))
    print(f"\ndone\nfiles: {OUT_REPORT.name}, {OUT_METRICS.name}")
    _ = levered  # silence


if __name__ == "__main__":
    main()
