"""
Sandbox — Telegram bot notifier for daily run summaries.

Sends HTML-formatted reports to a Telegram chat after the automated or daily
runner. This is an outbound Telegram Bot API notifier, not an inbound webhook
server.

Env vars required:
  TELEGRAM_BOT_TOKEN  - from @BotFather
  TELEGRAM_CHAT_ID    - your chat ID

Usage:
  python telegram_notifier.py --run-summary --status success
  python telegram_notifier.py --run-summary --status failure
  python telegram_notifier.py --message "Hello from bot"
  python telegram_notifier.py --test
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import requests


HERE = Path(__file__).parent
LIVE_DIR = HERE / "live"
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_MSG_LEN = 3800


def html_escape(value: object) -> str:
    return html.escape(str(value), quote=False)


def chunk_html_by_lines(text: str) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > MAX_MSG_LEN and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [""]


def send_telegram(token: str, chat_id: str, text: str, parse_mode: str = "HTML") -> list[dict]:
    url = TELEGRAM_API.format(token=token)
    responses = []
    for chunk in chunk_html_by_lines(text):
        response = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if not response.ok:
            print(f"Telegram API error {response.status_code}: {response.text[:300]}", file=sys.stderr)
            response.raise_for_status()
        responses.append(response.json())
    return responses


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def load_json(path: Path) -> dict | list:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def fmt_money(value: float, currency: str = "") -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,.2f} {currency}".strip()


def add_daily_ops_section(lines: list[str]) -> None:
    daily_ops = load_json(LIVE_DIR / "daily_ops_metrics_latest.json")
    if not isinstance(daily_ops, dict) or not daily_ops:
        return
    decision = daily_ops.get("decision", "UNKNOWN")
    delta = daily_ops.get("delta_summary", {})
    decision_emoji = "🟢" if decision in {"DEMO_OK", "DEMO_OK_LIVE_NO_GO"} else "🔴"
    lines.append(f"{decision_emoji} <b>Decision</b>: <code>{html_escape(decision)}</code>")
    lines.append(
        f"   As of: {html_escape(daily_ops.get('as_of', '?'))} · "
        f"Broker: {html_escape(daily_ops.get('broker', '?'))}"
    )
    if isinstance(delta, dict):
        lines.append(
            "   Delta: "
            f"{int(delta.get('rows', 0))} rows · "
            f"${float(delta.get('total_delta_notional_usd', 0)):,.0f} · "
            f"max {float(delta.get('max_estimated_lots', 0)):.2f} lots"
        )
    step_counts: dict[str, int] = {}
    for step in daily_ops.get("steps", []):
        status = step.get("status", "UNKNOWN")
        step_counts[status] = step_counts.get(status, 0) + 1
    if step_counts:
        lines.append("   Steps: " + ", ".join(f"{k}={v}" for k, v in sorted(step_counts.items())))
    lines.append("")


def add_account_snapshot_section(lines: list[str]) -> None:
    snapshots = load_jsonl(LIVE_DIR / "automated_daily_pnl.jsonl")
    if not snapshots:
        return
    latest = snapshots[-1]
    currency = latest.get("currency", "")
    bal = float(latest.get("balance", 0) or 0)
    avail = float(latest.get("available", 0) or 0)
    pl = float(latest.get("profit_loss", 0) or 0)
    lines.append(f"💰 <b>Balance</b>: {bal:,.2f} {html_escape(currency)}")
    lines.append(f"   Available: {avail:,.2f} {html_escape(currency)}")
    lines.append(f"   {'📈' if pl >= 0 else '📉'} Unrealized PL: {fmt_money(pl, str(currency))}")

    prev_date_snap = None
    latest_date = latest.get("date", "")
    for snapshot in reversed(snapshots[:-1]):
        if snapshot.get("date") and snapshot.get("date") != latest_date:
            prev_date_snap = snapshot
            break
    if prev_date_snap:
        prev_bal = float(prev_date_snap.get("balance", bal) or bal)
        delta = bal - prev_bal
        delta_pct = (delta / prev_bal * 100) if prev_bal else 0.0
        lines.append(
            f"   {'🟢' if delta >= 0 else '🔴'} Δ vs {html_escape(prev_date_snap.get('date', 'prev'))}: "
            f"{fmt_money(delta, str(currency))} ({delta_pct:+.2f}%)"
        )
    lines.append("")


def add_execution_section(lines: list[str]) -> None:
    executions = load_jsonl(LIVE_DIR / "capital_executions.jsonl")
    today = datetime.now(UTC).date().isoformat()
    today_execs = [row for row in executions if row.get("ts", "").startswith(today)]
    if not today_execs:
        lines.append("⚡ <b>Executions today</b>: 0")
        lines.append("")
        return
    n_open = sum(1 for row in today_execs if row.get("status") in {"submitted", "filled", "dry_run_only"})
    n_close = sum(1 for row in today_execs if row.get("status") == "closed" or row.get("action") == "close_all")
    n_err = sum(1 for row in today_execs if row.get("status") == "error")
    lines.append(f"⚡ <b>Executions today</b>: {len(today_execs)} (open: {n_open}, close: {n_close}, err: {n_err})")
    for row in today_execs[-8:]:
        ts = html_escape(row.get("ts", "")[11:16])
        if row.get("action") == "close_all":
            status = row.get("status", "?")
            mark = "✓" if status == "closed" else "✗"
            lines.append(f"   {ts} {mark} CLOSE {html_escape(row.get('epic', '?'))}")
        else:
            status = row.get("status", "?")
            mark = "✓" if status in {"submitted", "filled", "dry_run_only"} else "✗" if status == "error" else "?"
            lines.append(
                f"   {ts} {mark} {html_escape(row.get('epic', '?'))} "
                f"{html_escape(row.get('direction', '?'))} {float(row.get('size', 0) or 0):.2f} "
                f"@ {float(row.get('price_mid', 0) or 0):.5f}"
            )
    if len(today_execs) > 8:
        lines.append(f"   ... +{len(today_execs) - 8} more")
    lines.append("")


def add_signal_section(lines: list[str]) -> None:
    data = load_json(LIVE_DIR / "prop_signals_latest.json")
    if not isinstance(data, dict) or not data:
        return
    accounts = data.get("accounts", [])
    orders = data.get("orders", [])
    summary = data.get("summary", {})
    n_nonflat = sum(1 for order in orders if order.get("side") != "FLAT")
    lines.append(f"🎯 <b>Signals</b>: {len(accounts)} accounts, {len(orders)} orders, {n_nonflat} non-flat")
    if isinstance(summary, dict):
        lines.append(
            f"   Gross ${float(summary.get('total_gross_notional_usd', 0)):,.0f} · "
            f"Delta ${float(summary.get('total_delta_notional_usd', 0)):,.0f}"
        )
        if summary.get("blackout_day"):
            lines.append(f"   ⚠️ News blackout: {html_escape(summary.get('blackout_reason', 'unknown'))}")
    lines.append("")


def add_tracker_alerts_section(lines: list[str]) -> None:
    alerts = load_json(LIVE_DIR / "tracker_alerts.json")
    if not isinstance(alerts, list) or not alerts:
        return
    lines.append(f"🚨 <b>Tracker alerts</b>: {len(alerts)}")
    for alert in alerts[:5]:
        lines.append(
            f"   • {html_escape(alert.get('account', '?'))}: "
            f"<code>{html_escape(alert.get('message', '?'))[:180]}</code>"
        )
    lines.append("")


def add_log_warnings_section(lines: list[str]) -> None:
    log_file = LIVE_DIR / "automated_runner.log"
    if not log_file.exists():
        return
    log_lines = log_file.read_text(encoding="utf-8").splitlines()
    marker_idx = None
    for index in range(len(log_lines) - 1, -1, -1):
        if "Automated daily runner started" in log_lines[index]:
            marker_idx = index
            break
    if marker_idx is None:
        return
    run_lines = log_lines[marker_idx:]
    errors = [line for line in run_lines if any(key in line for key in ("ERROR", "FATAL", "STEP FAILED"))]
    warns = [line for line in run_lines if "WARN" in line]
    if errors:
        lines.append(f"❌ <b>Errors</b> ({len(errors)}):")
        for err in errors[:5]:
            msg = err.split("] ", 1)[-1] if "]" in err else err
            lines.append(f"   • <code>{html_escape(msg[:180])}</code>")
        lines.append("")
    elif warns:
        lines.append(f"⚠️ <b>Warnings</b>: {len(warns)} non-fatal")
        lines.append("")


def build_summary(status: str = "success", repo: str = "Sabofxx/sabo-quant-prod") -> str:
    lines: list[str] = []
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    emoji = "✅" if status == "success" else "❌"
    lines.append(f"<b>{emoji} Sabo Quant Daily · {now}</b>")
    lines.append("")
    add_daily_ops_section(lines)
    add_account_snapshot_section(lines)
    add_execution_section(lines)
    add_signal_section(lines)
    add_tracker_alerts_section(lines)
    add_log_warnings_section(lines)
    safe_repo = html_escape(repo)
    lines.append(
        f"🔗 <a href=\"https://github.com/{safe_repo}/actions\">GitHub Actions</a> · "
        f"<a href=\"https://github.com/{safe_repo}/blob/main/sabo_lit/sandbox/live/dashboard.html\">Dashboard</a> · "
        f"<a href=\"https://capital.com\">Capital.com</a>"
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-summary", action="store_true", help="Build comprehensive summary from live state files")
    parser.add_argument("--message", type=str, help="Send literal message")
    parser.add_argument("--status", default="success", choices=["success", "failure"])
    parser.add_argument("--test", action="store_true", help="Send test message")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "Sabofxx/sabo-quant-prod"))
    parser.add_argument("--print-only", action="store_true", help="Render message without sending to Telegram")
    return parser


def parse_args() -> argparse.Namespace:
    return build_parser().parse_args()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.test:
        text = f"🤖 <b>Sabo Quant Test</b>\nBot connection OK at {datetime.now(UTC).isoformat(timespec='seconds')}"
    elif args.message:
        text = html_escape(args.message)
    elif args.run_summary:
        text = build_summary(status=args.status, repo=args.repo)
    else:
        parser.print_help()
        return

    if args.print_only:
        print(text)
        return

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID env vars missing", file=sys.stderr)
        sys.exit(2)

    responses = send_telegram(token, chat_id, text)
    for response in responses:
        message_id = response.get("result", {}).get("message_id")
        print(f"Sent. message_id={message_id}")


if __name__ == "__main__":
    main()
