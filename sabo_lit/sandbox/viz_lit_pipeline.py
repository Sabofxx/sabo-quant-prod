"""
Sandbox viz — render the LIT pipeline eval as an interactive HTML.

Reads ``sandbox/eval_lit_pipeline.json`` (produced by
``eval_lit_pipeline.py``) and writes ``sandbox/viz_lit_pipeline.html``.

Layers:
  * candlestick chart of XAUUSD M5 over the eval window
  * each InducementEvent's parent zone as a semi-transparent rectangle
    over [induced_at, window_end], coloured by direction (red bearish,
    green bullish) — always visible (alpha 0.15)
  * a diamond at the inducement timestamp + mid-band price
  * gate-confirmed setups as DIRECTION-aware triangles:
      * bullish (long)  → triangle-up,   bright green
      * bearish (short) → triangle-down, bright red
    Phase label read from the setup itself (no hardcode).
  * Each gate marker annotated next to it with compact P&L:
    ``"+45p / +80p"`` for pnl@50 / pnl@100 candles ahead.
  * Rich hover per gate: phase, direction, entry, pnl@50, pnl@100.

P&L computation: forward-look the candles list to find close at
T+50 and T+100 candles, compute pnl_pips = (P - P0) for bullish or
(P0 - P) for bearish. Pip size hardcoded for XAUUSD (0.01). Setups
whose horizon exceeds the window get ``None`` for that horizon.

Stats printed at end of script.

Sandbox / throwaway. Pure read of the JSON payload, no recompute.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path

import plotly.graph_objects as go


IN_JSON = Path(__file__).parent / "eval_lit_pipeline.json"
OUT_HTML = Path(__file__).parent / "viz_lit_pipeline.html"

PIP_SIZE = 0.01     # XAUUSD
HORIZONS = (50, 100)

DIRECTION_COLOR_ZONE = {
    "bearish": "#d62728",  # red
    "bullish": "#2ca02c",  # green
}
DIRECTION_COLOR_GATE = {
    "bearish": "#ff1744",  # bright red
    "bullish": "#00c853",  # bright green
}
DIRECTION_SYMBOL = {
    "bearish": "triangle-down",
    "bullish": "triangle-up",
}


def _iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _pnl_pips(
    candles: list[dict],
    entry_idx: int,
    horizon: int,
    direction: str,
    entry: float,
) -> float | None:
    target_idx = entry_idx + horizon
    if target_idx >= len(candles):
        return None
    future_close = candles[target_idx]["c"]
    if direction == "bullish":
        return (future_close - entry) / PIP_SIZE
    return (entry - future_close) / PIP_SIZE


def _pnl_label(pnl: float | None) -> str:
    if pnl is None:
        return "—"
    sign = "+" if pnl >= 0 else ""
    return f"{sign}{pnl:.0f}p"


def main() -> None:
    data = json.loads(IN_JSON.read_text())

    candles = data["candles"]
    inducements = data["inducements"]
    setups = data["setups"]
    window_end = _iso(data["window"]["end"])

    # Build a timestamp -> idx map ONCE; used to anchor setups and zones.
    ts_to_idx = {c["t"]: i for i, c in enumerate(candles)}

    times = [_iso(c["t"]) for c in candles]
    opens = [c["o"] for c in candles]
    highs = [c["h"] for c in candles]
    lows = [c["l"] for c in candles]
    closes = [c["c"] for c in candles]

    fig = go.Figure()

    # ---- candles -----------------------------------------------------
    fig.add_trace(
        go.Candlestick(
            x=times, open=opens, high=highs, low=lows, close=closes,
            name="XAUUSD M5",
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
        )
    )

    # ---- inducement parent zones: semi-transparent, always visible ---
    legend_emitted: set[str] = set()
    for e in inducements:
        induced_at = _iso(e["timestamp"])
        color = DIRECTION_COLOR_ZONE.get(e["direction"], "#888888")
        fig.add_shape(
            type="rect",
            xref="x", yref="y",
            x0=induced_at, x1=window_end,
            y0=e["zone_lower"], y1=e["zone_upper"],
            line=dict(color=color, width=1),
            fillcolor=color, opacity=0.15,
            layer="below",
        )
        legend_key = f"inducement_zone_{e['direction']}"
        if legend_key not in legend_emitted:
            legend_emitted.add(legend_key)
            mid = (e["zone_upper"] + e["zone_lower"]) / 2
            fig.add_trace(
                go.Scatter(
                    x=[induced_at], y=[mid],
                    mode="markers",
                    marker=dict(color=color, size=10, symbol="square"),
                    name=f"inducement zone ({e['direction']})",
                    showlegend=True,
                    opacity=0.6,
                )
            )

    # ---- inducement event dots ---------------------------------------
    for direction in ("bearish", "bullish"):
        es = [e for e in inducements if e["direction"] == direction]
        if not es:
            continue
        fig.add_trace(
            go.Scatter(
                x=[_iso(e["timestamp"]) for e in es],
                y=[(e["zone_upper"] + e["zone_lower"]) / 2 for e in es],
                mode="markers",
                marker=dict(
                    color=DIRECTION_COLOR_ZONE[direction],
                    size=9, symbol="diamond",
                ),
                name=f"inducement ({direction})",
                hovertext=[
                    (
                        f"inducement<br>"
                        f"ts={e['timestamp']}<br>"
                        f"dir={e['direction']}<br>"
                        f"conf={e['confidence']:.2f}<br>"
                        f"zone={e['zone_kind']}<br>"
                        f"band=[{e['zone_lower']:.2f}, {e['zone_upper']:.2f}]"
                    )
                    for e in es
                ],
                hoverinfo="text",
            )
        )

    # ---- gate-confirmed setups: P&L + direction-aware markers -------
    # Enrich each setup with pnl@H and entry_idx.
    enriched: list[dict] = []
    for s in setups:
        entry_idx = ts_to_idx.get(s["confirmed_at"])
        pnls: dict[int, float | None] = {}
        if entry_idx is None:
            for h in HORIZONS:
                pnls[h] = None
        else:
            for h in HORIZONS:
                pnls[h] = _pnl_pips(
                    candles, entry_idx, h,
                    s["direction"], s["hypothetical_entry"],
                )
        enriched.append({**s, "_pnls": pnls})

    # One trace per (direction, phase) so the legend / colour stays clean.
    grouped: dict[tuple[str, str], list[dict]] = {}
    for s in enriched:
        grouped.setdefault((s["direction"], s["phase"]), []).append(s)

    for (direction, phase), ss in grouped.items():
        colour = DIRECTION_COLOR_GATE[direction]
        symbol = DIRECTION_SYMBOL[direction]
        xs = [_iso(s["confirmed_at"]) for s in ss]
        ys = [s["hypothetical_entry"] for s in ss]
        labels = [
            f"{_pnl_label(s['_pnls'][50])} / {_pnl_label(s['_pnls'][100])}"
            for s in ss
        ]
        hovers = [
            (
                f"GATE PASSED<br>"
                f"confirmed_at={s['confirmed_at']}<br>"
                f"induced_at={s['induced_at']}<br>"
                f"direction={s['direction']}<br>"
                f"phase={s['phase']}<br>"
                f"induc_conf={s['inducement_confidence']:.2f}<br>"
                f"zone={s['zone_kind']} "
                f"[{s['zone_lower']:.2f}, {s['zone_upper']:.2f}]<br>"
                f"entry={s['hypothetical_entry']:.2f}<br>"
                f"pnl@50={_pnl_label(s['_pnls'][50])}<br>"
                f"pnl@100={_pnl_label(s['_pnls'][100])}"
            )
            for s in ss
        ]
        fig.add_trace(
            go.Scatter(
                x=xs, y=ys,
                mode="markers+text",
                marker=dict(
                    color=colour,
                    size=14,
                    symbol=symbol,
                    line=dict(width=1, color="black"),
                ),
                text=labels,
                textposition=(
                    "top center" if direction == "bullish" else "bottom center"
                ),
                textfont=dict(size=10, color=colour),
                name=f"gate ({direction}, {phase})",
                hovertext=hovers,
                hoverinfo="text",
            )
        )

    fig.update_layout(
        title=(
            "LIT pipeline — XAUUSD M5 — "
            f"{data['window']['start'][:10]} → {data['window']['end'][:10]} "
            f"(raw 24/7) — {len(setups)} gate setups"
        ),
        xaxis_rangeslider_visible=False,
        height=900,
        template="plotly_white",
        hovermode="closest",
    )
    fig.update_yaxes(title_text="price (USD)")
    fig.update_xaxes(title_text="time (UTC)")

    fig.write_html(str(OUT_HTML), include_plotlyjs="cdn")
    print(f"wrote: {OUT_HTML}")

    # ------------------------------------------------------------------
    # Aggregate P&L stats — printed to stdout (no extra file)
    # ------------------------------------------------------------------
    n_bullish = sum(1 for s in enriched if s["direction"] == "bullish")
    n_bearish = sum(1 for s in enriched if s["direction"] == "bearish")

    print()
    print("=== gate setups by direction ===")
    print(f"  bullish   {n_bullish}")
    print(f"  bearish   {n_bearish}")

    for h in HORIZONS:
        values = [s["_pnls"][h] for s in enriched if s["_pnls"][h] is not None]
        skipped = len(enriched) - len(values)
        print()
        print(f"=== pnl @ +{h} candles (pips) — n={len(values)}, skipped={skipped} ===")
        if not values:
            print("  (no setups with a valid horizon)")
            continue
        sorted_values = sorted(values)
        n = len(sorted_values)
        wins = sum(1 for v in values if v > 0)
        print(
            f"  min={sorted_values[0]:>+8.1f}  "
            f"med={sorted_values[n // 2]:>+8.1f}  "
            f"mean={statistics.mean(values):>+8.1f}  "
            f"max={sorted_values[-1]:>+8.1f}  "
            f"win_rate={(wins / n) * 100:>5.1f}% "
            f"({wins}/{n})"
        )


if __name__ == "__main__":
    main()
