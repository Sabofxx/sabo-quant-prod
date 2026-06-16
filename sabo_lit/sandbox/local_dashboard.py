"""
Rich local dashboard for sabo-quant.

Reads every live/ state file, computes the metrics that actually matter for a
prop-firm go/no-go decision (equity curve, drawdown from peak, realized PnL,
win rate, per-pair edge, slippage, GSL stop-out rate, open positions) and
renders a single self-contained HTML page with interactive charts.

Usage:
  python local_dashboard.py            # write live/dashboard_full.html
  python local_dashboard.py --serve    # build, serve on :8765, open browser
  python local_dashboard.py --serve --port 9000

The HTML embeds all data inline (one JSON blob) and pulls Chart.js from a CDN,
so the file works by double-click when online; --serve is only a convenience.
"""
from __future__ import annotations

import argparse
import html
import json
import statistics
import webbrowser
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).parent
LIVE_DIR = HERE / "live"
OUTPUT = LIVE_DIR / "dashboard_full.html"

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD"]


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def load_json(path: Path) -> dict | list:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def pip_factor(epic: str) -> float:
    return 100.0 if "JPY" in (epic or "") else 10000.0


def compute() -> dict:
    """Aggregate all live data into a single JSON-serializable payload."""
    snaps = load_jsonl(LIVE_DIR / "automated_daily_pnl.jsonl")
    realized = load_jsonl(LIVE_DIR / "realized_pnl.jsonl")
    slippage = load_jsonl(LIVE_DIR / "slippage.jsonl")
    execs = load_jsonl(LIVE_DIR / "capital_executions.jsonl")
    state = load_json(LIVE_DIR / "account_state.json")
    intraday = load_json(LIVE_DIR / "intraday_risk_state.json")
    signals = load_json(LIVE_DIR / "prop_signals_latest.json")
    if not isinstance(state, dict):
        state = {}

    currency = state.get("currency", "") if state else ""

    # ---- equity curve (one balance per date, last wins) ----
    by_date: dict[str, dict] = {}
    for s in snaps:
        d = s.get("date")
        if d:
            by_date[d] = s
    dates = sorted(by_date)
    equity = [{"date": d,
               "balance": float(by_date[d].get("balance", 0) or 0),
               "deposit": float(by_date[d].get("deposit", 0) or 0)}
              for d in dates]

    # current balance (prefer intraday > state > last snapshot)
    cur_bal = 0.0
    if isinstance(intraday, dict) and intraday.get("balance"):
        cur_bal = float(intraday["balance"])
    elif state.get("balance"):
        cur_bal = float(state["balance"])
    elif equity:
        cur_bal = equity[-1]["balance"]

    # ---- drawdown from running peak ----
    balances = [e["balance"] for e in equity if e["balance"] > 0]
    if cur_bal > 0:
        balances.append(cur_bal)
    peak = max(balances) if balances else 0.0
    dd_pct = (cur_bal - peak) / peak * 100 if peak else 0.0
    dd_curve = []
    run_peak = 0.0
    for e in equity:
        run_peak = max(run_peak, e["balance"])
        dd = (e["balance"] - run_peak) / run_peak * 100 if run_peak else 0.0
        dd_curve.append({"date": e["date"], "dd": round(dd, 3)})

    # ---- realized PnL ----
    profits = [float(r.get("profit", 0) or 0) for r in realized]
    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p < 0]
    realized_total = sum(profits)
    win_rate = len(wins) / len(profits) * 100 if profits else 0.0
    avg_win = statistics.mean(wins) if wins else 0.0
    avg_loss = statistics.mean(losses) if losses else 0.0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses else 0.0

    realized_by_day: dict[str, float] = defaultdict(float)
    for r in realized:
        ts = r.get("ts", "")[:10]
        if ts:
            realized_by_day[ts] += float(r.get("profit", 0) or 0)
    realized_daily = [{"date": d, "pnl": round(realized_by_day[d], 2)}
                      for d in sorted(realized_by_day)]

    realized_by_pair: dict[str, dict] = {}
    for r in realized:
        ep = r.get("epic", "?")
        b = realized_by_pair.setdefault(ep, {"pnl": 0.0, "n": 0, "w": 0})
        p = float(r.get("profit", 0) or 0)
        b["pnl"] += p
        b["n"] += 1
        if p > 0:
            b["w"] += 1
    pair_stats = [{"pair": k, "pnl": round(v["pnl"], 2), "n": v["n"],
                   "win_rate": round(v["w"] / v["n"] * 100, 1) if v["n"] else 0.0}
                  for k, v in sorted(realized_by_pair.items(), key=lambda kv: kv[1]["pnl"])]

    # ---- slippage ----
    slip_pips = [float(r.get("slippage_pips", 0) or 0) for r in slippage]
    slip_stats = {}
    if slip_pips:
        slip_stats = {
            "n": len(slip_pips),
            "mean": round(statistics.mean(slip_pips), 3),
            "median": round(statistics.median(slip_pips), 3),
            "p90": round(sorted(slip_pips)[int(len(slip_pips) * 0.9)], 3),
            "max": round(max(slip_pips), 3),
        }

    # ---- GSL stop-out rate: opens vs next-day close_all per day ----
    opens: dict[str, int] = defaultdict(int)
    closes: dict[str, int] = defaultdict(int)
    for r in execs:
        ts = r.get("ts", "")[:10]
        if not ts:
            continue
        if r.get("action") == "close_all":
            closes[ts] += 1
        elif r.get("status") == "submitted":
            opens[ts] += 1
    stopout = []
    odates = sorted(opens)
    for i, d in enumerate(odates):
        nxt = odates[i + 1] if i + 1 < len(odates) else None
        closed_next = closes.get(nxt, 0) if nxt else 0
        op = opens[d]
        rate = round((op - closed_next) / op * 100, 1) if (op and nxt) else None
        stopout.append({"date": d, "opened": op,
                        "closed_next": closed_next if nxt else None,
                        "stopout_pct": rate})

    # ---- open positions ----
    positions = []
    for p in (state.get("positions", []) if isinstance(state, dict) else []):
        positions.append({
            "epic": p.get("epic"),
            "direction": p.get("direction"),
            "size": p.get("size"),
            "open_level": p.get("open_level"),
            "profit_loss": p.get("profit_loss"),
        })

    # ---- latest signals ----
    sig_rows = []
    if isinstance(signals, dict):
        seen = set()
        for o in signals.get("orders", []):
            ins = o.get("instrument")
            if ins in PAIRS and ins not in seen:
                seen.add(ins)
                sig_rows.append({"pair": ins, "side": o.get("side"),
                                 "signal": round(float(o.get("signal", 0) or 0), 3),
                                 "price": o.get("last_price")})

    deposit = float(state.get("deposit", 0) or 0) if state else 0.0
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "currency": currency,
        "environment": state.get("environment", "?") if state else "?",
        "kpi": {
            "balance": round(cur_bal, 2),
            "peak": round(peak, 2),
            "dd_pct": round(dd_pct, 2),
            "deposit": round(deposit, 2),
            "open_pl": round(float(state.get("profit_loss", 0) or 0), 2) if state else 0.0,
            "realized_total": round(realized_total, 2),
            "n_trades": len(profits),
            "win_rate": round(win_rate, 1),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "margin_used": round(float(state.get("margin_used", 0) or 0), 2) if state else 0.0,
            "n_positions": len(positions),
        },
        "equity": equity,
        "dd_curve": dd_curve,
        "realized_daily": realized_daily,
        "pair_stats": pair_stats,
        "slip_stats": slip_stats,
        "slip_hist": slip_pips,
        "stopout": stopout,
        "positions": positions,
        "signals": sig_rows,
    }


def esc(v: object) -> str:
    return html.escape(str(v), quote=True)


def render(data: dict) -> str:
    blob = json.dumps(data)
    cur = esc(data["currency"])
    return f"""<!doctype html>
<html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SABO Quant — Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
 :root {{ --bg:#0d1117; --card:#161b22; --bd:#30363d; --tx:#e6edf3; --mut:#8b949e;
         --grn:#3fb950; --red:#f85149; --org:#d29922; --blu:#58a6ff; }}
 *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--tx);
   font:14px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif}}
 header{{padding:18px 24px;border-bottom:1px solid var(--bd);display:flex;
   justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px}}
 h1{{font-size:18px;margin:0}} .mut{{color:var(--mut);font-size:12px}}
 .wrap{{padding:20px 24px;max-width:1280px;margin:0 auto}}
 .kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px}}
 .kpi{{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:14px}}
 .kpi .lbl{{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.04em}}
 .kpi .val{{font-size:22px;font-weight:600;margin-top:4px}}
 .grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
 @media(max-width:880px){{.grid{{grid-template-columns:1fr}}}}
 .card{{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:16px;margin-bottom:16px}}
 .card h2{{font-size:13px;margin:0 0 12px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}}
 table{{width:100%;border-collapse:collapse;font-size:13px}}
 th,td{{text-align:right;padding:6px 8px;border-bottom:1px solid var(--bd)}}
 th:first-child,td:first-child{{text-align:left}}
 .grn{{color:var(--grn)}} .red{{color:var(--red)}} .org{{color:var(--org)}}
 canvas{{max-height:260px}}
</style></head><body>
<header>
  <div><h1>📊 SABO Quant — Dashboard</h1>
    <div class="mut" id="sub"></div></div>
  <div class="mut" id="gen"></div>
</header>
<div class="wrap">
  <div class="kpis" id="kpis"></div>
  <div class="grid">
    <div class="card"><h2>Equity vs dépôt</h2><canvas id="eq"></canvas></div>
    <div class="card"><h2>Drawdown depuis le pic (%)</h2><canvas id="dd"></canvas></div>
    <div class="card"><h2>PnL réalisé par jour</h2><canvas id="rd"></canvas></div>
    <div class="card"><h2>PnL réalisé par paire</h2><canvas id="pp"></canvas></div>
    <div class="card"><h2>Slippage (pips)</h2><canvas id="sl"></canvas></div>
    <div class="card"><h2>Taux de stop-out GSL (%)</h2><canvas id="so"></canvas></div>
  </div>
  <div class="grid">
    <div class="card"><h2>Positions ouvertes</h2><div id="pos"></div></div>
    <div class="card"><h2>Signaux du jour</h2><div id="sig"></div></div>
  </div>
  <div class="card"><h2>Détail par paire</h2><div id="ptab"></div></div>
</div>
<script>
const D = {blob};
const CUR = "{cur}";
const money = v => (v>=0?"+":"") + v.toLocaleString("fr-FR",{{maximumFractionDigits:2}}) + " " + CUR;
const pct = v => (v>=0?"+":"") + v.toFixed(2) + "%";
const cls = v => v>=0?"grn":"red";
document.getElementById("gen").textContent = "généré " + D.generated_at.replace("T"," ").replace("+00:00"," UTC");
document.getElementById("sub").textContent = D.environment.toUpperCase() + " · " + CUR;

const k = D.kpi;
const ddCls = k.dd_pct>-1?"grn":k.dd_pct>-5?"org":"red";
const kpis = [
  ["Balance", money(k.balance), ""],
  ["Pic equity", k.peak.toLocaleString("fr-FR"), ""],
  ["Drawdown / pic", pct(k.dd_pct), ddCls],
  ["PnL réalisé", money(k.realized_total), cls(k.realized_total)],
  ["Win rate", k.win_rate.toFixed(0)+"% ("+k.n_trades+")", ""],
  ["Profit factor", k.profit_factor.toFixed(2), k.profit_factor>=1?"grn":"red"],
  ["Avg win / loss", money(k.avg_win)+" / "+money(k.avg_loss), ""],
  ["PL ouvert (latent)", money(k.open_pl), cls(k.open_pl)],
  ["Marge utilisée", k.margin_used.toLocaleString("fr-FR")+" "+CUR, ""],
  ["Positions", String(k.n_positions), ""],
];
document.getElementById("kpis").innerHTML = kpis.map(([l,v,c]) =>
  `<div class="kpi"><div class="lbl">${{l}}</div><div class="val ${{c||''}}">${{v}}</div></div>`).join("");

const gridC = "#30363d", txC = "#8b949e";
Chart.defaults.color = txC; Chart.defaults.borderColor = gridC; Chart.defaults.font.size = 11;
const baseOpts = {{responsive:true, plugins:{{legend:{{labels:{{boxWidth:12}}}}}},
  scales:{{x:{{grid:{{color:gridC}}}}, y:{{grid:{{color:gridC}}}}}}}};

new Chart(eq, {{type:"line", data:{{labels:D.equity.map(e=>e.date),
  datasets:[
   {{label:"Balance", data:D.equity.map(e=>e.balance), borderColor:"#58a6ff", tension:.2, pointRadius:0}},
   {{label:"Dépôt", data:D.equity.map(e=>e.deposit), borderColor:"#8b949e", borderDash:[4,4], tension:.2, pointRadius:0}}
  ]}}, options:baseOpts}});

new Chart(dd, {{type:"line", data:{{labels:D.dd_curve.map(e=>e.date),
  datasets:[{{label:"DD %", data:D.dd_curve.map(e=>e.dd), borderColor:"#f85149",
    backgroundColor:"rgba(248,81,73,.15)", fill:true, tension:.2, pointRadius:0}}]}},
  options:baseOpts}});

new Chart(rd, {{type:"bar", data:{{labels:D.realized_daily.map(e=>e.date),
  datasets:[{{label:"PnL", data:D.realized_daily.map(e=>e.pnl),
    backgroundColor:D.realized_daily.map(e=>e.pnl>=0?"#3fb950":"#f85149")}}]}},
  options:baseOpts}});

new Chart(pp, {{type:"bar", data:{{labels:D.pair_stats.map(e=>e.pair),
  datasets:[{{label:"PnL", data:D.pair_stats.map(e=>e.pnl),
    backgroundColor:D.pair_stats.map(e=>e.pnl>=0?"#3fb950":"#f85149")}}]}},
  options:{{...baseOpts, indexAxis:"y"}}}});

// slippage histogram (bucket 0.25 pip)
const hb = {{}}; D.slip_hist.forEach(v=>{{const b=(Math.floor(v/0.25)*0.25).toFixed(2); hb[b]=(hb[b]||0)+1;}});
const hk = Object.keys(hb).sort((a,b)=>a-b);
new Chart(sl, {{type:"bar", data:{{labels:hk,
  datasets:[{{label:"fills", data:hk.map(k=>hb[k]), backgroundColor:"#58a6ff"}}]}},
  options:baseOpts}});

new Chart(so, {{type:"line", data:{{labels:D.stopout.map(e=>e.date),
  datasets:[{{label:"stop-out %", data:D.stopout.map(e=>e.stopout_pct), borderColor:"#d29922",
    backgroundColor:"rgba(210,153,34,.15)", fill:true, tension:.2, spanGaps:true}}]}},
  options:baseOpts}});

function tbl(el, rows, head, fmt){{
  if(!rows.length){{document.getElementById(el).innerHTML='<div class="mut">aucune donnée</div>';return;}}
  document.getElementById(el).innerHTML =
    '<table><thead><tr>'+head.map(h=>`<th>${{h}}</th>`).join('')+'</tr></thead><tbody>'+
    rows.map(fmt).join('')+'</tbody></table>';
}}
tbl("pos", D.positions, ["Paire","Sens","Taille","Entrée","PL"], p=>
  `<tr><td>${{p.epic||''}}</td><td>${{p.direction||''}}</td><td>${{(p.size||0).toLocaleString("fr-FR")}}</td>`+
  `<td>${{p.open_level||''}}</td><td class="${{cls(p.profit_loss||0)}}">${{money(p.profit_loss||0)}}</td></tr>`);
tbl("sig", D.signals, ["Paire","Sens","Signal","Prix"], s=>
  `<tr><td>${{s.pair}}</td><td>${{s.side}}</td><td>${{s.signal}}</td><td>${{s.price}}</td></tr>`);
tbl("ptab", D.pair_stats, ["Paire","PnL réalisé","Trades","Win rate"], p=>
  `<tr><td>${{p.pair}}</td><td class="${{cls(p.pnl)}}">${{money(p.pnl)}}</td><td>${{p.n}}</td><td>${{p.win_rate}}%</td></tr>`);
</script></body></html>"""


def main() -> None:
    global LIVE_DIR, OUTPUT
    ap = argparse.ArgumentParser(description="Rich local sabo-quant dashboard")
    ap.add_argument("--serve", action="store_true", help="serve + open in browser")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--live-dir", default="", help="state dir to read (e.g. live/fxtsm); default global live/")
    args = ap.parse_args()

    if args.live_dir:
        LIVE_DIR = Path(args.live_dir) if Path(args.live_dir).is_absolute() else HERE / args.live_dir
        OUTPUT = LIVE_DIR / "dashboard_full.html"

    data = compute()
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render(data), encoding="utf-8")
    print(f"Wrote {OUTPUT} ({OUTPUT.stat().st_size:,} bytes)")
    k = data["kpi"]
    print(f"  balance={k['balance']:,.2f} {data['currency']} | DD/peak={k['dd_pct']:+.2f}% "
          f"| realized={k['realized_total']:+,.2f} | win={k['win_rate']:.0f}% ({k['n_trades']})")

    if args.serve:
        import functools
        import http.server
        import socketserver
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(LIVE_DIR))
        url = f"http://localhost:{args.port}/{OUTPUT.name}"
        with socketserver.TCPServer(("", args.port), handler) as httpd:
            print(f"Serving {url}  (Ctrl+C to stop)")
            webbrowser.open(url)
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\nstopped")


if __name__ == "__main__":
    main()
