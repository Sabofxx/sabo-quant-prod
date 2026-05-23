"""
Audit prop-firm operational rules against the ADAPTIVE_75_50 FX stack.

The goal is not to re-optimize the edge. It is to catch firm-specific rules
that can break an otherwise valid strategy: inactivity, minimum trading days,
profitable-day gates, payout gates, and stricter drawdown models.

Outputs:
  - sandbox/firm_rule_audit_report.md
  - sandbox/firm_rule_audit_metrics.json
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import pandas as pd

import strategy_propfirm_cashmax as cash


HERE = Path(__file__).parent
OUT_REPORT = HERE / "firm_rule_audit_report.md"
OUT_METRICS = HERE / "firm_rule_audit_metrics.json"

ANN_DAYS = 252
VOL_LOOKBACK = 60
ADAPTIVE_WINDOW = 126
MAX_LEVERAGE = 10.0
START = pd.Timestamp("2019-01-01", tz="UTC")
END = pd.Timestamp("2025-12-31 23:59:59", tz="UTC")

ADAPTIVE_75_50_TIERS = [(0.3, 1.0), (0.0, 0.75), (-1e9, 0.5)]

PRODUCTION_ACCOUNTS = [
    {"account_id": "A1", "strategy": "FX_MR_STACK", "target_vol": 0.10},
    {"account_id": "A2", "strategy": "FX_MR_STACK", "target_vol": 0.10},
    {"account_id": "A3", "strategy": "NO_EUR_STACK", "target_vol": 0.10},
    {"account_id": "A4", "strategy": "NO_EUR_STACK", "target_vol": 0.10},
    {"account_id": "A5", "strategy": "COMDOLL_STACK", "target_vol": 0.10},
    {"account_id": "A6", "strategy": "COMDOLL_STACK", "target_vol": 0.10},
    {"account_id": "A7", "strategy": "FX_MR_STACK", "target_vol": 0.08},
    {"account_id": "A8", "strategy": "NO_EUR_STACK", "target_vol": 0.08},
]


RuleConfidence = Literal["verified", "partly_verified", "template_only"]
Verdict = Literal["COMPATIBLE", "COMPATIBLE_VERIFY", "RISKY", "ABANDON"]


@dataclass(frozen=True)
class FirmTemplate:
    name: str
    account_size: float
    fee: float
    profit_share: float
    targets: tuple[float, ...]
    max_daily_loss: float
    max_overall_loss: float
    overall_mode: Literal["static", "trailing_eod"]
    min_trading_days_per_phase: int = 0
    inactivity_days: int | None = None
    min_profitable_days_per_payout: int = 0
    profitable_day_threshold: float = 0.0
    payout_interval_days: int = 21
    instant_funded: bool = False
    daily_loss_is_pause: bool = False
    confidence: RuleConfidence = "template_only"
    source: str = ""
    notes: tuple[str, ...] = ()


@dataclass
class AccountAudit:
    account_id: str
    strategy: str
    blowups: int = 0
    inactivity_violations: int = 0
    daily_violations: int = 0
    overall_violations: int = 0
    phase_passes: int = 0
    funded_days: int = 0
    payout_checks: int = 0
    payout_blocks_profitable_days: int = 0
    payouts: int = 0
    fees_paid: float = 0.0
    max_no_trade_gap_calendar_days: int = 0
    min_profitable_days_observed: int = 0


FIRM_TEMPLATES = {
    "FTMO_2STEP_NORMAL": FirmTemplate(
        name="FTMO_2STEP_NORMAL",
        account_size=200_000,
        fee=1_080,
        profit_share=0.80,
        targets=(0.10, 0.05),
        max_daily_loss=0.05,
        max_overall_loss=0.10,
        overall_mode="static",
        min_trading_days_per_phase=4,
        confidence="verified",
        source="https://ftmo.com/en/trading-objectives/",
        notes=(
            "Minimum 4 trading days applies during Challenge and Verification.",
            "No minimum trading days after the 2-step FTMO Account is reached.",
        ),
    ),
    "FTMO_SWING_TEMPLATE": FirmTemplate(
        name="FTMO_SWING_TEMPLATE",
        account_size=200_000,
        fee=1_080,
        profit_share=0.80,
        targets=(0.10, 0.05),
        max_daily_loss=0.05,
        max_overall_loss=0.10,
        overall_mode="static",
        min_trading_days_per_phase=0,
        confidence="template_only",
        source="https://ftmo.com/en/trading-objectives/",
        notes=(
            "Template assumes no minimum-day friction for swing-style deployment.",
            "Verify exact Swing product terms before purchase; FTMO product rules change.",
        ),
    ),
    "FUNDEDNEXT_CFD": FirmTemplate(
        name="FUNDEDNEXT_CFD",
        account_size=200_000,
        fee=999,
        profit_share=0.80,
        targets=(0.08, 0.05),
        max_daily_loss=0.05,
        max_overall_loss=0.10,
        overall_mode="static",
        inactivity_days=60,
        confidence="verified",
        source="https://help.fundednext.com/en/articles/8019664-is-there-an-inactivity-period-for-my-accounts-in-fundednext-cfd",
        notes=(
            "60 consecutive calendar days without placing a trade can deactivate accounts.",
            "ADAPTIVE_75_50 should keep trading; full-pause variants are much riskier.",
        ),
    ),
    "FUNDINGPIPS_ZERO": FirmTemplate(
        name="FUNDINGPIPS_ZERO",
        account_size=200_000,
        fee=999,
        profit_share=0.95,
        targets=(),
        max_daily_loss=0.03,
        max_overall_loss=0.05,
        overall_mode="trailing_eod",
        inactivity_days=30,
        min_profitable_days_per_payout=7,
        profitable_day_threshold=0.0025,
        payout_interval_days=14,
        instant_funded=True,
        confidence="verified",
        source="https://help.fundingpips.com/hc/en-us/articles/34502157694865-FundingPips-Zero",
        notes=(
            "No evaluation phase; direct funded model.",
            "3% daily loss, 5% trailing drawdown, 30-day inactivity, 7 profitable days per rolling 30-day period.",
            "Weekend non-crypto holds and news restrictions are not modeled here.",
            "Max-risk-per-trade rule is not modeled and must be handled by execution sizing.",
        ),
    ),
    "E8_SIGNATURE_FOREX": FirmTemplate(
        name="E8_SIGNATURE_FOREX",
        account_size=150_000,
        fee=450,
        profit_share=0.80,
        targets=(0.06,),
        max_daily_loss=0.02,
        max_overall_loss=0.03,
        overall_mode="trailing_eod",
        inactivity_days=60,
        min_profitable_days_per_payout=5,
        profitable_day_threshold=0.003,
        payout_interval_days=14,
        daily_loss_is_pause=True,
        confidence="partly_verified",
        source="https://help.e8markets.com/en/articles/11755943-e8-signature-forex",
        notes=(
            "Official source confirms 60-day activity rule, 2% daily pause, and 0.3% profitable-day definition.",
            "The 5-profitable-day payout count is treated as user-provided and must be re-verified.",
            "EOD dynamic drawdown details vary by product; this template uses 3% as a conservative placeholder.",
        ),
    ),
}


def adaptive_75_50_returns(
    daily: pd.Series,
    intraday: pd.Series,
    target_vol: float,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Apply the final production ADAPTIVE_75_50 sizing with shifted inputs."""
    realized = daily.rolling(VOL_LOOKBACK).std() * math.sqrt(ANN_DAYS)
    base_leverage = (target_vol / realized).clip(upper=MAX_LEVERAGE).shift(1).fillna(1.0)
    static_returns = (daily * base_leverage).dropna()

    rolling_mean = static_returns.rolling(ADAPTIVE_WINDOW).mean()
    rolling_std = static_returns.rolling(ADAPTIVE_WINDOW).std()
    rolling_sharpe = ((rolling_mean / rolling_std) * math.sqrt(ANN_DAYS)).shift(1)

    def to_multiplier(value: float) -> float:
        if pd.isna(value):
            return 1.0
        for threshold, multiplier in ADAPTIVE_75_50_TIERS:
            if value >= threshold:
                return multiplier
        return 0.5

    multiplier = rolling_sharpe.map(to_multiplier).fillna(1.0)
    effective_leverage = (target_vol * multiplier / realized).clip(upper=MAX_LEVERAGE).shift(1).fillna(1.0)
    close_path = (daily * effective_leverage).dropna()
    intraday_path = (intraday.reindex(close_path.index).fillna(daily) * effective_leverage.reindex(close_path.index)).dropna()
    common = close_path.index.intersection(intraday_path.index)
    return close_path.loc[common], intraday_path.loc[common], multiplier.reindex(common).fillna(1.0)


def build_account_paths(specs: dict[str, dict]) -> dict[str, dict[str, pd.Series]]:
    paths = {}
    for account in PRODUCTION_ACCOUNTS:
        spec = specs[account["strategy"]]
        close_path, intraday_path, multiplier = adaptive_75_50_returns(
            spec["daily"],
            spec["intraday"],
            float(account["target_vol"]),
        )
        close_path = close_path[(close_path.index >= START) & (close_path.index <= END)]
        intraday_path = intraday_path.reindex(close_path.index).fillna(close_path)
        multiplier = multiplier.reindex(close_path.index).fillna(1.0)
        paths[account["account_id"]] = {
            "strategy": account["strategy"],
            "close": close_path,
            "intraday": intraday_path,
            "multiplier": multiplier,
        }
    return paths


def no_trade_gap_days(close_path: pd.Series) -> int:
    trade_days = close_path[close_path.abs() > 1e-12].index
    if len(trade_days) < 2:
        return 0
    gaps = trade_days.to_series().diff().dropna().dt.days
    return int(gaps.max()) if not gaps.empty else 0


def reset_state() -> dict[str, float | int | bool | pd.Timestamp | None]:
    return {
        "phase": 0,
        "phase_pnl": 0.0,
        "account_pnl": 0.0,
        "high_water": 0.0,
        "trading_days_phase": 0,
        "profitable_days_phase": 0,
        "profitable_days_since_payout": 0,
        "funded": False,
        "last_trade_date": None,
        "last_reset_date": None,
        "last_payout_check": None,
    }


def is_overall_breach(state: dict, template: FirmTemplate) -> bool:
    account_pnl = float(state["account_pnl"])
    if template.overall_mode == "static":
        return account_pnl <= -template.max_overall_loss
    high_water = max(float(state["high_water"]), account_pnl)
    floor = high_water - template.max_overall_loss
    return account_pnl <= floor


def advance_phase_if_ready(state: dict, template: FirmTemplate, audit: AccountAudit) -> None:
    if bool(state["funded"]) or template.instant_funded:
        state["funded"] = True
        return
    phase = int(state["phase"])
    if phase >= len(template.targets):
        state["funded"] = True
        return
    target_hit = float(state["phase_pnl"]) >= template.targets[phase]
    min_days_hit = int(state["trading_days_phase"]) >= template.min_trading_days_per_phase
    if target_hit and min_days_hit:
        audit.phase_passes += 1
        state["phase"] = phase + 1
        state["phase_pnl"] = 0.0
        state["trading_days_phase"] = 0
        state["profitable_days_phase"] = 0
        if int(state["phase"]) >= len(template.targets):
            state["funded"] = True
            state["last_payout_check"] = None


def handle_payout_check(state: dict, template: FirmTemplate, audit: AccountAudit, date: pd.Timestamp) -> None:
    if not bool(state["funded"]):
        return
    last_check = state["last_payout_check"]
    if last_check is None:
        state["last_payout_check"] = date
        return
    if (date - last_check).days < template.payout_interval_days:
        return
    audit.payout_checks += 1
    profitable_days = int(state["profitable_days_since_payout"])
    audit.min_profitable_days_observed = max(audit.min_profitable_days_observed, profitable_days)
    if profitable_days < template.min_profitable_days_per_payout:
        audit.payout_blocks_profitable_days += 1
    elif float(state["account_pnl"]) > 0:
        audit.payouts += 1
        state["profitable_days_since_payout"] = 0
        state["last_payout_check"] = date
    else:
        state["last_payout_check"] = date


def simulate_account(template: FirmTemplate, account_id: str, account_path: dict[str, pd.Series]) -> AccountAudit:
    close_path = account_path["close"]
    intraday_path = account_path["intraday"].reindex(close_path.index).fillna(close_path)
    audit = AccountAudit(account_id=account_id, strategy=str(account_path["strategy"]), fees_paid=template.fee)
    state = reset_state()
    if template.instant_funded:
        state["funded"] = True
    audit.max_no_trade_gap_calendar_days = no_trade_gap_days(close_path)

    for date, daily_return in close_path.items():
        if state["last_reset_date"] is None:
            state["last_reset_date"] = date
        traded = abs(float(daily_return)) > 1e-12
        if traded:
            last_trade = state["last_trade_date"]
            if last_trade is not None:
                gap = int((date - last_trade).days)
                audit.max_no_trade_gap_calendar_days = max(audit.max_no_trade_gap_calendar_days, gap)
            state["last_trade_date"] = date
            state["trading_days_phase"] = int(state["trading_days_phase"]) + 1
            if daily_return >= template.profitable_day_threshold and daily_return > 0:
                state["profitable_days_phase"] = int(state["profitable_days_phase"]) + 1
                state["profitable_days_since_payout"] = int(state["profitable_days_since_payout"]) + 1

        if template.inactivity_days is not None and state["last_trade_date"] is not None:
            if (date - state["last_trade_date"]).days > template.inactivity_days:
                audit.inactivity_violations += 1
                audit.blowups += 1
                audit.fees_paid += template.fee
                state = reset_state()
                if template.instant_funded:
                    state["funded"] = True
                state["last_reset_date"] = date
                continue

        intraday_return = float(intraday_path.loc[date])
        if intraday_return <= -template.max_daily_loss:
            audit.daily_violations += 1
            if not template.daily_loss_is_pause:
                audit.blowups += 1
                audit.fees_paid += template.fee
                state = reset_state()
                if template.instant_funded:
                    state["funded"] = True
                state["last_reset_date"] = date
                continue

        state["phase_pnl"] = float(state["phase_pnl"]) + float(daily_return)
        state["account_pnl"] = float(state["account_pnl"]) + float(daily_return)
        state["high_water"] = max(float(state["high_water"]), float(state["account_pnl"]))

        if is_overall_breach(state, template):
            audit.overall_violations += 1
            audit.blowups += 1
            audit.fees_paid += template.fee
            state = reset_state()
            if template.instant_funded:
                state["funded"] = True
            state["last_reset_date"] = date
            continue

        advance_phase_if_ready(state, template, audit)
        if bool(state["funded"]):
            audit.funded_days += 1
        handle_payout_check(state, template, audit, date)

    return audit


def classify(template: FirmTemplate, audits: list[AccountAudit]) -> tuple[Verdict, list[str]]:
    reasons = []
    total_inactivity = sum(a.inactivity_violations for a in audits)
    total_daily = sum(a.daily_violations for a in audits)
    total_overall = sum(a.overall_violations for a in audits)
    total_payout_blocks = sum(a.payout_blocks_profitable_days for a in audits)
    payout_checks = sum(a.payout_checks for a in audits)

    if total_inactivity:
        reasons.append(f"{total_inactivity} inactivity violations")
    if total_daily and (template.max_daily_loss < 0.05 or template.daily_loss_is_pause):
        reasons.append(f"{total_daily} strict daily-risk events")
    if total_overall and template.max_overall_loss < 0.10:
        reasons.append(f"{total_overall} strict/trailing overall-DD violations")
    if payout_checks and total_payout_blocks / payout_checks > 0.50:
        reasons.append(f"{total_payout_blocks}/{payout_checks} payout checks blocked by profitable-day gate")
    if template.confidence != "verified":
        reasons.append(f"rules are {template.confidence}; manual verification required")

    if total_inactivity:
        return "ABANDON", reasons
    if total_daily and template.max_daily_loss <= 0.03 and not template.daily_loss_is_pause:
        return "ABANDON", reasons
    if template.max_overall_loss < 0.10 and total_overall > len(audits):
        return "RISKY", reasons
    if payout_checks and total_payout_blocks / payout_checks > 0.50:
        return "RISKY", reasons
    if template.confidence == "verified":
        return "COMPATIBLE", reasons or ["no modeled operational blocker"]
    return "COMPATIBLE_VERIFY", reasons or ["modeled compatible, but rules need manual verification"]


def run_audit() -> dict:
    specs = cash.build_strategy_universe()
    account_paths = build_account_paths(specs)
    results = {}
    for firm_name, template in FIRM_TEMPLATES.items():
        audits = [simulate_account(template, account_id, account_path) for account_id, account_path in account_paths.items()]
        verdict, reasons = classify(template, audits)
        results[firm_name] = {
            "template": asdict(template),
            "verdict": verdict,
            "reasons": reasons,
            "accounts": [asdict(audit) for audit in audits],
            "totals": {
                "blowups": sum(a.blowups for a in audits),
                "inactivity_violations": sum(a.inactivity_violations for a in audits),
                "daily_violations": sum(a.daily_violations for a in audits),
                "overall_violations": sum(a.overall_violations for a in audits),
                "phase_passes": sum(a.phase_passes for a in audits),
                "funded_days": sum(a.funded_days for a in audits),
                "payout_checks": sum(a.payout_checks for a in audits),
                "payout_blocks_profitable_days": sum(a.payout_blocks_profitable_days for a in audits),
                "payouts": sum(a.payouts for a in audits),
                "fees_paid": sum(a.fees_paid for a in audits),
                "max_no_trade_gap_calendar_days": max(a.max_no_trade_gap_calendar_days for a in audits),
            },
        }
    payload = {
        "run_date": pd.Timestamp.now(tz="UTC").isoformat(),
        "period": {"start": str(START.date()), "end": str(END.date())},
        "adaptive": "ADAPTIVE_75_50",
        "accounts": PRODUCTION_ACCOUNTS,
        "results": results,
    }
    OUT_METRICS.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_report(payload)
    return payload


def write_report(payload: dict) -> None:
    lines = [
        "# Firm Rule Audit — ADAPTIVE_75_50",
        "",
        f"Run date: {payload['run_date']}",
        f"Backtest window: {payload['period']['start']} → {payload['period']['end']}",
        "",
        "Purpose: detect prop-firm operational rules that can invalidate the strategy even if edge is positive.",
        "This is a compatibility audit, not a new alpha backtest.",
        "",
        "## Verdict Summary",
        "",
        "| firm template | verdict | confidence | blowups | inactivity | daily DD | overall DD | payout blocks | max no-trade gap |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for firm_name, result in payload["results"].items():
        template = result["template"]
        totals = result["totals"]
        lines.append(
            f"| {firm_name} | **{result['verdict']}** | {template['confidence']} | "
            f"{totals['blowups']} | {totals['inactivity_violations']} | {totals['daily_violations']} | "
            f"{totals['overall_violations']} | {totals['payout_blocks_profitable_days']}/{totals['payout_checks']} | "
            f"{totals['max_no_trade_gap_calendar_days']}d |"
        )

    lines.extend([
        "",
        "## Firm Details",
        "",
    ])
    for firm_name, result in payload["results"].items():
        template = result["template"]
        totals = result["totals"]
        lines.extend([
            f"### {firm_name}",
            "",
            f"- Verdict: **{result['verdict']}**",
            f"- Source: {template['source'] or 'manual template'}",
            f"- Confidence: `{template['confidence']}`",
            f"- Targets: `{template['targets']}` | daily DD `{template['max_daily_loss']:.1%}` | overall DD `{template['max_overall_loss']:.1%}` | mode `{template['overall_mode']}`",
            f"- Min trading days/phase: `{template['min_trading_days_per_phase']}` | inactivity: `{template['inactivity_days']}` | profitable-day gate: `{template['min_profitable_days_per_payout']}` days at `{template['profitable_day_threshold']:.2%}`",
            f"- Totals: blowups `{totals['blowups']}`, phase passes `{totals['phase_passes']}`, funded days `{totals['funded_days']}`, payouts `{totals['payouts']}`",
            "- Reasons:",
        ])
        for reason in result["reasons"]:
            lines.append(f"  - {reason}")
        lines.append("- Notes:")
        for note in template["notes"]:
            lines.append(f"  - {note}")
        lines.append("")

    lines.extend([
        "## Direct Answer",
        "",
        "- **Most strict for this strategy:** `FUNDINGPIPS_ZERO` and `E8_SIGNATURE_FOREX` because they add profitable-day gates and stricter daily/trailing drawdown.",
        "- **Most compatible:** `FTMO_2STEP_NORMAL` / `FUNDEDNEXT_CFD` under ADAPTIVE_75_50, assuming exact symbols, payout rules, and news rules are verified before purchase.",
        "- **Pause-friendly conclusion:** ADAPTIVE_75_50 never fully pauses, so inactivity rules are not the blocker. Full-pause variants would be materially riskier for FundedNext/FundingPips/E8.",
        "- **Manual verification still required:** broker dashboard must confirm exact inactivity, payout, consistency, news, weekend, copy-trading, and max-risk-per-trade rules before buying a challenge.",
        "",
        "## Known Limitations",
        "",
        "- Does not model firm-specific consistency rules such as best-day caps.",
        "- Does not model swap, commissions beyond strategy cost assumptions, weekend restrictions, or exact server-time cutoff.",
        "- Does not model FundingPips max-risk-per-trade grouping rule; execution must enforce it separately.",
        "- E8 template is intentionally conservative because product rules vary heavily by account type and region.",
    ])
    OUT_REPORT.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    payload = run_audit()
    print("Firm rule audit complete.")
    for firm_name, result in payload["results"].items():
        totals = result["totals"]
        print(
            f"  {firm_name:<22} {result['verdict']:<18} "
            f"blowups={totals['blowups']:<3} inactivity={totals['inactivity_violations']:<2} "
            f"payout_blocks={totals['payout_blocks_profitable_days']}/{totals['payout_checks']}"
        )
    print(f"Wrote: {OUT_REPORT}")
    print(f"Wrote: {OUT_METRICS}")


if __name__ == "__main__":
    main()
