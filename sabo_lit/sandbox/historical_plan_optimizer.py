"""
Sandbox — historical prop-firm plan optimizer.

Question: given the same strategy streams, which prop-firm account plan and
portfolio shape would have produced the most actual cash from 2019-2025?

This is deterministic historical replay, not bootstrap:
  - uses real 2019-01-02 → 2025-12-31 FX returns
  - models phase targets, daily/overall loss, monthly payouts, fee refunds
  - replaces blown accounts after 7 trading days

It complements `strategy_propfirm_cashmax.py`:
  - cashmax = OOS bootstrap expectation from 2024-2025
  - this file = actual path through bad + good regimes from 2019-2025
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import strategy_propfirm_cashmax as cash


HERE = Path(__file__).parent
OUT_REPORT = HERE / "historical_plan_optimizer_report.md"
OUT_METRICS = HERE / "historical_plan_optimizer_metrics.json"

REPLACEMENT_DELAY_DAYS = 7
PAYOUT_DAYS = 21


def build_levered_streams(portfolios: dict[str, list[cash.AccountPlan]]) -> dict[tuple[str, float], tuple[pd.Series, pd.Series]]:
    specs = cash.build_strategy_universe()
    needed: set[tuple[str, float]] = set()
    for portfolio in portfolios.values():
        for account in portfolio:
            needed.add((account.spec, account.vol_target))
            needed.add((account.spec, account.funded_vol_target or account.vol_target))

    levered = {}
    for spec_name, vol_target in sorted(needed):
        daily = specs[spec_name]["daily"]
        intraday = specs[spec_name]["intraday"]
        close_lev, intraday_lev = cash.vol_target_returns(daily, intraday, vol_target)
        common = close_lev.index.intersection(intraday_lev.index)
        levered[(spec_name, vol_target)] = (close_lev.loc[common], intraday_lev.loc[common])
    return levered


def reset_after_blowup(
    ledger: list[dict],
    dates: list[pd.Timestamp],
    day_idx: int,
    plan: cash.FirmPlan,
) -> tuple[int, float]:
    waiting_until_idx = day_idx + REPLACEMENT_DELAY_DAYS
    fee = 0.0
    if waiting_until_idx < len(dates):
        fee = plan.fee
        ledger.append({
            "event": "REPLACEMENT_BUY",
            "date": str(dates[waiting_until_idx].date()),
            "fee": plan.fee,
        })
    return waiting_until_idx, fee


def simulate_account_historical(
    account: cash.AccountPlan,
    plan: cash.FirmPlan,
    close_eval: pd.Series,
    intraday_eval: pd.Series,
    close_funded: pd.Series,
    intraday_funded: pd.Series,
    dates: list[pd.Timestamp],
) -> dict:
    state_idx = 0
    state = "EVAL_1"
    state_days = 0
    cum_pct = 0.0
    peak_pct = 0.0
    funded_cum = 0.0
    payouts = 0.0
    fees_paid = plan.fee
    n_blowups = 0
    n_funded_periods = 0
    days_funded = 0
    first_payout_done = False
    waiting_until_idx = -1
    ledger = [{
        "event": "INITIAL_BUY",
        "date": str(dates[0].date()),
        "fee": plan.fee,
    }]

    for day_idx, date in enumerate(dates):
        if day_idx <= waiting_until_idx:
            continue

        funded = state == "FUNDED"
        close_raw = close_funded.loc[date] if funded else close_eval.loc[date]
        intraday_raw = intraday_funded.loc[date] if funded else intraday_eval.loc[date]
        close_r, intraday_r = cash.apply_eval_daily_cap(close_raw, intraday_raw, plan, funded)

        if intraday_r <= -plan.max_daily_loss:
            n_blowups += 1
            ledger.append({
                "event": "BLOW_UP_DAILY",
                "date": str(date.date()),
                "state": state,
                "cum_pct": cum_pct,
                "intraday_pct": intraday_r,
            })
            waiting_until_idx, fee = reset_after_blowup(ledger, dates, day_idx, plan)
            fees_paid += fee
            state_idx = 0
            state = "EVAL_1"
            state_days = 0
            cum_pct = 0.0
            peak_pct = 0.0
            funded_cum = 0.0
            continue

        if cash.breaches_overall(cum_pct, intraday_r, peak_pct, plan):
            n_blowups += 1
            ledger.append({
                "event": "BLOW_UP_OVERALL",
                "date": str(date.date()),
                "state": state,
                "cum_pct": cum_pct,
                "peak_pct": peak_pct,
                "intraday_pct": intraday_r,
            })
            waiting_until_idx, fee = reset_after_blowup(ledger, dates, day_idx, plan)
            fees_paid += fee
            state_idx = 0
            state = "EVAL_1"
            state_days = 0
            cum_pct = 0.0
            peak_pct = 0.0
            funded_cum = 0.0
            continue

        cum_pct += close_r
        peak_pct = max(peak_pct, cum_pct)
        state_days += 1

        if state.startswith("EVAL"):
            target = plan.targets[state_idx]
            if cum_pct >= target:
                ledger.append({
                    "event": f"PHASE_{state_idx + 1}_PASS",
                    "date": str(date.date()),
                    "days_in_state": state_days,
                    "cum_pct": cum_pct,
                })
                state_idx += 1
                state_days = 0
                cum_pct = 0.0
                peak_pct = 0.0
                if state_idx == len(plan.targets):
                    state = "FUNDED"
                    n_funded_periods += 1
                    funded_cum = 0.0
                    ledger[-1]["event"] = "PHASE_FINAL_PASS_FUNDED"
                else:
                    state = f"EVAL_{state_idx + 1}"
        else:
            funded_cum += close_r
            days_funded += 1
            if state_days > 0 and state_days % PAYOUT_DAYS == 0 and funded_cum > 0.0:
                payout = funded_cum * plan.profit_share * plan.account_size
                payouts += payout
                ledger.append({
                    "event": "PAYOUT",
                    "date": str(date.date()),
                    "gross_payout_usd": payout,
                    "funded_cum_pct": funded_cum,
                })
                cum_pct -= funded_cum
                funded_cum = 0.0
                if plan.fee_refund_after_first_payout and not first_payout_done:
                    payouts += plan.fee
                    ledger.append({
                        "event": "FEE_REFUND",
                        "date": str(date.date()),
                        "gross_payout_usd": plan.fee,
                    })
                    first_payout_done = True

    return {
        "account": account.__dict__,
        "firm_plan": plan.__dict__,
        "fees_paid": fees_paid,
        "payouts": payouts,
        "net_cash": payouts - fees_paid,
        "n_blowups": n_blowups,
        "n_funded_periods": n_funded_periods,
        "days_funded": days_funded,
        "ledger": ledger,
    }


def simulate_portfolio(
    portfolio: list[cash.AccountPlan],
    levered: dict[tuple[str, float], tuple[pd.Series, pd.Series]],
) -> dict:
    indices = []
    for account in portfolio:
        eval_close, eval_intraday = levered[(account.spec, account.vol_target)]
        funded_target = account.funded_vol_target or account.vol_target
        funded_close, funded_intraday = levered[(account.spec, funded_target)]
        indices.extend([eval_close.index, eval_intraday.index, funded_close.index, funded_intraday.index])
    common_idx = sorted(set.intersection(*(set(index) for index in indices)))
    dates = list(common_idx)

    per_account = {}
    totals = {"fees": 0.0, "payouts": 0.0, "net_cash": 0.0, "blowups": 0, "funded_periods": 0}
    yearly: dict[int, dict[str, float]] = {}
    for account in portfolio:
        plan = cash.FIRM_PLANS[account.firm]
        funded_target = account.funded_vol_target or account.vol_target
        eval_close, eval_intraday = levered[(account.spec, account.vol_target)]
        funded_close, funded_intraday = levered[(account.spec, funded_target)]
        result = simulate_account_historical(
            account,
            plan,
            eval_close.reindex(dates),
            eval_intraday.reindex(dates),
            funded_close.reindex(dates),
            funded_intraday.reindex(dates),
            dates,
        )
        per_account[account.name] = result
        totals["fees"] += result["fees_paid"]
        totals["payouts"] += result["payouts"]
        totals["net_cash"] += result["net_cash"]
        totals["blowups"] += result["n_blowups"]
        totals["funded_periods"] += result["n_funded_periods"]

        for event in result["ledger"]:
            year = int(event["date"][:4])
            yearly.setdefault(year, {"fees": 0.0, "payouts": 0.0, "blowups": 0.0, "phase_passes": 0.0})
            if event["event"] in {"INITIAL_BUY", "REPLACEMENT_BUY"}:
                yearly[year]["fees"] += event.get("fee", 0.0)
            elif event["event"] in {"PAYOUT", "FEE_REFUND"}:
                yearly[year]["payouts"] += event.get("gross_payout_usd", 0.0)
            elif event["event"].startswith("BLOW_UP"):
                yearly[year]["blowups"] += 1
            elif "PASS" in event["event"]:
                yearly[year]["phase_passes"] += 1

    n_years = (dates[-1] - dates[0]).days / 365.25
    yearly_net = {year: values["payouts"] - values["fees"] for year, values in yearly.items()}
    cumulative = 0.0
    min_cumulative = 0.0
    first_positive_year = None
    for year in sorted(yearly_net):
        cumulative += yearly_net[year]
        min_cumulative = min(min_cumulative, cumulative)
        if first_positive_year is None and cumulative > 0:
            first_positive_year = year

    return {
        "totals": {**totals, "annualized_net": totals["net_cash"] / n_years},
        "per_account": per_account,
        "per_year": yearly,
        "yearly_net": yearly_net,
        "min_cumulative_net": min_cumulative,
        "first_positive_year": first_positive_year,
        "n_years": n_years,
        "start": str(dates[0].date()),
        "end": str(dates[-1].date()),
    }


def compact_account_rows(portfolio_result: dict) -> list[dict]:
    rows = []
    for account_name, result in portfolio_result["per_account"].items():
        account = result["account"]
        rows.append({
            "account": account_name,
            "firm": account["firm"],
            "spec": account["spec"],
            "vol": account["vol_target"],
            "funded_vol": account.get("funded_vol_target") or account["vol_target"],
            "fees": result["fees_paid"],
            "payouts": result["payouts"],
            "net_cash": result["net_cash"],
            "blowups": result["n_blowups"],
            "funded_periods": result["n_funded_periods"],
        })
    return rows


def main() -> None:
    portfolios = cash.CANDIDATE_PORTFOLIOS
    levered = build_levered_streams(portfolios)

    print("\nHistorical plan replay:")
    results = {}
    for name, portfolio in portfolios.items():
        result = simulate_portfolio(portfolio, levered)
        results[name] = result
        totals = result["totals"]
        print(
            f"  {name:<32} net=${totals['net_cash']:>9,.0f} "
            f"ann=${totals['annualized_net']:>8,.0f}/y fees=${totals['fees']:>7,.0f} "
            f"blowups={totals['blowups']:>3.0f} min_cum=${result['min_cumulative_net']:>9,.0f} "
            f"first_pos={result['first_positive_year']}"
        )

    best_name = max(results, key=lambda key: results[key]["totals"]["net_cash"])
    best = results[best_name]

    lines: list[str] = []

    def emit(line: str = "") -> None:
        lines.append(line)

    emit("# Historical Prop-Firm Plan Optimizer")
    emit("")
    emit("Deterministic replay on real 2019-2025 FX returns. Replaces blown accounts after")
    emit(f"{REPLACEMENT_DELAY_DAYS} trading days and withdraws funded profits every {PAYOUT_DAYS} trading days.")
    emit("")
    emit("## Portfolio comparison")
    emit("| portfolio | fees | payouts | net cash | annualized | blow-ups | min cumulative | first positive year |")
    emit("|---|---:|---:|---:|---:|---:|---:|---:|")
    for name, result in sorted(results.items(), key=lambda item: item[1]["totals"]["net_cash"], reverse=True):
        totals = result["totals"]
        first_pos = "" if result["first_positive_year"] is None else str(result["first_positive_year"])
        emit(
            f"| {name} | ${totals['fees']:,.0f} | ${totals['payouts']:,.0f} | "
            f"**${totals['net_cash']:+,.0f}** | ${totals['annualized_net']:,.0f}/y | "
            f"{totals['blowups']:.0f} | ${result['min_cumulative_net']:,.0f} | {first_pos} |"
        )
    emit("")

    emit(f"## Best historical portfolio: {best_name}")
    emit("")
    emit("| account | firm | spec | eval vol | funded vol | fees | payouts | net | blow-ups | funded periods |")
    emit("|---|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for row in compact_account_rows(best):
        emit(
            f"| {row['account']} | {row['firm']} | {row['spec']} | "
            f"{row['vol']*100:.0f}% | {row['funded_vol']*100:.0f}% | "
            f"${row['fees']:,.0f} | ${row['payouts']:,.0f} | **${row['net_cash']:+,.0f}** | "
            f"{row['blowups']} | {row['funded_periods']} |"
        )
    emit("")

    emit("## Best portfolio per-year cash")
    emit("| year | fees | payouts | net | blow-ups | phase passes |")
    emit("|---|---:|---:|---:|---:|---:|")
    for year in sorted(best["per_year"]):
        values = best["per_year"][year]
        net = values["payouts"] - values["fees"]
        emit(
            f"| {year} | ${values['fees']:,.0f} | ${values['payouts']:,.0f} | "
            f"${net:+,.0f} | {values['blowups']:.0f} | {values['phase_passes']:.0f} |"
        )
    emit("")

    emit("## Interpretation")
    emit("- The fastest path is low-target 5%/5% style plans when the firm offers static drawdown and fee refund.")
    emit("- Large 500k trailing accounts can maximize gross cash but add rule fragility; size them lower than 200k static accounts.")
    emit("- The historical optimum is path-dependent and should not be treated as a guaranteed forward allocation.")
    emit("- Use this as a plan-selection filter; live firm rules still need exact calibration before buying challenges.")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))
    OUT_METRICS.write_text(json.dumps({
        "recommended": best_name,
        "results": results,
    }, indent=2, default=str))
    print(f"\nWrote {OUT_REPORT}")
    print(f"Wrote {OUT_METRICS}")
    print(f"Best portfolio: {best_name}")


if __name__ == "__main__":
    main()
