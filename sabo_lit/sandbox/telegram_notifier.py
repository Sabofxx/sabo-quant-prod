"""
Sandbox — Telegram bot notifier for daily run summaries.

Sends HTML-formatted reports to a Telegram chat after the automated runner.
Outbound Telegram Bot API notifier (not an inbound webhook server).

Message modes:
  * Trading day (Mon-Fri, FX open at run time):
      full daily report — balance, executions, signals, positions, weekly trend
  * Weekend / market-closed run (Fri night, Sat, Sun pre-open):
      "Weekend Status" — balance, open positions snapshot, weekly summary,
      next market open ETA

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
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests


HERE = Path(__file__).parent
LIVE_DIR = HERE / "live"
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_MSG_LEN = 3800

WEEKDAY_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
WEEKDAY_SHORT_FR = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]


def html_escape(value: object) -> str:
    return html.escape(str(value), quote=False)


def chunk_html_by_lines(text: str) -> list[str]:
    """Split message into <=MAX_MSG_LEN chunks on line boundaries.

    Telegram caps HTML messages at 4096 chars. Split between lines only — never
    inside a tag. If any single line exceeds the cap, drop tags from that line
    via regex strip to avoid sending a half-tag chunk.
    """
    chunks: list[str] = []
    current = ""
    for raw_line in text.splitlines():
        line = raw_line
        if len(line) > MAX_MSG_LEN:
            line = re.sub(r"<[^>]+>", "", line)[:MAX_MSG_LEN]
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


def fmt_money(value: float, currency: str = "", show_sign: bool = True) -> str:
    sign = ""
    if show_sign and value > 0:
        sign = "+"
    return f"{sign}{value:,.2f} {currency}".strip()


def fmt_pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


# ─────────────────────────────────────────────────────────────────────────────
# Market schedule helpers
# ─────────────────────────────────────────────────────────────────────────────

def next_market_open_utc(now: datetime) -> datetime:
    """Return next FX market open UTC after `now`.

    Capital FX schedule:
      Mon-Thu 21:05 reopen, 20:59:50 close
      Fri 20:59:50 close
      Sun 21:00 open
    """
    weekday = now.weekday()  # Mon=0 ... Sun=6
    # Saturday → next open Sun 21:00
    if weekday == 5:
        sunday = now + timedelta(days=1)
        return sunday.replace(hour=21, minute=0, second=0, microsecond=0)
    # Sunday before 21:00
    if weekday == 6 and now.hour < 21:
        return now.replace(hour=21, minute=0, second=0, microsecond=0)
    # Friday after 20:59:50
    if weekday == 4 and (now.hour, now.minute) >= (20, 59):
        sunday = now + timedelta(days=2)
        return sunday.replace(hour=21, minute=0, second=0, microsecond=0)
    # Weekday 20:55 - 21:10 maintenance break
    if weekday in {0, 1, 2, 3} and (20 * 60 + 55) <= (now.hour * 60 + now.minute) < (21 * 60 + 10):
        return now.replace(hour=21, minute=5, second=0, microsecond=0)
    return now  # market already open


def is_weekend_mode(state: dict, now: datetime | None = None) -> bool:
    """Return True if message should use weekend / market-closed layout."""
    current = now or datetime.now(UTC)
    weekday = current.weekday()
    if not state.get("market_open", True):
        return True
    return weekday in {5, 6}


def fmt_market_status(state: dict, now: datetime) -> tuple[str, str]:
    """Return (emoji, line) for market status."""
    if state.get("market_open"):
        return "🟢", "Marché FX : <b>OUVERT</b>"
    reason = state.get("market_reason", "FX closed")
    next_open = next_market_open_utc(now)
    delta = next_open - now
    hours = int(delta.total_seconds() // 3600)
    minutes = int((delta.total_seconds() % 3600) // 60)
    eta = f"{hours}h{minutes:02d}m"
    open_str = next_open.strftime("%a %d/%m %H:%M UTC")
    return "🌙", f"Marché FX : <b>FERMÉ</b> ({html_escape(reason)}) · réouverture {open_str} (dans {eta})"


# ─────────────────────────────────────────────────────────────────────────────
# Sections
# ─────────────────────────────────────────────────────────────────────────────

def section_header(now: datetime, state: dict, status: str, weekend: bool) -> list[str]:
    weekday = WEEKDAY_FR[now.weekday()]
    date_str = now.strftime("%Y-%m-%d %H:%M UTC")
    if status == "failure":
        emoji = "❌"
        mode = "ÉCHEC RUN"
    elif weekend:
        emoji = "🌙"
        mode = "WEEKEND"
    else:
        emoji = "🟢"
        mode = "DAILY"
    env = (state.get("environment") or "demo").upper()
    return [
        f"<b>{emoji} SABO QUANT · {mode} · {env}</b>",
        f"<i>{weekday} {date_str}</i>",
        "",
    ]


def section_account(state: dict) -> list[str]:
    if not state:
        return ["💰 <b>Compte</b> : <i>état non disponible</i>", ""]
    currency = state.get("currency", "")
    bal = float(state.get("balance", 0))
    avail = float(state.get("available", 0))
    margin = float(state.get("margin_used", 0))
    pl = float(state.get("profit_loss", 0))
    deposit = float(state.get("deposit", 0))
    margin_pct = (margin / bal * 100) if bal else 0.0
    pl_emoji = "📈" if pl >= 0 else "📉"
    env_emoji = "🧪" if state.get("environment") == "demo" else "🔥"
    lines = [
        f"💰 <b>Compte {env_emoji} {html_escape(state.get('environment','?'))} · "
        f"{html_escape(state.get('broker','?'))}</b>",
        f"   Balance      : <b>{bal:,.2f} {html_escape(currency)}</b>",
        f"   Disponible   : {avail:,.2f} {html_escape(currency)}",
        f"   Marge utilisée: {margin:,.2f} {html_escape(currency)} ({margin_pct:.1f}% balance)",
        f"   {pl_emoji} PL ouvert   : {fmt_money(pl, str(currency))}",
    ]
    if deposit:
        lines.append(f"   Dépôt initial : {deposit:,.2f} {html_escape(currency)} "
                     f"(net total Δ : {fmt_money(bal - deposit, str(currency))})")
    return lines + [""]


def section_pl_trend(snapshots: list[dict], currency: str) -> list[str]:
    """7-day P&L trend from daily snapshots."""
    if not snapshots:
        return []
    by_date: dict[str, float] = {}
    for snap in snapshots:
        date = snap.get("date")
        if date:
            by_date[date] = float(snap.get("balance", 0))
    if len(by_date) < 2:
        return []
    dates = sorted(by_date.keys())[-8:]  # 8 days → 7 deltas
    if len(dates) < 2:
        return []
    lines = ["📊 <b>Trajectoire balance (7 derniers jours)</b>"]
    prev_bal = by_date[dates[0]]
    week_start = prev_bal
    for date in dates[1:]:
        bal = by_date[date]
        delta = bal - prev_bal
        delta_pct = (delta / prev_bal * 100) if prev_bal else 0.0
        emoji = "🟢" if delta >= 0 else "🔴"
        weekday_idx = datetime.fromisoformat(date).weekday()
        wd_short = WEEKDAY_SHORT_FR[weekday_idx]
        lines.append(
            f"   {emoji} {wd_short} {date}: {bal:,.2f} {html_escape(currency)} "
            f"({fmt_money(delta, str(currency), show_sign=True)} / {fmt_pct(delta_pct)})"
        )
        prev_bal = bal
    total_delta = by_date[dates[-1]] - week_start
    total_pct = (total_delta / week_start * 100) if week_start else 0.0
    lines.append(
        f"   <b>Σ semaine : {fmt_money(total_delta, str(currency))} ({fmt_pct(total_pct)})</b>"
    )
    return lines + [""]


def section_today_delta(snapshots: list[dict], currency: str) -> list[str]:
    """Today vs yesterday balance delta."""
    if len(snapshots) < 2:
        return []
    today = snapshots[-1]
    today_date = today.get("date")
    prev = None
    for snap in reversed(snapshots[:-1]):
        if snap.get("date") != today_date:
            prev = snap
            break
    if not prev:
        return []
    bal = float(today.get("balance", 0))
    prev_bal = float(prev.get("balance", 0))
    delta = bal - prev_bal
    delta_pct = (delta / prev_bal * 100) if prev_bal else 0.0
    emoji = "🟢" if delta >= 0 else "🔴"
    return [
        f"   {emoji} Δ jour ({html_escape(prev.get('date','prev'))}→{html_escape(today_date)}): "
        f"{fmt_money(delta, str(currency))} ({fmt_pct(delta_pct)})",
        "",
    ]


def section_positions(state: dict) -> list[str]:
    positions = state.get("positions", []) if state else []
    if not positions:
        return ["📊 <b>Positions ouvertes</b> : 0", ""]
    currency = state.get("currency", "")
    lines = [f"📊 <b>Positions ouvertes</b> : {len(positions)}"]
    total_pl = 0.0
    for pos in positions[:12]:
        direction = pos.get("direction", "?")
        size = float(pos.get("size", 0) or 0)
        open_lvl = float(pos.get("open_level", 0) or 0)
        bid = float(pos.get("current_bid", 0) or 0)
        offer = float(pos.get("current_offer", 0) or 0)
        mid = (bid + offer) / 2 if (bid and offer) else (bid or offer or open_lvl)
        pl = float(pos.get("profit_loss", 0) or 0)
        total_pl += pl
        emoji = "🟢" if pl >= 0 else "🔴"
        epic = html_escape(pos.get("epic", "?"))
        arrow = "▲" if direction == "BUY" else "▼"
        lines.append(
            f"   {emoji} {arrow} {epic} {direction} {size:,.0f} @ {open_lvl:.5f} "
            f"→ {mid:.5f} : {fmt_money(pl, str(currency))}"
        )
    if len(positions) > 12:
        lines.append(f"   … +{len(positions) - 12} autres")
    lines.append(f"   <b>PL ouvert total : {fmt_money(total_pl, str(currency))}</b>")
    return lines + [""]


def section_signals() -> list[str]:
    data = load_json(LIVE_DIR / "prop_signals_latest.json")
    if not isinstance(data, dict) or not data:
        return []
    accounts = data.get("accounts", []) if isinstance(data.get("accounts"), list) else []
    orders = data.get("orders", []) if isinstance(data.get("orders"), list) else []
    summary = data.get("summary", {}) if isinstance(data.get("summary"), dict) else {}
    n_nonflat = sum(1 for order in orders if order.get("side") != "FLAT")
    lines = [
        f"🎯 <b>Signaux générés</b> : {len(accounts)} comptes · {len(orders)} ordres · "
        f"{n_nonflat} non-flat",
        f"   Gross cible : ${float(summary.get('total_gross_notional_usd', 0)):,.0f}",
        f"   Delta exec  : ${float(summary.get('total_delta_notional_usd', 0)):,.0f}",
    ]
    if summary.get("blackout_day"):
        lines.append(f"   ⚠️ Blackout news : {html_escape(summary.get('blackout_reason', '?'))}")
    if summary.get("as_of"):
        lines.append(f"   As of : {html_escape(summary.get('as_of'))}")
    return lines + [""]


def section_executions_today(state: dict) -> list[str]:
    executions = load_jsonl(LIVE_DIR / "capital_executions.jsonl")
    today = datetime.now(UTC).date().isoformat()
    today_execs = [row for row in executions if row.get("ts", "").startswith(today)]
    if not today_execs:
        if state.get("market_open"):
            return ["⚡ <b>Exécutions du jour</b> : 0 (aucun delta dépassant seuil)", ""]
        return []  # silent when market closed — nothing to say
    n_open = sum(1 for row in today_execs
                  if row.get("status") in {"submitted", "filled", "dry_run_only"})
    n_close = sum(1 for row in today_execs
                   if row.get("status") == "closed" or row.get("action") == "close_all")
    n_err = sum(1 for row in today_execs if row.get("status") == "error")
    lines = [
        f"⚡ <b>Exécutions du jour</b> : {len(today_execs)} "
        f"(open: {n_open}, close: {n_close}, err: {n_err})"
    ]
    for row in today_execs[-10:]:
        ts = html_escape(row.get("ts", "")[11:16])
        if row.get("action") == "close_all":
            status = row.get("status", "?")
            mark = "✓" if status == "closed" else "✗"
            lines.append(f"   {ts} {mark} CLOSE {html_escape(row.get('epic', '?'))}")
        else:
            status = row.get("status", "?")
            mark = "✓" if status in {"submitted", "filled", "dry_run_only"} else "✗" \
                if status == "error" else "?"
            arrow = "▲" if row.get("direction") == "BUY" else \
                "▼" if row.get("direction") == "SELL" else "·"
            lines.append(
                f"   {ts} {mark} {arrow} {html_escape(row.get('epic', '?'))} "
                f"{html_escape(row.get('direction', '?'))} "
                f"{float(row.get('size', 0) or 0):,.0f} @ "
                f"{float(row.get('price_mid', 0) or 0):.5f}"
            )
    if len(today_execs) > 10:
        lines.append(f"   … +{len(today_execs) - 10} autres")
    return lines + [""]


def section_weekly_exec_summary() -> list[str]:
    """Aggregate executions of the last 7 days."""
    executions = load_jsonl(LIVE_DIR / "capital_executions.jsonl")
    if not executions:
        return []
    cutoff = (datetime.now(UTC) - timedelta(days=7)).date().isoformat()
    recent = [row for row in executions if row.get("ts", "")[:10] >= cutoff]
    if not recent:
        return []
    n_total = len(recent)
    n_opens = sum(1 for row in recent
                   if row.get("status") in {"submitted", "filled"}
                   and row.get("action") != "close_all")
    n_closes = sum(1 for row in recent if row.get("action") == "close_all")
    n_errs = sum(1 for row in recent if row.get("status") == "error")
    gross = sum(abs(float(row.get("delta_usd", 0) or 0)) for row in recent
                 if row.get("status") in {"submitted", "filled"})
    lines = [
        "📅 <b>Activité 7 derniers jours</b>",
        f"   Trades       : {n_total} (opens: {n_opens}, closes: {n_closes}, err: {n_errs})",
        f"   Volume gross : ${gross:,.0f}",
    ]
    return lines + [""]


def section_market_status(state: dict, now: datetime) -> list[str]:
    emoji, line = fmt_market_status(state, now)
    return [f"{emoji} {line}", ""]


def section_next_run() -> list[str]:
    now = datetime.now(UTC)
    next_run = now.replace(hour=22, minute=5, second=0, microsecond=0)
    if next_run <= now:
        next_run = next_run + timedelta(days=1)
    delta = next_run - now
    hours = int(delta.total_seconds() // 3600)
    minutes = int((delta.total_seconds() % 3600) // 60)
    weekday = WEEKDAY_FR[next_run.weekday()]
    return [f"⏰ Prochain run : <b>{weekday} {next_run.strftime('%Y-%m-%d %H:%M UTC')}</b> "
            f"(dans {hours}h{minutes:02d}m)", ""]


def section_errors_warnings() -> list[str]:
    log_file = LIVE_DIR / "automated_runner.log"
    if not log_file.exists():
        return []
    log_lines = log_file.read_text(encoding="utf-8").splitlines()
    marker_idx = None
    for index in range(len(log_lines) - 1, -1, -1):
        if "Automated daily runner started" in log_lines[index]:
            marker_idx = index
            break
    if marker_idx is None:
        return []
    run_lines = log_lines[marker_idx:]
    errors = [line for line in run_lines if any(k in line for k in ("ERROR", "FATAL", "STEP FAILED"))]
    warns = [line for line in run_lines if "WARN" in line]
    lines: list[str] = []
    if errors:
        lines.append(f"❌ <b>Erreurs</b> ({len(errors)})")
        for err in errors[:5]:
            msg = err.split("] ", 1)[-1] if "]" in err else err
            lines.append(f"   • <code>{html_escape(msg[:200])}</code>")
        lines.append("")
    elif warns:
        lines.append(f"⚠️ <b>Avertissements</b> : {len(warns)} non-fatal")
        lines.append("")
    return lines


def section_tracker_alerts() -> list[str]:
    alerts = load_json(LIVE_DIR / "tracker_alerts.json")
    if not isinstance(alerts, list) or not alerts:
        return []
    lines = [f"🚨 <b>Alertes tracker</b> : {len(alerts)}"]
    for alert in alerts[:5]:
        lines.append(
            f"   • {html_escape(alert.get('account', '?'))}: "
            f"<code>{html_escape(alert.get('message', '?'))[:180]}</code>"
        )
    return lines + [""]


def section_footer(repo: str) -> list[str]:
    safe_repo = html_escape(repo)
    dashboard_url = (
        f"https://htmlpreview.github.io/?"
        f"https://github.com/{safe_repo}/blob/main/sabo_lit/sandbox/live/dashboard.html"
    )
    return [
        "━━━━━━━━━━━━━━━━━━━",
        f"🔗 <a href=\"https://github.com/{safe_repo}/actions\">Actions</a> · "
        f"<a href=\"{dashboard_url}\">Dashboard</a> · "
        f"<a href=\"https://capital.com\">Capital.com</a>",
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Build messages
# ─────────────────────────────────────────────────────────────────────────────

def build_summary(status: str = "success",
                   repo: str | None = None) -> str:
    """Build the full HTML message for Telegram."""
    if not repo:
        repo = os.environ.get("GITHUB_REPOSITORY") or "Sabofxx/sabo-quant-prod"
    now = datetime.now(UTC)
    state = load_json(LIVE_DIR / "account_state.json")
    if not isinstance(state, dict):
        state = {}
    snapshots = load_jsonl(LIVE_DIR / "automated_daily_pnl.jsonl")
    currency = state.get("currency", "") if state else ""
    weekend = is_weekend_mode(state, now)

    lines: list[str] = []
    lines.extend(section_header(now, state, status, weekend))
    lines.extend(section_market_status(state, now))
    lines.extend(section_account(state))
    lines.extend(section_today_delta(snapshots, str(currency)))
    lines.extend(section_positions(state))
    if not weekend:
        lines.extend(section_executions_today(state))
        lines.extend(section_signals())
    else:
        lines.extend(section_weekly_exec_summary())
    lines.extend(section_pl_trend(snapshots, str(currency)))
    lines.extend(section_tracker_alerts())
    lines.extend(section_errors_warnings())
    lines.extend(section_next_run())
    lines.extend(section_footer(repo))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-summary", action="store_true",
                          help="Build comprehensive summary from live state files")
    parser.add_argument("--message", type=str, help="Send literal message")
    parser.add_argument("--status", default="success", choices=["success", "failure"])
    parser.add_argument("--test", action="store_true", help="Send test message")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"),
                          help="owner/repo for footer links (default: $GITHUB_REPOSITORY)")
    parser.add_argument("--print-only", action="store_true",
                          help="Render message without sending to Telegram")
    return parser


def parse_args() -> argparse.Namespace:
    return build_parser().parse_args()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.test:
        text = (
            f"🤖 <b>Sabo Quant Test</b>\n"
            f"Bot connection OK at {datetime.now(UTC).isoformat(timespec='seconds')}"
        )
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
