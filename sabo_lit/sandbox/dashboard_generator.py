"""
Generate static HTML dashboard from live state files.

Output:
  sabo_lit/sandbox/live/dashboard.html

Reads, when present:
  - daily_ops_metrics_latest.json
  - prop_signals_latest.json
  - tracker_alerts.json
  - tracker_dashboard.md
  - automated_daily_pnl.jsonl
  - capital_executions.jsonl
  - automated_runner.log
"""
from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from pathlib import Path


HERE = Path(__file__).parent
LIVE_DIR = HERE / "live"
OUTPUT = LIVE_DIR / "dashboard.html"


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def load_json(path: Path) -> dict | list:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def load_text_tail(path: Path, n: int = 80) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]


def status_class(status: str) -> str:
    if status in {"OK", "DEMO_OK", "DEMO_OK_LIVE_NO_GO", "submitted", "filled", "closed", "dry_run_only"}:
        return "ok"
    if status in {"WARN", "LIVE_NO_GO"}:
        return "warn"
    if status in {"FAIL", "NO_GO", "error", "failure"}:
        return "bad"
    return "muted"


def table(headers: list[str], rows: list[list[object]]) -> str:
    head = "".join(f"<th>{esc(header)}</th>" for header in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def card(label: str, value: object, css_class: str = "") -> str:
    return f"<div class='card'><div class='label'>{esc(label)}</div><div class='value {css_class}'>{esc(value)}</div></div>"


def build_daily_ops_section(daily_ops: dict) -> tuple[str, list[str]]:
    decision = daily_ops.get("decision", "NO_DATA")
    delta = daily_ops.get("delta_summary", {}) if isinstance(daily_ops.get("delta_summary"), dict) else {}
    cards = [
        card("Decision", decision, status_class(str(decision))),
        card("As of", daily_ops.get("as_of", "—")),
        card("Broker", daily_ops.get("broker", "—")),
        card("Delta rows", delta.get("rows", 0)),
        card("Gross delta", f"${float(delta.get('total_delta_notional_usd', 0)):,.0f}"),
        card("Max lots/order", f"{float(delta.get('max_estimated_lots', 0)):.2f}"),
    ]
    step_rows = []
    for step in daily_ops.get("steps", []):
        status = esc(step.get("status", "?"))
        step_rows.append([
            esc(step.get("name", "?")),
            f"<span class='{status_class(step.get('status', ''))}'>{status}</span>",
            esc(step.get("detail", "")),
        ])
    section = "<h2>Daily Ops</h2><div class='grid'>" + "".join(cards) + "</div>"
    if step_rows:
        section += table(["Step", "Status", "Detail"], step_rows)
    return section, [str(decision)]


def build_signals_section(signals: dict) -> str:
    if not signals:
        return "<h2>Signals</h2><p class='muted'>No signal file found.</p>"
    summary = signals.get("summary", {}) if isinstance(signals.get("summary"), dict) else {}
    orders = signals.get("orders", []) if isinstance(signals.get("orders"), list) else []
    accounts = signals.get("accounts", []) if isinstance(signals.get("accounts"), list) else []
    non_flat = [order for order in orders if order.get("side") != "FLAT"]
    cards = [
        card("Accounts", len(accounts)),
        card("Orders", len(orders)),
        card("Non-flat", len(non_flat)),
        card("Target gross", f"${float(summary.get('total_gross_notional_usd', 0)):,.0f}"),
        card("Delta gross", f"${float(summary.get('total_delta_notional_usd', 0)):,.0f}"),
        card("Blackout", summary.get("blackout_reason") or "No"),
    ]
    order_rows = []
    for order in non_flat[:80]:
        order_rows.append([
            esc(order.get("account_id", "")),
            esc(order.get("strategy", "")),
            esc(order.get("broker_symbol", order.get("instrument", ""))),
            esc(order.get("delta_side", "")),
            f"${float(order.get('delta_notional_usd', 0) or 0):,.0f}",
            f"{float(order.get('estimated_standard_lots', 0) or 0):.4f}",
        ])
    section = "<h2>Signals</h2><div class='grid'>" + "".join(cards) + "</div>"
    if order_rows:
        section += table(["Account", "Strategy", "Symbol", "Side", "Delta USD", "Lots"], order_rows)
    return section


def build_tracker_section(alerts: list) -> str:
    rows = []
    for alert in alerts:
        rows.append([
            esc(alert.get("timestamp", "")),
            esc(alert.get("account", "")),
            esc(alert.get("strategy", "")),
            esc(alert.get("message", "")),
        ])
    section = "<h2>Tracker Alerts</h2>"
    if not rows:
        section += "<p class='ok'>No active tracker alerts.</p>"
    else:
        section += table(["Time", "Account", "Strategy", "Message"], rows)
    return section


def build_account_section(snapshots: list[dict]) -> tuple[str, list[float], list[str]]:
    if not snapshots:
        return "<h2>Capital Account</h2><p class='muted'>No Capital account snapshots found.</p>", [], []
    latest = snapshots[-1]
    currency = latest.get("currency", "")
    balance = float(latest.get("balance", 0) or 0)
    available = float(latest.get("available", 0) or 0)
    pl = float(latest.get("profit_loss", 0) or 0)
    initial = float(snapshots[0].get("balance", balance) or balance)
    delta = balance - initial
    cards = [
        card("Balance", f"{balance:,.2f} {currency}"),
        card("Available", f"{available:,.2f} {currency}"),
        card("Unrealized P&L", f"{pl:+,.2f} {currency}", "ok" if pl >= 0 else "bad"),
        card("Δ since first snapshot", f"{delta:+,.2f}", "ok" if delta >= 0 else "bad"),
    ]
    dates = [row.get("ts", "")[:19].replace("T", " ") for row in snapshots]
    balances = [float(row.get("balance", 0) or 0) for row in snapshots]
    return "<h2>Capital Account</h2><div class='grid'>" + "".join(cards) + "</div><div id='pnl-chart'></div>", balances, dates


def build_executions_section(executions: list[dict]) -> str:
    rows = []
    for row in reversed(executions[-80:]):
        action = "CLOSE" if row.get("action") == "close_all" else "OPEN"
        status = str(row.get("status", ""))
        rows.append([
            esc(row.get("ts", "")[:19].replace("T", " ")),
            esc(action),
            esc(row.get("epic", "")),
            esc(row.get("direction", "—")),
            f"{float(row.get('size', 0) or 0):.2f}" if action == "OPEN" else "—",
            f"${float(row.get('delta_usd', 0) or 0):,.0f}" if action == "OPEN" else "—",
            f"<span class='{status_class(status)}'>{esc(status)}</span>",
        ])
    section = f"<h2>Recent Executions ({len(rows)} shown / {len(executions)} total)</h2>"
    if rows:
        section += table(["Time", "Action", "Epic", "Direction", "Size", "Delta", "Status"], rows)
    else:
        section += "<p class='muted'>No execution log found.</p>"
    return section


def build_log_section(lines: list[str]) -> str:
    if not lines:
        return "<h2>Runner Log</h2><p class='muted'>No automated runner log found.</p>"
    content = "\n".join(esc(line) for line in lines)
    return f"<h2>Runner Log Tail</h2><pre>{content}</pre>"


def main() -> None:
    LIVE_DIR.mkdir(exist_ok=True)
    daily_ops = load_json(LIVE_DIR / "daily_ops_metrics_latest.json")
    signals = load_json(LIVE_DIR / "prop_signals_latest.json")
    alerts = load_json(LIVE_DIR / "tracker_alerts.json")
    snapshots = load_jsonl(LIVE_DIR / "automated_daily_pnl.jsonl")
    executions = load_jsonl(LIVE_DIR / "capital_executions.jsonl")
    log_tail = load_text_tail(LIVE_DIR / "automated_runner.log")

    daily_ops_section, _decision = build_daily_ops_section(daily_ops if isinstance(daily_ops, dict) else {})
    account_section, balances, dates = build_account_section(snapshots)
    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sabo Quant — Live Dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; max-width: 1280px; margin: 0 auto; padding: 24px; background: #0d1117; color: #c9d1d9; }}
h1, h2 {{ color: #58a6ff; }}
.subtitle {{ color: #8b949e; font-size: 13px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 14px; margin: 16px 0 24px; }}
.card {{ background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 14px; }}
.label {{ color: #8b949e; font-size: 11px; text-transform: uppercase; letter-spacing: .5px; }}
.value {{ font-size: 21px; font-weight: 700; margin-top: 5px; }}
.ok {{ color: #3fb950; }} .warn {{ color: #d29922; }} .bad {{ color: #f85149; }} .muted {{ color: #8b949e; }}
table {{ width: 100%; border-collapse: collapse; background: #161b22; border: 1px solid #30363d; border-radius: 10px; overflow: hidden; margin: 12px 0 26px; }}
th, td {{ text-align: left; padding: 9px 12px; border-bottom: 1px solid #30363d; font-size: 13px; }}
th {{ color: #8b949e; background: #1c2128; }} tr:last-child td {{ border-bottom: none; }}
pre {{ background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 14px; overflow-x: auto; color: #c9d1d9; }}
#pnl-chart {{ height: 340px; background: #161b22; border: 1px solid #30363d; border-radius: 10px; margin-bottom: 26px; }}
.footer {{ margin-top: 32px; color: #8b949e; font-size: 12px; }}
</style>
</head>
<body>
<h1>Sabo Quant Dashboard</h1>
<div class="subtitle">Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}</div>
{daily_ops_section}
{build_signals_section(signals if isinstance(signals, dict) else {})}
{build_tracker_section(alerts if isinstance(alerts, list) else [])}
{account_section}
{build_executions_section(executions)}
{build_log_section(log_tail)}
<div class="footer">Static dashboard generated by <code>dashboard_generator.py</code>. Live trading still requires verified broker symbols and clean demo execution.</div>
<script>
const dates = {json.dumps(dates)};
const balances = {json.dumps(balances)};
if (dates.length > 0) {{
  Plotly.newPlot('pnl-chart', [{{
    x: dates, y: balances, type: 'scatter', mode: 'lines+markers', name: 'Balance',
    line: {{color: '#58a6ff', width: 2.5}}, marker: {{size: 6}}
  }}], {{
    paper_bgcolor: '#161b22', plot_bgcolor: '#0d1117', font: {{color: '#c9d1d9'}},
    margin: {{t: 25, r: 25, b: 45, l: 70}}, xaxis: {{gridcolor: '#30363d'}}, yaxis: {{gridcolor: '#30363d'}}
  }}, {{responsive: true, displayModeBar: false}});
}}
</script>
</body>
</html>"""
    OUTPUT.write_text(html_doc, encoding="utf-8")
    print(f"Dashboard written: {OUTPUT}")
    print(f"  Signals: {bool(signals)}, Daily ops: {bool(daily_ops)}, Snapshots: {len(snapshots)}, Executions: {len(executions)}")


if __name__ == "__main__":
    main()
