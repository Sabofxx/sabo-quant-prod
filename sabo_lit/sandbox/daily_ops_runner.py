"""
One-command daily operations runner for the FX prop-firm system.

This runner is intentionally dry-run oriented. It orchestrates the live workflow
without sending live orders:

1. news blackout check
2. account/broker config checks
3. signal + delta-order generation
4. delta CSV sanity checks
5. MT5 connector dry-run
6. live tracker dashboard refresh
7. GO / NO-GO report
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

import live_news_calendar as news


HERE = Path(__file__).parent
DEFAULT_ACCOUNTS = HERE / "propfirm_hybrid_8x200_accounts.json"
DEFAULT_BROKER_MAP = HERE / "broker_symbol_map.json"
DEFAULT_POSITIONS = HERE / "propfirm_positions.example.json"
LIVE_DIR = HERE / "live"
DEFAULT_OUT_DIR = LIVE_DIR
SIGNAL_GENERATOR = HERE / "propfirm_signal_generator.py"
MT5_CONNECTOR = HERE / "mt5_connector.py"
LIVE_TRACKER = HERE / "live_tracker.py"
DASHBOARD_GENERATOR = HERE / "dashboard_generator.py"
TELEGRAM_NOTIFIER = HERE / "telegram_notifier.py"


@dataclass(frozen=True)
class StepResult:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class CommandResult:
    label: str
    returncode: int
    stdout: str
    stderr: str
    command: list[str]


def parse_as_of(value: str | None) -> pd.Timestamp:
    if value:
        timestamp = pd.Timestamp(value)
    else:
        timestamp = pd.Timestamp.now(tz="UTC").floor("D")
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC").floor("D")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_command(label: str, command: list[str]) -> CommandResult:
    completed = subprocess.run(command, cwd=HERE.parent, text=True, capture_output=True, check=False)
    return CommandResult(
        label=label,
        returncode=completed.returncode,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
        command=command,
    )


def broker_from_accounts(accounts_path: Path, requested_broker: str | None) -> tuple[str, StepResult]:
    config = load_json(accounts_path)
    enabled = [account for account in config.get("accounts", []) if account.get("enabled", True)]
    brokers = sorted({str(account.get("broker", "")).strip() for account in enabled if account.get("broker")})
    if requested_broker:
        return requested_broker, StepResult("broker_selection", "OK", f"broker forced by CLI: {requested_broker}")
    if len(brokers) == 1:
        return brokers[0], StepResult("broker_selection", "OK", f"single broker in config: {brokers[0]}")
    return "default_mt5", StepResult(
        "broker_selection",
        "WARN",
        f"mixed brokers in config {brokers}; using default_mt5 for dry-run symbol mapping",
    )


def broker_check(broker_map_path: Path, broker: str, require_verified: bool) -> StepResult:
    raw = load_json(broker_map_path)
    brokers = raw.get("brokers", {})
    if broker not in brokers:
        return StepResult("broker_map", "FAIL", f"unknown broker {broker}; known={sorted(brokers)}")
    entry = brokers[broker]
    verified = bool(entry.get("verified", False))
    if require_verified and not verified:
        return StepResult("broker_map", "FAIL", f"broker map {broker} is not verified")
    if not verified:
        return StepResult("broker_map", "WARN", f"broker map {broker} is template-only; live not approved")
    return StepResult("broker_map", "OK", f"broker map {broker} verified")


def news_check(as_of: pd.Timestamp, refresh: bool) -> tuple[StepResult, str]:
    events, source = news.load_events(as_of, as_of + pd.Timedelta(days=7), refresh=refresh)
    today = as_of.strftime("%Y-%m-%d")
    labels = sorted({event.label for event in events if event.date == today})
    if labels:
        return StepResult("news", "WARN", f"{today} blackout={','.join(labels)} source={source}"), ",".join(labels)
    return StepResult("news", "OK", f"{today} no blackout source={source}"), ""


def positions_check(path: Path | None) -> StepResult:
    if path is None:
        return StepResult("positions", "WARN", "no positions file supplied; deltas assume flat book")
    if not path.exists():
        return StepResult("positions", "FAIL", f"positions file not found: {path}")
    raw = load_json(path)
    count = len(raw.get("positions", []))
    return StepResult("positions", "OK", f"{count} current positions loaded from {path}")


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def summarize_delta_csv(path: Path, max_lots: float) -> tuple[StepResult, dict[str, Any]]:
    rows = read_csv_rows(path)
    symbols = sorted({row.get("broker_symbol", "") for row in rows if row.get("broker_symbol")})
    accounts = sorted({row.get("account_id", "") for row in rows if row.get("account_id")})
    total_delta = sum(abs(float(row.get("delta_notional_usd") or 0.0)) for row in rows)
    lot_values = [
        abs(float(row["estimated_standard_lots"]))
        for row in rows
        if row.get("estimated_standard_lots") not in (None, "", "None")
    ]
    max_row_lots = max(lot_values) if lot_values else 0.0
    summary = {
        "rows": len(rows),
        "accounts": accounts,
        "symbols": symbols,
        "total_delta_notional_usd": total_delta,
        "max_estimated_lots": max_row_lots,
        "path": str(path),
    }
    if max_row_lots > max_lots:
        return StepResult("delta_csv", "FAIL", f"max row lots {max_row_lots:.2f} exceeds cap {max_lots:.2f}"), summary
    return StepResult(
        "delta_csv",
        "OK",
        f"{len(rows)} rows, ${total_delta:,.0f} gross delta, max lots {max_row_lots:.2f}",
    ), summary


def command_step(result: CommandResult) -> StepResult:
    if result.returncode == 0:
        return StepResult(result.label, "OK", "command completed")
    detail = result.stderr or result.stdout or f"returncode={result.returncode}"
    return StepResult(result.label, "FAIL", detail[-500:])


def decision(steps: list[StepResult], require_live_ready: bool) -> str:
    if any(step.status == "FAIL" for step in steps):
        return "NO_GO"
    if require_live_ready and any(step.status == "WARN" for step in steps):
        return "LIVE_NO_GO"
    if any(step.status == "WARN" for step in steps):
        return "DEMO_OK_LIVE_NO_GO"
    return "DEMO_OK"


def write_report(
    path: Path,
    as_of: pd.Timestamp,
    broker: str,
    steps: list[StepResult],
    commands: list[CommandResult],
    delta_summary: dict[str, Any],
    final_decision: str,
) -> None:
    lines = [
        "# Daily Ops Runner Report",
        "",
        f"Generated: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"As of: {as_of.date()}",
        f"Broker map: `{broker}`",
        f"Decision: **{final_decision}**",
        "",
        "## Step Status",
        "",
        "| step | status | detail |",
        "|---|---|---|",
    ]
    for step in steps:
        lines.append(f"| {step.name} | {step.status} | {step.detail.replace('|', '/')} |")
    lines.extend([
        "",
        "## Delta Summary",
        "",
        f"- Rows: `{delta_summary.get('rows', 0)}`",
        f"- Gross delta: `${delta_summary.get('total_delta_notional_usd', 0.0):,.0f}`",
        f"- Max estimated lots/order: `{delta_summary.get('max_estimated_lots', 0.0):.2f}`",
        f"- Accounts: `{', '.join(delta_summary.get('accounts', []))}`",
        f"- Symbols: `{', '.join(delta_summary.get('symbols', []))}`",
        "",
        "## Commands",
        "",
    ])
    for result in commands:
        lines.extend([
            f"### {result.label}",
            "",
            f"- Return code: `{result.returncode}`",
            f"- Command: `{' '.join(result.command)}`",
        ])
        if result.stdout:
            lines.extend(["", "```text", result.stdout[-2000:], "```"])
        if result.stderr:
            lines.extend(["", "stderr:", "```text", result.stderr[-2000:], "```"])
        lines.append("")
    lines.extend([
        "## Interpretation",
        "",
        "- `DEMO_OK`: safe to use for demo/manual rehearsal.",
        "- `DEMO_OK_LIVE_NO_GO`: files generated, but at least one live blocker/warning remains.",
        "- `LIVE_NO_GO`: live was requested as strict, but warnings remain.",
        "- `NO_GO`: fix failed step before execution.",
        "",
        "This runner never sends live orders. Run `mt5_connector.py --live` separately only after demo validation.",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")



def os_environ_has_telegram() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily FX prop-firm operations in dry-run mode.")
    parser.add_argument("--as-of", default=None, help="Completed close date YYYY-MM-DD. Default: today UTC.")
    parser.add_argument("--accounts", default=str(DEFAULT_ACCOUNTS))
    parser.add_argument("--positions", default=str(DEFAULT_POSITIONS), help="Current positions JSON. Use empty string to omit.")
    parser.add_argument("--broker", default=None, help="Broker key from broker_symbol_map.json. Auto/default if omitted.")
    parser.add_argument("--broker-map", default=str(DEFAULT_BROKER_MAP))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--state", default=None, help="Optional account state JSON for lockouts.")
    parser.add_argument("--refresh-news", action="store_true", help="Refresh Trading Economics cache if key is configured.")
    parser.add_argument("--ignore-news", action="store_true", help="Pass through to signal generator; not recommended.")
    parser.add_argument("--skip-mt5-dry-run", action="store_true")
    parser.add_argument("--skip-dashboard", action="store_true")
    parser.add_argument("--skip-telegram", action="store_true")
    parser.add_argument("--require-live-ready", action="store_true", help="Treat warnings as live blockers.")
    parser.add_argument("--max-lots-per-order", type=float, default=5.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    as_of = parse_as_of(args.as_of)
    accounts_path = Path(args.accounts)
    broker_map_path = Path(args.broker_map)
    positions_path = Path(args.positions) if args.positions else None
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    steps: list[StepResult] = []
    commands: list[CommandResult] = []

    broker, broker_selection = broker_from_accounts(accounts_path, args.broker)
    steps.append(broker_selection)
    steps.append(broker_check(broker_map_path, broker, args.require_live_ready))
    news_step, _blackout_reason = news_check(as_of, args.refresh_news)
    steps.append(news_step)
    steps.append(positions_check(positions_path))

    signal_cmd = [
        sys.executable,
        str(SIGNAL_GENERATOR),
        "--adaptive",
        "--as-of",
        as_of.strftime("%Y-%m-%d"),
        "--accounts",
        str(accounts_path),
        "--broker",
        broker,
        "--broker-map",
        str(broker_map_path),
        "--out-dir",
        str(out_dir),
    ]
    if positions_path is not None:
        signal_cmd.extend(["--positions", str(positions_path)])
    if args.state:
        signal_cmd.extend(["--state", args.state])
    if args.ignore_news:
        signal_cmd.append("--ignore-news")
    signal_result = run_command("signal_generation", signal_cmd)
    commands.append(signal_result)
    steps.append(command_step(signal_result))

    latest_delta = out_dir / "prop_delta_orders_latest.csv"
    delta_step, delta_summary = summarize_delta_csv(latest_delta, args.max_lots_per_order)
    steps.append(delta_step)

    if not args.skip_mt5_dry_run:
        mt5_cmd = [
            sys.executable,
            str(MT5_CONNECTOR),
            "--delta-csv",
            str(latest_delta),
            "--max-lots-per-order",
            str(args.max_lots_per_order),
        ]
        mt5_result = run_command("mt5_dry_run", mt5_cmd)
        commands.append(mt5_result)
        steps.append(command_step(mt5_result))
    else:
        steps.append(StepResult("mt5_dry_run", "WARN", "skipped by CLI"))

    tracker_cmd = [sys.executable, str(LIVE_TRACKER), "--account-config", str(accounts_path)]
    tracker_result = run_command("live_tracker", tracker_cmd)
    commands.append(tracker_result)
    steps.append(command_step(tracker_result))

    if not args.skip_dashboard:
        dashboard_cmd = [sys.executable, str(DASHBOARD_GENERATOR)]
        dashboard_result = run_command("dashboard_generator", dashboard_cmd)
        commands.append(dashboard_result)
        steps.append(command_step(dashboard_result))
    else:
        steps.append(StepResult("dashboard_generator", "WARN", "skipped by CLI"))

    final_decision = decision(steps, args.require_live_ready)
    report_path = out_dir / f"daily_ops_report_{as_of.date()}.md"
    latest_report = out_dir / "daily_ops_report_latest.md"
    write_report(report_path, as_of, broker, steps, commands, delta_summary, final_decision)
    latest_report.write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")

    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "as_of": str(as_of.date()),
        "broker": broker,
        "decision": final_decision,
        "steps": [asdict(step) for step in steps],
        "delta_summary": delta_summary,
        "report": str(report_path),
    }
    metrics_path = out_dir / f"daily_ops_metrics_{as_of.date()}.json"
    latest_metrics = out_dir / "daily_ops_metrics_latest.json"
    metrics_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    latest_metrics.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if not args.skip_telegram and os_environ_has_telegram():
        telegram_status = "failure" if final_decision == "NO_GO" else "success"
        telegram_cmd = [
            sys.executable,
            str(TELEGRAM_NOTIFIER),
            "--run-summary",
            "--status",
            telegram_status,
        ]
        telegram_result = run_command("telegram_notify", telegram_cmd)
        commands.append(telegram_result)
        steps.append(command_step(telegram_result))
        final_decision = decision(steps, args.require_live_ready)
        payload["decision"] = final_decision
        payload["steps"] = [asdict(step) for step in steps]
        metrics_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        latest_metrics.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        write_report(report_path, as_of, broker, steps, commands, delta_summary, final_decision)
        latest_report.write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")
    elif not args.skip_telegram:
        steps.append(StepResult("telegram_notify", "WARN", "TELEGRAM_* env vars not set; skipped"))
        final_decision = decision(steps, args.require_live_ready)
        payload["decision"] = final_decision
        payload["steps"] = [asdict(step) for step in steps]
        metrics_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        latest_metrics.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        write_report(report_path, as_of, broker, steps, commands, delta_summary, final_decision)
        latest_report.write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")

    print(f"Decision: {final_decision}")
    for step in steps:
        print(f"  {step.name:<18} {step.status:<5} {step.detail}")
    print(f"Report: {report_path}")
    print(f"Metrics: {metrics_path}")


if __name__ == "__main__":
    main()
