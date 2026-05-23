"""
Sandbox — multi-firm prop allocation with realistic per-firm caps.

Critique of codex hybrid_blend_8x200 : assumes 8 accounts at 1 firm = $1.6M
allocation at single firm. Most firms cap allocation per trader/passport:
  - FTMO        : $400k max ($600k with scaling)
  - FundedNext  : $400k max
  - MyForexFunds (defunct) : $600k
  - Maven       : $300k max
  - FundingPips : $200k base, $400k scaled
  - The5ers     : $20k base, scaling 4x
  - E8 Funding  : $400k max

Realistic deployment requires 4-5 different firms with at most 2 accounts each.
Each firm has its own rule quirks (trailing vs static DD, news rules, payout cycle).

This script reruns the portfolio MC with:
  - 4 firm templates (FTMO_SWING, MFF_LIKE_STATIC, FUNDED_NEXT_TRAIL, FUNDINGPIPS_FAST)
  - Max 2 accounts per firm
  - Per-firm cost (some have monthly recurring, some one-time)
  - Friction additive: 1.5% slippage haircut to gross payouts (live realistic)
  - Only PROBABLE/ROBUST validated specs allowed (drop FRAGILE H1 picks)

Validated specs allowed:
  - FX_MR_STACK       (FX MR baseline, OOS Sh 1.51)
  - EURUSD_MR5        (FX MR single-pair, OOS Sh 1.16)
  - NO_EUR_STACK      (FX MR without EURUSD, OOS Sh 1.39)
  - COMDOLL_STACK     (FX MR AUD/NZD/CAD, OOS Sh 1.27)
  - H1_CHFJPY_BREAK   (only H1 pick that passed PROBABLE in validation)

DROPPED specs:
  - H1_NASDAQ_TREND  (FRAGILE per validate_codex_picks)
  - H1_DOW_TSM       (FRAGILE per validate_codex_picks)
  - HYBRID_FX_H1     (depends on 2 FRAGILE H1 components)
"""
from __future__ import annotations

import json
import random
import zlib
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

import strategy_h1_edge_miner as h1
import strategy_propfirm_cashmax as cash


HERE = Path(__file__).parent
OUT_REPORT = HERE / "strategy_propfirm_multifirm_report.md"
OUT_METRICS = HERE / "strategy_propfirm_multifirm_metrics.json"

SLIPPAGE_HAIRCUT = 0.015  # 1.5% deduction from gross payouts (live execution friction)
N_MC = 5000
HORIZON_DAYS = 504
SEED = 20260522


@dataclass(frozen=True)
class FirmPlan:
    name: str
    account_size: float
    fee: float
    targets: tuple[float, ...]
    max_daily_loss: float
    max_overall_loss: float
    profit_share: float
    overall_mode: str  # 'static' or 'eod_trailing'
    daily_profit_cap_eval: float | None = None
    fee_refund_after_first_payout: bool = False
    max_accounts_per_trader: int = 2


FIRM_PLANS_REAL = {
    "FTMO_SWING_200K": FirmPlan(
        name="FTMO_SWING_200K", account_size=200_000, fee=1_080,
        targets=(0.10, 0.05),
        max_daily_loss=0.05, max_overall_loss=0.10,
        profit_share=0.80, overall_mode="eod_trailing",
        max_accounts_per_trader=2,
    ),
    "MFF_LIKE_STATIC_200K": FirmPlan(
        name="MFF_LIKE_STATIC_200K", account_size=200_000, fee=900,
        targets=(0.05, 0.05),
        max_daily_loss=0.05, max_overall_loss=0.10,
        profit_share=0.80, overall_mode="static",
        daily_profit_cap_eval=0.02,
        fee_refund_after_first_payout=True,
        max_accounts_per_trader=2,
    ),
    "FUNDED_NEXT_TRAIL_200K": FirmPlan(
        name="FUNDED_NEXT_TRAIL_200K", account_size=200_000, fee=1_000,
        targets=(0.08, 0.05),
        max_daily_loss=0.05, max_overall_loss=0.10,
        profit_share=0.85, overall_mode="eod_trailing",
        fee_refund_after_first_payout=True,
        max_accounts_per_trader=2,
    ),
    "FUNDINGPIPS_FAST_200K": FirmPlan(
        name="FUNDINGPIPS_FAST_200K", account_size=200_000, fee=850,
        targets=(0.08, 0.05),
        max_daily_loss=0.05, max_overall_loss=0.10,
        profit_share=0.80, overall_mode="static",
        max_accounts_per_trader=2,
    ),
}


# EURUSD_MR5 dropped to PROBABLE after held-out validation: held-out Q4 2025 Sh=-1.65.
# Kept for legacy reference but DOWNSIZED in any allocation using it.
VALIDATED_ROBUST = ["FX_MR_STACK", "NO_EUR_STACK", "COMDOLL_STACK"]
VALIDATED_PROBABLE = ["EURUSD_MR5"]
VALIDATED_SPECS = VALIDATED_ROBUST + VALIDATED_PROBABLE


@dataclass(frozen=True)
class AccountPlan:
    name: str
    firm_template: str
    spec: str
    vol_target: float


# Realistic allocations
# After held-out validation: EURUSD_MR5 dropped from 2 accounts (was PROBABLE only).
# Replaced with FX_MR_STACK / NO_EUR_STACK / COMDOLL_STACK (all ROBUST).
# Legacy variants with EURUSD_MR5 kept for A/B comparison.
CANDIDATE_PORTFOLIOS_REAL = {
    "concentrated_safe_4x200_2firms_robust_only": [
        AccountPlan("A1", "MFF_LIKE_STATIC_200K", "FX_MR_STACK", 0.10),
        AccountPlan("A2", "MFF_LIKE_STATIC_200K", "NO_EUR_STACK", 0.10),
        AccountPlan("A3", "FUNDINGPIPS_FAST_200K", "COMDOLL_STACK", 0.10),
        AccountPlan("A4", "FUNDINGPIPS_FAST_200K", "FX_MR_STACK", 0.10),
    ],
    "diversified_8x200_4firms_robust_only": [
        AccountPlan("A1", "MFF_LIKE_STATIC_200K", "FX_MR_STACK", 0.10),
        AccountPlan("A2", "MFF_LIKE_STATIC_200K", "NO_EUR_STACK", 0.10),
        AccountPlan("A3", "FUNDINGPIPS_FAST_200K", "NO_EUR_STACK", 0.10),
        AccountPlan("A4", "FUNDINGPIPS_FAST_200K", "COMDOLL_STACK", 0.10),
        AccountPlan("A5", "FUNDED_NEXT_TRAIL_200K", "FX_MR_STACK", 0.08),
        AccountPlan("A6", "FUNDED_NEXT_TRAIL_200K", "COMDOLL_STACK", 0.08),
        AccountPlan("A7", "FTMO_SWING_200K", "NO_EUR_STACK", 0.08),
        AccountPlan("A8", "FTMO_SWING_200K", "COMDOLL_STACK", 0.08),
    ],
    "diversified_8x200_4firms_with_eurusd_legacy": [
        AccountPlan("A1", "MFF_LIKE_STATIC_200K", "FX_MR_STACK", 0.10),
        AccountPlan("A2", "MFF_LIKE_STATIC_200K", "EURUSD_MR5", 0.05),  # PROBABLE → half-size
        AccountPlan("A3", "FUNDINGPIPS_FAST_200K", "NO_EUR_STACK", 0.10),
        AccountPlan("A4", "FUNDINGPIPS_FAST_200K", "COMDOLL_STACK", 0.10),
        AccountPlan("A5", "FUNDED_NEXT_TRAIL_200K", "FX_MR_STACK", 0.08),
        AccountPlan("A6", "FUNDED_NEXT_TRAIL_200K", "EURUSD_MR5", 0.04),  # half-size
        AccountPlan("A7", "FTMO_SWING_200K", "NO_EUR_STACK", 0.08),
        AccountPlan("A8", "FTMO_SWING_200K", "COMDOLL_STACK", 0.08),
    ],
    "minimal_test_2x200_2firms": [
        AccountPlan("A1", "MFF_LIKE_STATIC_200K", "FX_MR_STACK", 0.10),
        AccountPlan("A2", "FUNDINGPIPS_FAST_200K", "NO_EUR_STACK", 0.10),
    ],
    "fast_eval_6x200_3firms_robust_only": [
        AccountPlan("A1", "MFF_LIKE_STATIC_200K", "FX_MR_STACK", 0.10),
        AccountPlan("A2", "MFF_LIKE_STATIC_200K", "NO_EUR_STACK", 0.10),
        AccountPlan("A3", "FUNDINGPIPS_FAST_200K", "COMDOLL_STACK", 0.10),
        AccountPlan("A4", "FUNDINGPIPS_FAST_200K", "FX_MR_STACK", 0.10),
        AccountPlan("A5", "FUNDED_NEXT_TRAIL_200K", "NO_EUR_STACK", 0.08),
        AccountPlan("A6", "FUNDED_NEXT_TRAIL_200K", "COMDOLL_STACK", 0.08),
    ],
}


def validate_firm_caps(portfolio: list[AccountPlan]) -> tuple[bool, list[str]]:
    counts: dict = {}
    errors = []
    for acct in portfolio:
        counts[acct.firm_template] = counts.get(acct.firm_template, 0) + 1
    for firm, count in counts.items():
        cap = FIRM_PLANS_REAL[firm].max_accounts_per_trader
        if count > cap:
            errors.append(f"{firm}: {count} accounts > cap {cap}")
    return len(errors) == 0, errors


def stable_seed(label: str) -> int:
    return SEED + zlib.crc32(label.encode("utf-8")) % 100_000


def simulate_account_haircut(
    close_path: list[float],
    intraday_path: list[float],
    plan: FirmPlan,
) -> dict:
    """Reuse cash module simulator + apply slippage haircut to payouts."""
    out = cash.simulate_account_on_path(close_path, intraday_path,
                                          _convert_to_cash_plan(plan),
                                          plan.account_size)
    out["payouts"] = out["payouts"] * (1.0 - SLIPPAGE_HAIRCUT)
    return out


def _convert_to_cash_plan(plan: FirmPlan) -> cash.FirmPlan:
    """Convert FirmPlan to cash.FirmPlan (compatible fields)."""
    return cash.FirmPlan(
        name=plan.name, account_size=plan.account_size, fee=plan.fee,
        targets=plan.targets, max_daily_loss=plan.max_daily_loss,
        max_overall_loss=plan.max_overall_loss, profit_share=plan.profit_share,
        overall_mode=plan.overall_mode,
        daily_profit_cap_eval=plan.daily_profit_cap_eval,
        fee_refund_after_first_payout=plan.fee_refund_after_first_payout,
    )


def portfolio_mc(
    portfolio: list[AccountPlan],
    levered: dict[tuple[str, float], tuple[pd.Series, pd.Series]],
    n_iter: int, horizon_days: int, seed: int,
) -> dict:
    rng = random.Random(seed)
    all_indices = sorted(
        set.intersection(*[
            set(levered[(account.spec, account.vol_target)][0].index)
            for account in portfolio
        ])
    )
    idx_count = len(all_indices)
    prepared = {}
    for account in portfolio:
        close, intraday = levered[(account.spec, account.vol_target)]
        close = close.reindex(all_indices)
        intraday = intraday.reindex(all_indices)
        prepared[account.name] = (close.to_list(), intraday.to_list())

    total_fees = sum(FIRM_PLANS_REAL[acct.firm_template].fee for acct in portfolio)
    outcomes = []
    for _ in range(n_iter):
        picks = [rng.randrange(idx_count) for _ in range(horizon_days)]
        total_payout = 0.0
        funded_count = 0
        dead_count = 0
        for account in portfolio:
            plan = FIRM_PLANS_REAL[account.firm_template]
            close_values, intraday_values = prepared[account.name]
            close_path = [close_values[idx] for idx in picks]
            intraday_path = [intraday_values[idx] for idx in picks]
            result = simulate_account_haircut(close_path, intraday_path, plan)
            total_payout += result["payouts"]
            funded_count += int(result["reached_funded"])
            dead_count += int(result["final_state"].startswith("DEAD"))
        outcomes.append({
            "gross": total_payout, "net": total_payout - total_fees,
            "funded_count": funded_count, "dead_count": dead_count,
        })
    gross = sorted(o["gross"] for o in outcomes)
    net = sorted(o["net"] for o in outcomes)
    return {
        "fees": total_fees,
        "gross_mean": sum(gross) / n_iter, "gross_median": gross[n_iter // 2],
        "gross_p05": gross[int(n_iter * 0.05)], "gross_p95": gross[int(n_iter * 0.95)],
        "net_mean": sum(net) / n_iter, "net_median": net[n_iter // 2],
        "net_p05": net[int(n_iter * 0.05)], "net_p95": net[int(n_iter * 0.95)],
        "p_net_positive": sum(n > 0 for n in net) / n_iter,
        "p_at_least_one_funded": sum(o["funded_count"] > 0 for o in outcomes) / n_iter,
        "avg_funded": sum(o["funded_count"] for o in outcomes) / n_iter,
        "avg_dead": sum(o["dead_count"] for o in outcomes) / n_iter,
    }


def main() -> None:
    print("Loading strategy universe from cash module...")
    specs = cash.build_strategy_universe()
    # Restrict to validated specs only
    specs = {name: specs[name] for name in VALIDATED_SPECS if name in specs}
    print(f"Validated specs: {list(specs)}")

    print("\nApplying vol-target sizing...")
    vol_targets = [0.04, 0.05, 0.08, 0.10, 0.12]  # extra low vals for half-size PROBABLE specs
    levered = {}
    levered_metrics = {}
    for name, values in specs.items():
        for tv in vol_targets:
            close_lev, intraday_lev = cash.vol_target_returns(values["daily"], values["intraday"], tv)
            oos = close_lev[close_lev.index >= cash.OOS_START]
            oos_intraday = intraday_lev.reindex(oos.index)
            levered[(name, tv)] = (oos, oos_intraday)
            m = cash.metrics(oos)
            m["worst_intraday"] = float(oos_intraday.min())
            levered_metrics[f"{name}_{int(tv * 100)}"] = m

    # Validate cap compliance
    print("\nFirm cap validation:")
    for name, portfolio in CANDIDATE_PORTFOLIOS_REAL.items():
        ok, errs = validate_firm_caps(portfolio)
        status = "OK" if ok else f"FAIL: {errs}"
        print(f"  {name}: {status}")

    # Run MC for each portfolio
    print(f"\nRunning Monte Carlo {N_MC} paths × {HORIZON_DAYS} days × haircut {SLIPPAGE_HAIRCUT*100:.1f}%...")
    portfolio_results = {}
    for name, portfolio in CANDIDATE_PORTFOLIOS_REAL.items():
        result = portfolio_mc(portfolio, levered, N_MC, HORIZON_DAYS, stable_seed(name))
        portfolio_results[name] = result
        print(f"  {name:<38} fees=${result['fees']:5.0f}  "
              f"E[net]=${result['net_mean']:8.0f}  "
              f"med=${result['net_median']:8.0f}  "
              f"p+={result['p_net_positive']*100:3.0f}%  "
              f"funded={result['avg_funded']:.1f}/{len(CANDIDATE_PORTFOLIOS_REAL[name])}")

    best = max(portfolio_results, key=lambda n: portfolio_results[n]["net_mean"])

    # Compare to original codex hybrid_blend_8x200 if available
    try:
        prev_metrics = json.loads(
            (HERE / "strategy_propfirm_hybrid_h1_metrics.json").read_text())
        prev_best = prev_metrics["portfolio_results"]["hybrid_blend_8x200"]
        prev_net = prev_best["net_mean"]
    except Exception:
        prev_net = None

    # Report
    lines: list[str] = []
    def emit(s: str = "") -> None:
        lines.append(s)

    emit("# Prop Firm Multi-Firm Realistic Allocation")
    emit("")
    emit("Critique-corrected version of codex hybrid_blend_8x200:")
    emit("- **Multi-firm constraint** : max 2 accounts per firm")
    emit("- **Slippage haircut** : -1.5% applied to gross payouts (live friction)")
    emit("- **Dropped FRAGILE H1 picks** : per `validate_codex_picks.py` verdict")
    emit("- **Validated specs only** : FX_MR_STACK + EURUSD_MR5 + NO_EUR_STACK + COMDOLL_STACK")
    emit("")

    emit("## Firm templates modeled")
    emit("| firm | account | fee | targets | DD mode | profit share | max accts/trader |")
    emit("|---|---:|---:|---|---|---:|---:|")
    for name, plan in FIRM_PLANS_REAL.items():
        targets_str = " / ".join(f"{t*100:.0f}%" for t in plan.targets)
        emit(f"| {name} | ${plan.account_size:.0f} | ${plan.fee} | {targets_str} | "
             f"{plan.overall_mode} | {plan.profit_share*100:.0f}% | "
             f"{plan.max_accounts_per_trader} |")
    emit("")

    emit("## Validated specs (OOS levered metrics)")
    emit("| spec @ vol | Sharpe | ret% | DD% | worst intra% |")
    emit("|---|---:|---:|---:|---:|")
    for key, m in sorted(levered_metrics.items()):
        emit(f"| {key} | {m['sharpe']:+.2f} | {m['ann_ret']*100:+.1f} | "
             f"{m['max_dd']*100:+.1f} | {m['worst_intraday']*100:+.2f} |")
    emit("")

    emit("## Portfolio candidates")
    emit("| portfolio | accts | fees | E[gross] | E[net] | median net | P(net+) | P(≥1 fund) | avg fund | p95 net |")
    emit("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name, r in sorted(portfolio_results.items(), key=lambda x: x[1]["net_mean"], reverse=True):
        n_accts = len(CANDIDATE_PORTFOLIOS_REAL[name])
        emit(f"| {name} | {n_accts} | ${r['fees']:.0f} | ${r['gross_mean']:.0f} | "
             f"${r['net_mean']:.0f} | ${r['net_median']:.0f} | "
             f"{r['p_net_positive']*100:.0f}% | {r['p_at_least_one_funded']*100:.0f}% | "
             f"{r['avg_funded']:.1f} | ${r['net_p95']:.0f} |")
    emit("")

    emit(f"## Best realistic portfolio: **{best}**")
    best_r = portfolio_results[best]
    emit(f"- Total fees    : ${best_r['fees']:.0f}")
    emit(f"- E[gross]      : ${best_r['gross_mean']:.0f}")
    emit(f"- E[net]        : ${best_r['net_mean']:.0f}")
    emit(f"- Median net    : ${best_r['net_median']:.0f}")
    emit(f"- CI95 net      : [${best_r['net_p05']:.0f}, ${best_r['net_p95']:.0f}]")
    emit(f"- P(net+)       : {best_r['p_net_positive']*100:.0f}%")
    emit(f"- P(≥1 funded)  : {best_r['p_at_least_one_funded']*100:.0f}%")
    emit(f"- Avg funded    : {best_r['avg_funded']:.1f} / {len(CANDIDATE_PORTFOLIOS_REAL[best])}")
    emit(f"- Avg blown     : {best_r['avg_dead']:.1f} / {len(CANDIDATE_PORTFOLIOS_REAL[best])}")
    emit("")
    emit("Composition:")
    for acct in CANDIDATE_PORTFOLIOS_REAL[best]:
        plan = FIRM_PLANS_REAL[acct.firm_template]
        emit(f"- {acct.name}: {acct.firm_template} ${plan.account_size:.0f}, "
             f"{acct.spec}, vol_target={acct.vol_target*100:.0f}%")
    emit("")

    if prev_net is not None:
        delta = best_r["net_mean"] - prev_net
        delta_pct = delta / prev_net * 100 if prev_net != 0 else 0
        emit("## Comparison vs codex hybrid_blend_8x200")
        emit(f"- Codex original E[net] : ${prev_net:.0f}")
        emit(f"- Realistic E[net]      : ${best_r['net_mean']:.0f}")
        emit(f"- Delta                 : ${delta:+.0f} ({delta_pct:+.0f}%)")
        emit("")
        emit("Sources of delta:")
        emit("- Drop FRAGILE H1 picks (per validation)")
        emit("- 1.5% slippage haircut")
        emit("- Multi-firm constraint (different rule mixes per firm)")
        emit("")

    emit("## Deployment recommendation")
    emit("")
    emit("**This realistic estimate replaces codex hybrid_blend_8x200 as the operational target.**")
    emit("")
    emit("Buy challenges sequentially, not in parallel:")
    emit(f"1. Start with {CANDIDATE_PORTFOLIOS_REAL[best][0].firm_template} → {CANDIDATE_PORTFOLIOS_REAL[best][0].spec}")
    emit("2. After Phase 1 pass, buy next account at SAME firm if cap allows")
    emit("3. After full cap at firm 1, move to firm 2")
    emit("4. Repeat across 3-4 different firms over 6-12 months")
    emit("")
    emit("Forward verification protocol:")
    emit("- First 30 days demo paper-trade with full signal pipeline (news gate + delta orders)")
    emit("- First real challenge : risk only 1 buy ($600-1100)")
    emit("- If Phase 1 passes within 60 days → buy 2nd account")
    emit("- If Phase 1 fails → re-evaluate signal vs realized P&L distribution")
    emit("")

    OUT_REPORT.write_text("\n".join(lines))
    OUT_METRICS.write_text(json.dumps({
        "firm_plans": {n: p.__dict__ for n, p in FIRM_PLANS_REAL.items()},
        "validated_specs": VALIDATED_SPECS,
        "levered_metrics": levered_metrics,
        "portfolio_results": portfolio_results,
        "best": best,
        "best_composition": [a.__dict__ for a in CANDIDATE_PORTFOLIOS_REAL[best]],
        "slippage_haircut": SLIPPAGE_HAIRCUT,
        "compared_to_codex_hybrid_8x200": {
            "codex_net_mean": prev_net,
            "realistic_net_mean": best_r["net_mean"],
            "delta": best_r["net_mean"] - prev_net if prev_net else None,
        } if prev_net else None,
    }, indent=2, default=str))

    print("\n\ndone")
    print(f"files: {OUT_REPORT.name}, {OUT_METRICS.name}")
    print(f"best portfolio: {best}")
    print(f"E[net]: ${best_r['net_mean']:.0f} (vs codex hybrid {prev_net or 'n/a'})")
    print(f"P(net+): {best_r['p_net_positive']*100:.0f}%")
    # silence unused import warning
    _ = h1


if __name__ == "__main__":
    main()
