"""
Sandbox — intraday risk monitor.

Runs lightweight checks on Capital.com positions outside the daily 22:05 cycle.
Silent unless a risk threshold is breached, then pings Telegram.

Thresholds (override via env):
  RISK_DD_PCT           - unrealized PL / balance below this → alert  (default -3.0)
  RISK_SINGLE_POS_PCT   - worst single position / balance below this → alert  (default -1.5)
  RISK_MARGIN_PCT       - margin used / balance above this → alert  (default 70.0)

Env vars required:
  CAPITAL_API_KEY, CAPITAL_IDENTIFIER, CAPITAL_API_PASSWORD, CAPITAL_ENVIRONMENT
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

Exits 0 on clear, 0 on breach (alert sent), non-zero only on infrastructure error.
A breach is normal operational signal — it should not fail the workflow.
"""
from __future__ import annotations

import json
import os
import sys
import html
from datetime import UTC, datetime
from pathlib import Path

from capital_connector import CapitalClient
from telegram_notifier import send_telegram


HERE = Path(__file__).parent
LIVE_DIR = HERE / "live"
ALERT_LOG = LIVE_DIR / "intraday_risk_alerts.jsonl"
STATE_FILE = LIVE_DIR / "intraday_risk_state.json"


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def fmt_money(value: float, currency: str = "") -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,.2f} {currency}".strip()


def html_escape(value: object) -> str:
    return html.escape(str(value), quote=False)


def collect_state(client: CapitalClient) -> dict:
    acc = client.account_summary()
    bal_block = acc.get("balance", {})
    balance = float(bal_block.get("balance", 0) or 0)
    available = float(bal_block.get("available", 0) or 0)
    margin_used = balance - available
    unrealized = float(bal_block.get("profitLoss", 0) or 0)

    positions = []
    worst_pos = None
    worst_pl = 0.0
    for entry in client.get_positions():
        pos = entry.get("position", {})
        market = entry.get("market", {})
        pl = float(pos.get("profit", 0) or 0)
        info = {
            "epic": market.get("epic"),
            "direction": pos.get("direction"),
            "size": float(pos.get("size", 0) or 0),
            "open_level": float(pos.get("level", 0) or 0),
            "current_bid": float(market.get("bid", 0) or 0),
            "current_offer": float(market.get("offer", 0) or 0),
            "profit_loss": pl,
        }
        positions.append(info)
        if pl < worst_pl:
            worst_pl = pl
            worst_pos = info

    return {
        "currency": acc.get("currency", ""),
        "environment": os.environ.get("CAPITAL_ENVIRONMENT", "demo"),
        "balance": balance,
        "available": available,
        "margin_used": margin_used,
        "unrealized_pl": unrealized,
        "positions": positions,
        "worst_position": worst_pos,
        "worst_pl": worst_pl,
    }


def evaluate(state: dict, dd_pct: float, single_pct: float, margin_pct: float) -> list[dict]:
    """Return list of breach dicts: [{kind, severity, message, value, threshold}]"""
    breaches = []
    balance = state["balance"]
    if balance <= 0:
        return breaches
    pl_pct = state["unrealized_pl"] / balance * 100
    if pl_pct < dd_pct:
        breaches.append({
            "kind": "drawdown",
            "severity": "high",
            "value_pct": pl_pct,
            "threshold_pct": dd_pct,
            "message": f"Unrealized PL {pl_pct:+.2f}% of balance (threshold {dd_pct:+.2f}%)",
        })
    worst_pos_pct = (state["worst_pl"] / balance * 100) if balance else 0.0
    if worst_pos_pct < single_pct:
        epic = state["worst_position"]["epic"] if state["worst_position"] else "?"
        breaches.append({
            "kind": "single_position",
            "severity": "medium",
            "value_pct": worst_pos_pct,
            "threshold_pct": single_pct,
            "epic": epic,
            "message": f"Worst position {epic} {worst_pos_pct:+.2f}% (threshold {single_pct:+.2f}%)",
        })
    margin_used_pct = state["margin_used"] / balance * 100
    if margin_used_pct > margin_pct:
        breaches.append({
            "kind": "margin",
            "severity": "medium",
            "value_pct": margin_used_pct,
            "threshold_pct": margin_pct,
            "message": f"Margin used {margin_used_pct:.1f}% > {margin_pct:.1f}%",
        })
    return breaches


def exposure_rows(state: dict) -> list[tuple[str, dict[str, float]]]:
    by_epic: dict[str, dict[str, float]] = {}
    for pos in state["positions"]:
        epic = str(pos.get("epic") or "?")
        row = by_epic.setdefault(epic, {"buy": 0.0, "sell": 0.0, "pl": 0.0, "count": 0.0})
        size = float(pos.get("size", 0) or 0)
        if pos.get("direction") == "BUY":
            row["buy"] += size
        elif pos.get("direction") == "SELL":
            row["sell"] += size
        row["pl"] += float(pos.get("profit_loss", 0) or 0)
        row["count"] += 1
    return sorted(by_epic.items(), key=lambda item: abs(item[1]["buy"] - item[1]["sell"]), reverse=True)


def dashboard_url() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY") or "Sabofxx/sabo-quant-prod"
    return (
        "https://htmlpreview.github.io/?"
        f"https://github.com/{repo}/blob/main/sabo_lit/sandbox/live/dashboard.html"
    )


def risk_label(state: dict, breaches: list[dict]) -> str:
    if breaches:
        return "ALERTE"
    balance = state["balance"]
    margin_pct = state["margin_used"] / balance * 100 if balance else 0.0
    pl_pct = state["unrealized_pl"] / balance * 100 if balance else 0.0
    if margin_pct >= 70 or pl_pct <= -3:
        return "ÉLEVÉ"
    if margin_pct >= 45 or pl_pct <= -1:
        return "À SURVEILLER"
    return "OK"


def build_alert_html(state: dict, breaches: list[dict]) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    env = state["environment"].upper()
    currency = state["currency"]
    lines = [
        f"<b>🚨 SABO QUANT · ALERTE INTRADAY · {env}</b>",
        f"<i>{now}</i>",
        "",
        f"💰 Balance : <b>{state['balance']:,.2f} {currency}</b> · "
        f"PL ouvert : {fmt_money(state['unrealized_pl'], currency)}",
        f"   Marge utilisée : {state['margin_used']:,.2f} {currency} "
        f"({state['margin_used'] / state['balance'] * 100:.1f}% balance)",
        "",
        "<b>Seuils franchis :</b>",
    ]
    for breach in breaches:
        emoji = "🔴" if breach["severity"] == "high" else "🟠"
        lines.append(f"   {emoji} {breach['message']}")
    if state["positions"]:
        lines.append("")
        lines.append("<b>Positions ouvertes :</b>")
        for pos in sorted(state["positions"], key=lambda p: p["profit_loss"])[:10]:
            pl = pos["profit_loss"]
            mark = "🟢" if pl >= 0 else "🔴"
            arrow = "▲" if pos["direction"] == "BUY" else "▼"
            lines.append(
                f"   {mark} {arrow} {pos['epic']} {pos['direction']} "
                f"{pos['size']:,.0f} @ {pos['open_level']:.5f} → "
                f"{fmt_money(pl, currency)}"
            )
    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━",
        "⚠️ Vérifie le compte Capital.com OU laisse tourner si dans les budgets risque acceptés.",
    ])
    return "\n".join(lines)


def build_status_html(state: dict, breaches: list[dict] | None = None) -> str:
    breaches = breaches or []
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    env = state["environment"].upper()
    currency = state["currency"]
    balance = state["balance"]
    margin_pct = state["margin_used"] / balance * 100 if balance else 0.0
    pl_pct = state["unrealized_pl"] / balance * 100 if balance else 0.0
    label = risk_label(state, breaches)
    icon = "🚨" if breaches else "🟢" if label == "OK" else "🟠"
    lines = [
        f"<b>{icon} SABO QUANT · STATUS INTRADAY · {env}</b>",
        f"<i>{now}</i>",
        "",
        f"État risque : <b>{label}</b>",
        f"💰 Balance : <b>{balance:,.2f} {html_escape(currency)}</b>",
        f"   Disponible : {state['available']:,.2f} {html_escape(currency)}",
        f"   Marge      : {state['margin_used']:,.2f} {html_escape(currency)} ({margin_pct:.1f}%)",
        f"   PL ouvert  : {fmt_money(state['unrealized_pl'], currency)} ({pl_pct:+.2f}%)",
        f"   Positions  : {len(state['positions'])}",
        "",
    ]
    if breaches:
        lines.append("<b>Seuils franchis :</b>")
        for breach in breaches:
            mark = "🔴" if breach["severity"] == "high" else "🟠"
            lines.append(f"   {mark} {html_escape(breach['message'])}")
        lines.append("")

    rows = exposure_rows(state)
    if rows:
        lines.append("<b>Exposition nette :</b>")
        for epic, row in rows[:6]:
            net = row["buy"] - row["sell"]
            direction = "BUY" if net > 0 else "SELL" if net < 0 else "FLAT"
            mark = "🟢" if row["pl"] >= 0 else "🔴"
            lines.append(
                f"   {mark} {html_escape(epic)} {direction} {abs(net):,.0f} units · "
                f"{int(row['count'])} pos · {fmt_money(row['pl'], currency)}"
            )
        lines.append("")

    if state["positions"]:
        worst = sorted(state["positions"], key=lambda p: p["profit_loss"])[:3]
        lines.append("<b>Pires positions :</b>")
        for pos in worst:
            arrow = "▲" if pos["direction"] == "BUY" else "▼"
            lines.append(
                f"   🔴 {arrow} {html_escape(pos['epic'])} {html_escape(pos['direction'])} "
                f"{pos['size']:,.0f} → {fmt_money(pos['profit_loss'], currency)}"
            )
        lines.append("")

    lines.extend([
        "━━━━━━━━━━━━━━━━━━━",
        f"🔗 <a href=\"{dashboard_url()}\">Dashboard HTML</a>",
    ])
    return "\n".join(lines)


def write_state(state: dict, breaches: list[dict]) -> None:
    LIVE_DIR.mkdir(exist_ok=True)
    record = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "environment": state["environment"],
        "balance": state["balance"],
        "available": state["available"],
        "margin_used": state["margin_used"],
        "unrealized_pl": state["unrealized_pl"],
        "positions_count": len(state["positions"]),
        "breaches": breaches,
    }
    STATE_FILE.write_text(json.dumps(record, indent=2), encoding="utf-8")


def log_alert(breaches: list[dict], state: dict) -> None:
    LIVE_DIR.mkdir(exist_ok=True)
    record = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "environment": state["environment"],
        "balance": state["balance"],
        "unrealized_pl": state["unrealized_pl"],
        "breaches": breaches,
    }
    with ALERT_LOG.open("a") as f:
        f.write(json.dumps(record) + "\n")


def main() -> None:
    api_key = os.environ.get("CAPITAL_API_KEY")
    identifier = os.environ.get("CAPITAL_IDENTIFIER")
    password = os.environ.get("CAPITAL_API_PASSWORD")
    env = os.environ.get("CAPITAL_ENVIRONMENT", "demo")
    if not all([api_key, identifier, password]):
        print("ERROR: missing CAPITAL_* env vars", file=sys.stderr)
        sys.exit(2)

    dd_pct = env_float("RISK_DD_PCT", -3.0)
    single_pct = env_float("RISK_SINGLE_POS_PCT", -1.5)
    margin_pct = env_float("RISK_MARGIN_PCT", 70.0)
    status_mode = os.environ.get("RISK_STATUS_MODE", "alert_only").strip().lower()

    client = CapitalClient(api_key, identifier, password, env)
    try:
        client.login()
    except Exception as exc:
        print(f"ERROR: Capital login failed: {exc}", file=sys.stderr)
        sys.exit(3)

    state = collect_state(client)
    breaches = evaluate(state, dd_pct, single_pct, margin_pct)
    write_state(state, breaches)

    if not breaches:
        print(f"OK: no breach. PL={state['unrealized_pl']:+.2f} {state['currency']} "
              f"margin={state['margin_used']:,.2f}/{state['balance']:,.2f}")
        if status_mode not in {"always", "heartbeat"}:
            return
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            print("WARN: Telegram env missing — status not pushed", file=sys.stderr)
            return
        try:
            send_telegram(token, chat_id, build_status_html(state))
            print("Telegram status sent.")
        except Exception as exc:
            print(f"ERROR: Telegram status send failed: {exc}", file=sys.stderr)
            sys.exit(4)
        return

    print(f"BREACH x{len(breaches)}: {[b['kind'] for b in breaches]}")
    log_alert(breaches, state)

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("WARN: Telegram env missing — breach logged but not pushed", file=sys.stderr)
        return

    text = build_status_html(state, breaches)
    try:
        send_telegram(token, chat_id, text)
        print("Telegram alert sent.")
    except Exception as exc:
        print(f"ERROR: Telegram send failed: {exc}", file=sys.stderr)
        sys.exit(4)


if __name__ == "__main__":
    main()
