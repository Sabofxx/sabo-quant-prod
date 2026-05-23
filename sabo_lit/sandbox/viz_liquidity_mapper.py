"""
Sandbox viz — render the eval output as an interactive HTML.

Reads ``sandbox/eval_liquidity_mapper.json`` (produced by
``eval_liquidity_mapper.py``) and writes
``sandbox/viz_liquidity_mapper.html``.

Visuals:
  * candlestick chart of XAUUSD M5 over the eval window
  * each liquidity zone overlaid as a horizontal rectangle spanning
    its lifetime [born_at -> died_at or window-end], coloured by kind
  * active zones are opaque; invalidated zones are dashed-edge + dimmer
  * separate panel for the simultaneous-active-zones series

Sandbox / throwaway. Pure read of the JSON payload, no recompute.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import plotly.graph_objects as go
from plotly.subplots import make_subplots


IN_JSON = Path(__file__).parent / "eval_liquidity_mapper.json"
OUT_HTML = Path(__file__).parent / "viz_liquidity_mapper.html"


KIND_COLOR = {
    "equal_highs":       "#d62728",  # red
    "equal_lows":        "#2ca02c",  # green
    "rolling_high":      "#ff7f0e",  # orange
    "rolling_low":       "#1f77b4",  # blue
    "previous_day_high": "#9467bd",  # purple
    "previous_day_low":  "#8c564b",  # brown
}


def _iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def main() -> None:
    data = json.loads(IN_JSON.read_text())

    candles = data["candles"]
    zones = data["zones"]
    active_series = data["active_count_series"]
    window_end = _iso(data["window"]["end"])

    times = [_iso(c["t"]) for c in candles]
    opens = [c["o"] for c in candles]
    highs = [c["h"] for c in candles]
    lows = [c["l"] for c in candles]
    closes = [c["c"] for c in candles]

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.78, 0.22],
        vertical_spacing=0.04,
        subplot_titles=("XAUUSD M5 + liquidity zones",
                        "active zones (simultaneous)"),
    )

    # candles
    fig.add_trace(
        go.Candlestick(
            x=times, open=opens, high=highs, low=lows, close=closes,
            name="XAUUSD M5",
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
        ),
        row=1, col=1,
    )

    # zones — one rect per zone, with a legend entry collated by kind
    legend_emitted: set[str] = set()
    for z in zones:
        born = _iso(z["born_at"])
        died = _iso(z["died_at"]) if z["died_at"] else window_end
        color = KIND_COLOR.get(z["kind"], "#888888")
        invalidated = z["died_at"] is not None
        line_dash = "dot" if invalidated else "solid"
        fill_opacity = 0.10 if invalidated else 0.25
        fig.add_shape(
            type="rect",
            xref="x", yref="y",
            x0=born, x1=died,
            y0=z["price_lower"], y1=z["price_upper"],
            line=dict(color=color, width=1, dash=line_dash),
            fillcolor=color, opacity=fill_opacity,
            layer="below",
            row=1, col=1,
        )
        # one legend marker per kind (invisible point, just for legend)
        if z["kind"] not in legend_emitted:
            legend_emitted.add(z["kind"])
            mid = (z["price_upper"] + z["price_lower"]) / 2
            fig.add_trace(
                go.Scatter(
                    x=[born], y=[mid],
                    mode="markers",
                    marker=dict(color=color, size=10, symbol="square"),
                    name=z["kind"],
                    showlegend=True,
                    legendgroup=z["kind"],
                ),
                row=1, col=1,
            )

    # active count series
    fig.add_trace(
        go.Scatter(
            x=[_iso(p["t"]) for p in active_series],
            y=[p["n"] for p in active_series],
            mode="lines",
            line=dict(color="#444", width=1),
            name="active zones",
            showlegend=False,
        ),
        row=2, col=1,
    )

    fig.update_layout(
        title=(
            "LiquidityMapper — XAUUSD M5 — "
            f"{data['window']['start'][:10]} → {data['window']['end'][:10]} "
            "(raw 24/7, no weekend filter)"
        ),
        xaxis_rangeslider_visible=False,
        height=900,
        template="plotly_white",
        hovermode="x unified",
    )
    fig.update_yaxes(title_text="price (USD)", row=1, col=1)
    fig.update_yaxes(title_text="n active", row=2, col=1)
    fig.update_xaxes(title_text="time (UTC)", row=2, col=1)

    fig.write_html(str(OUT_HTML), include_plotlyjs="cdn")
    print(f"wrote: {OUT_HTML}")


if __name__ == "__main__":
    main()
