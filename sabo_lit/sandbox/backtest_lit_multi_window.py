"""
Multi-window LIT backtest — bull / bear / range regimes.

Re-runs the same pipeline as ``backtest_lit_pipeline.py`` (same configs,
no tuning) across three deliberately-chosen XAUUSD M5 windows:

    bull   2024-11-19 -> 2025-05-19   (~+30% on gold)
    bear   2022-03-01 -> 2022-09-01   (~-15% on gold)
    range  2023-06-01 -> 2023-12-01   (gold ~$1900-2000, lateral)

The point is to test whether the long-side edge observed on the bull
window survives outside a trending-up regime. Sandbox / throwaway.

Outputs:
  * ``sandbox/backtest_multi_window.json`` — full raw stats + per-gate
    detail for every window.
  * ``sandbox/backtest_multi_window_equity.html`` — three equity curves
    (h=100, sizing 1u) overlaid on a relative-trade-index axis so the
    regimes can be compared without time stretch.
  * Stats printed to stdout (raw tables only — no interpretation).
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PARENT = Path(__file__).resolve().parents[2]
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

import plotly.graph_objects as go  # noqa: E402

from sabo_lit.core import (  # noqa: E402
    InducementEvent,
    LiquidityZone,
    MarketPhase,
    PhaseKind,
    SweepEvent,
    ValidatedStructureState,
)
from sabo_lit.lit.inducement_detector import InducementPatternDetector  # noqa: E402
from sabo_lit.lit.liquidity_mapper import LiquidityMapper  # noqa: E402
from sabo_lit.lit.phase_classifier import PhaseClassifier  # noqa: E402
from sabo_lit.lit.structure_validator import (  # noqa: E402
    RuleBasedStructureValidator,
)
from sabo_lit.lit.sweep_detector import RuleBasedSweepDetector  # noqa: E402
from sabo_lit.sandbox.backtest_lit_pipeline import (  # noqa: E402
    HORIZONS,
    TRAILING_CONTEXT,
    GateRecord,
    _drawdown,
    _longest_losing_streak,
    _pnl_pips,
    _profit_factor,
    _quantiles,
    _win_rate,
    build_detector_config,
    build_mapper_config,
    build_phase_config,
    build_sweep_config,
    build_validator_config,
)
from sabo_lit.sandbox.load_xauusd import load_candles  # noqa: E402


PROGRESS_EVERY = 10_000
MIN_GATES_WARN = 30
H_FOCUS = 100  # horizon used for sections B, C, D + recap

WINDOWS: tuple[tuple[str, datetime, datetime], ...] = (
    (
        "bull",
        datetime(2024, 11, 19, 0, 0, tzinfo=timezone.utc),
        datetime(2025, 5, 19, 0, 0, tzinfo=timezone.utc),
    ),
    (
        "bear",
        datetime(2022, 3, 1, 0, 0, tzinfo=timezone.utc),
        datetime(2022, 9, 1, 0, 0, tzinfo=timezone.utc),
    ),
    (
        "range",
        datetime(2023, 6, 1, 0, 0, tzinfo=timezone.utc),
        datetime(2023, 12, 1, 0, 0, tzinfo=timezone.utc),
    ),
)

OUT_JSON = Path(__file__).parent / "backtest_multi_window.json"
OUT_EQUITY_HTML = Path(__file__).parent / "backtest_multi_window_equity.html"


def _verify_coverage(
    name: str, start: datetime, end: datetime, all_candles: list,
) -> list[int]:
    """Return eval_indices for this window. Aborts (returns []) if the
    window has no candles or starts/ends with > 1 day of missing data."""
    eval_indices = [
        i for i, c in enumerate(all_candles)
        if start <= c.open_time <= end
    ]
    if not eval_indices:
        print(f"!! window {name!r}: no candles in [{start}, {end}] — skipping.")
        return []
    first_dt = all_candles[eval_indices[0]].open_time
    last_dt = all_candles[eval_indices[-1]].open_time
    if (first_dt - start).total_seconds() > 86400:
        print(
            f"!! window {name!r}: first candle {first_dt} is > 1d "
            f"after start {start} — partial coverage."
        )
    if (end - last_dt).total_seconds() > 86400:
        print(
            f"!! window {name!r}: last candle {last_dt} is > 1d "
            f"before end {end} — partial coverage."
        )
    return eval_indices


def run_one_window(
    name: str,
    start: datetime,
    end: datetime,
    all_candles: list,
) -> dict[str, Any] | None:
    eval_indices = _verify_coverage(name, start, end, all_candles)
    if not eval_indices:
        return None

    print()
    print("=" * 78)
    print(f"WINDOW: {name}   {start.date()} -> {end.date()}")
    print("=" * 78)
    first_c = all_candles[eval_indices[0]]
    last_c = all_candles[eval_indices[-1]]
    price_start = float(first_c.open)
    price_end = float(last_c.close)
    price_var_pct = (price_end - price_start) / price_start * 100
    print(
        f"  first candle: {first_c.open_time.isoformat()}  open={price_start:.2f}"
    )
    print(
        f"  last candle:  {last_c.open_time.isoformat()}  close={price_end:.2f}"
    )
    print(f"  price variation: {price_var_pct:+.2f}%")
    print(f"  candles in window: {len(eval_indices):,}")

    mapper = LiquidityMapper(build_mapper_config())
    detector = InducementPatternDetector(build_detector_config())
    sweeper = RuleBasedSweepDetector(build_sweep_config())
    phaser = PhaseClassifier(build_phase_config())
    validator = RuleBasedStructureValidator(build_validator_config())

    gates: list[GateRecord] = []
    gates_seen: set[str] = set()
    inducement_history: dict[str, InducementEvent] = {}
    sweep_history: dict[str, SweepEvent] = {}
    phase_counter: Counter[str] = Counter()
    total_steps = len(eval_indices)
    loop_t0 = time.perf_counter()

    for step, i in enumerate(eval_indices):
        if step and step % PROGRESS_EVERY == 0:
            elapsed = time.perf_counter() - loop_t0
            rate = step / elapsed if elapsed > 0 else 0
            eta = (total_steps - step) / rate if rate > 0 else 0
            print(
                f"  [{name}] step {step:>6,}/{total_steps:,}  "
                f"elapsed={elapsed:>6.1f}s  rate={rate:>6.0f}/s  "
                f"eta={eta:>5.0f}s  gates={len(gates)}"
            )

        candle = all_candles[i]
        lo = max(0, i - TRAILING_CONTEXT + 1)
        window = all_candles[lo : i + 1]

        zones: list[LiquidityZone] = mapper.map_zones(window)

        induc = detector.detect(window, zones, None)
        if induc is not None:
            zid = induc.inducement_zone.zone_id
            if zid not in inducement_history:
                inducement_history[zid] = induc
        latest_induc: InducementEvent | None = None
        if inducement_history:
            latest_induc = max(
                inducement_history.values(), key=lambda e: e.timestamp,
            )

        sweep_now = sweeper.detect(window, zones)
        if sweep_now is not None:
            sid = sweep_now.sweep_id
            if sid not in sweep_history:
                sweep_history[sid] = sweep_now
        latest_sweep: SweepEvent | None = None
        if sweep_history:
            latest_sweep = max(
                sweep_history.values(), key=lambda s: s.timestamp,
            )

        phase: MarketPhase = phaser.classify(window, latest_induc, latest_sweep)
        phase_counter[phase.phase.value] += 1

        if latest_induc is not None and phase.phase != PhaseKind.UNDEFINED:
            try:
                gate_out: ValidatedStructureState | None = validator.validate(
                    latest_induc, phase, window,
                )
            except ValueError:
                gate_out = None
            if (
                gate_out is not None
                and gate_out.inducement_event_id not in gates_seen
            ):
                gates_seen.add(gate_out.inducement_event_id)
                rec = GateRecord(
                    inducement_event_id=gate_out.inducement_event_id,
                    confirmed_at=candle.open_time.isoformat(),
                    confirmed_idx=i,
                    direction=gate_out.inferred_direction,
                    phase=phase.phase.value,
                    inducement_confidence=latest_induc.confidence,
                    zone_kind=latest_induc.inducement_zone.kind.value,
                    zone_upper=float(latest_induc.inducement_zone.price_upper),
                    zone_lower=float(latest_induc.inducement_zone.price_lower),
                    entry=float(candle.close),
                )
                gates.append(rec)

    loop_elapsed = time.perf_counter() - loop_t0
    print(f"  [{name}] loop done in {loop_elapsed:.1f}s, {len(gates)} gates")

    # P&Ls
    for g in gates:
        for h in HORIZONS:
            g.pnls[h] = _pnl_pips(
                all_candles, g.confirmed_idx, h, g.direction, g.entry,
            )

    return {
        "name": name,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "price_start": price_start,
        "price_end": price_end,
        "price_var_pct": price_var_pct,
        "candles_in_window": total_steps,
        "unique_inducements": len(inducement_history),
        "unique_sweeps": len(sweep_history),
        "phase_counter": dict(phase_counter),
        "loop_elapsed_s": loop_elapsed,
        "gates": gates,
    }


def _print_window_stats(res: dict[str, Any]) -> None:
    name = res["name"]
    gates: list[GateRecord] = res["gates"]
    n = len(gates)

    print()
    print(f"--- {name}: SECTION A — counts ---")
    print(f"  unique gates: {n}")
    if n < MIN_GATES_WARN:
        print(f"  WARN: {n} gates < {MIN_GATES_WARN} — sample too small to conclude.")
    by_dir = Counter(g.direction for g in gates)
    by_kind = Counter(g.zone_kind for g in gates)
    print(f"  by direction:  bullish={by_dir.get('bullish', 0)}  bearish={by_dir.get('bearish', 0)}")
    print("  by parent zone kind:")
    for k in sorted(by_kind):
        print(f"    {k:<22s} {by_kind[k]:>4d}")

    # ----- SECTION B (h=100) -----
    print()
    print(f"--- {name}: SECTION B — P&L horizon {H_FOCUS} (pips, $0.01) ---")
    pnls = [g.pnls[H_FOCUS] for g in gates if g.pnls[H_FOCUS] is not None]
    if pnls:
        q = _quantiles(pnls)
        wr = _win_rate(pnls) * 100
        pf = _profit_factor(pnls)
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(
            f"  overall n={len(pnls)} med={q['median']:+.0f} mean={q['mean']:+.0f} "
            f"win%={wr:.1f} PF={pf_s}"
        )
    else:
        print("  overall (no data)")

    print("  by direction:")
    print(f"    {'dir':<10s} {'n':>4s} {'med':>8s} {'mean':>8s} {'win%':>6s} {'PF':>6s}")
    for d in ("bullish", "bearish"):
        vs = [g.pnls[H_FOCUS] for g in gates
              if g.direction == d and g.pnls[H_FOCUS] is not None]
        if not vs:
            print(f"    {d:<10s} {0:>4d}  (no data)")
            continue
        s = sorted(vs)
        med = s[len(s) // 2]
        mean = statistics.mean(vs)
        wr = _win_rate(vs) * 100
        pf = _profit_factor(vs)
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(
            f"    {d:<10s} {len(vs):>4d} {med:+8.0f} {mean:+8.0f} "
            f"{wr:>5.1f}% {pf_s:>6s}"
        )

    print("  by parent zone kind (top 4 by count):")
    print(f"    {'kind':<22s} {'n':>4s} {'med':>8s} {'mean':>8s} {'win%':>6s}")
    by_kind_pnls: dict[str, list[float]] = defaultdict(list)
    for g in gates:
        v = g.pnls[H_FOCUS]
        if v is not None:
            by_kind_pnls[g.zone_kind].append(v)
    top4 = sorted(by_kind_pnls.items(), key=lambda kv: -len(kv[1]))[:4]
    for kind, vs in top4:
        s = sorted(vs)
        med = s[len(s) // 2]
        mean = statistics.mean(vs)
        wr = _win_rate(vs) * 100
        print(
            f"    {kind:<22s} {len(vs):>4d} {med:+8.0f} {mean:+8.0f} {wr:>5.1f}%"
        )

    # ----- SECTION C — monthly -----
    print()
    print(f"--- {name}: SECTION C — monthly breakdown (h={H_FOCUS}) ---")
    print(f"    {'month':<10s} {'n':>4s} {'med':>8s} {'win%':>6s} {'PF':>6s}")
    by_month: dict[str, list[float]] = defaultdict(list)
    for g in gates:
        v = g.pnls[H_FOCUS]
        if v is None:
            continue
        m = datetime.fromisoformat(g.confirmed_at).strftime("%Y-%m")
        by_month[m].append(v)
    for m in sorted(by_month):
        vs = by_month[m]
        s = sorted(vs)
        med = s[len(s) // 2]
        wr = _win_rate(vs) * 100
        pf = _profit_factor(vs)
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(
            f"    {m:<10s} {len(vs):>4d} {med:+8.0f} {wr:>5.1f}% {pf_s:>6s}"
        )

    # ----- SECTION D — drawdown -----
    print()
    print(f"--- {name}: SECTION D — drawdown (h={H_FOCUS}, sizing 1u) ---")
    pnls_sorted_by_idx = [
        g.pnls[H_FOCUS]
        for g in sorted(gates, key=lambda x: x.confirmed_idx)
        if g.pnls[H_FOCUS] is not None
    ]
    if not pnls_sorted_by_idx:
        print("  (no data)")
        return
    equity = []
    running = 0.0
    for p in pnls_sorted_by_idx:
        running += p
        equity.append(running)
    dd = _drawdown(equity)
    streak = _longest_losing_streak(pnls_sorted_by_idx)
    print(f"  trades:                 {len(pnls_sorted_by_idx)}")
    print(f"  final equity (pips):    {equity[-1]:+.0f}")
    print(f"  max drawdown (pips):    {dd['max_dd_pips']:.0f}")
    print(f"  max drawdown (% peak):  {dd['max_dd_pct'] * 100:.1f}%")
    print(f"  longest losing streak:  {streak}")


def _print_recap(results: dict[str, dict[str, Any]]) -> None:
    print()
    print("=" * 78)
    print("RECAP — 3 windows, h=100, sizing 1u")
    print("=" * 78)
    print(
        f"  {'window':<7s} {'regime':>8s} {'n':>5s} {'PF':>6s} {'win%':>6s} "
        f"{'final':>8s} {'DD%':>6s} {'bull_PF':>8s} {'bear_PF':>8s}"
    )
    for name, res in results.items():
        if res is None:
            print(f"  {name:<7s} -- skipped --")
            continue
        gates = res["gates"]
        pnls = [g.pnls[H_FOCUS] for g in gates if g.pnls[H_FOCUS] is not None]
        if not pnls:
            print(f"  {name:<7s} (no data)")
            continue
        pf = _profit_factor(pnls)
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        wr = _win_rate(pnls) * 100
        bull_pnls = [
            g.pnls[H_FOCUS] for g in gates
            if g.direction == "bullish" and g.pnls[H_FOCUS] is not None
        ]
        bear_pnls = [
            g.pnls[H_FOCUS] for g in gates
            if g.direction == "bearish" and g.pnls[H_FOCUS] is not None
        ]

        def _pf_str(vs: list[float]) -> str:
            if not vs:
                return "—"
            x = _profit_factor(vs)
            return "inf" if x == float("inf") else f"{x:.2f}"

        # equity / DD
        pnls_sorted_by_idx = [
            g.pnls[H_FOCUS]
            for g in sorted(gates, key=lambda x: x.confirmed_idx)
            if g.pnls[H_FOCUS] is not None
        ]
        equity = []
        running = 0.0
        for p in pnls_sorted_by_idx:
            running += p
            equity.append(running)
        dd = _drawdown(equity)
        dd_pct = dd["max_dd_pct"] * 100
        final_pips = equity[-1]
        regime_s = f"{res['price_var_pct']:+.1f}%"
        print(
            f"  {name:<7s} {regime_s:>8s} {len(pnls):>5d} {pf_s:>6s} "
            f"{wr:>5.1f}% {final_pips:+8.0f} {dd_pct:>5.1f}% "
            f"{_pf_str(bull_pnls):>8s} {_pf_str(bear_pnls):>8s}"
        )


def _write_equity_html(results: dict[str, dict[str, Any]]) -> None:
    fig = go.Figure()
    colors = {"bull": "#2ca02c", "bear": "#d62728", "range": "#1f77b4"}
    for name, res in results.items():
        if res is None:
            continue
        gates = res["gates"]
        pnls = [
            g.pnls[H_FOCUS]
            for g in sorted(gates, key=lambda x: x.confirmed_idx)
            if g.pnls[H_FOCUS] is not None
        ]
        if not pnls:
            continue
        equity = []
        running = 0.0
        for p in pnls:
            running += p
            equity.append(running)
        xs = list(range(len(equity)))
        regime_s = f"{res['price_var_pct']:+.1f}%"
        fig.add_trace(
            go.Scatter(
                x=xs, y=equity,
                mode="lines",
                name=f"{name} ({regime_s})",
                line=dict(color=colors.get(name, "#888"), width=2),
            )
        )
    fig.update_layout(
        title=(
            "LIT pipeline — XAUUSD M5 — equity (h=100, 1u sizing) "
            "— 3 regimes overlaid on relative trade index"
        ),
        xaxis_title="trade # (window-relative)",
        yaxis_title="cumulative pips",
        template="plotly_white",
        height=700,
        hovermode="x unified",
    )
    fig.write_html(str(OUT_EQUITY_HTML), include_plotlyjs="cdn")
    print(f"wrote: {OUT_EQUITY_HTML}")


def main() -> None:
    t0 = time.perf_counter()
    print("loading candles ...")
    all_candles = load_candles()
    print(f"loaded {len(all_candles):,}")

    results: dict[str, dict[str, Any] | None] = {}
    for name, start, end in WINDOWS:
        res = run_one_window(name, start, end, all_candles)
        results[name] = res
        if res is not None:
            _print_window_stats(res)

    _print_recap(results)
    _write_equity_html(results)

    # JSON dump (serialise dataclasses)
    payload: dict[str, Any] = {}
    for name, res in results.items():
        if res is None:
            payload[name] = None
            continue
        payload[name] = {
            **{k: v for k, v in res.items() if k != "gates"},
            "gates": [asdict(g) for g in res["gates"]],
        }
    OUT_JSON.write_text(json.dumps(payload, default=str))
    print(f"wrote: {OUT_JSON}")

    print(f"total elapsed: {time.perf_counter() - t0:.1f}s")


if __name__ == "__main__":
    main()
