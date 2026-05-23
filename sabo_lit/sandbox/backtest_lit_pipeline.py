"""
Sandbox backtest — 6 months of XAUUSD M5 through the full LIT pipeline.

Window: 2024-11-19 00:00 UTC -> 2025-05-19 00:00 UTC.

Pipeline (Phase 1.4 complete):
  LiquidityMapper -> InducementPatternDetector -> RuleBasedSweepDetector
    -> PhaseClassifier -> RuleBasedStructureValidator

Same configs as ``eval_lit_pipeline.py`` (no tuning to this window).

Trade modelling
---------------
* Gate setups deduped by ``inducement_event_id`` — the same setup is
  re-emitted every tick the gate keeps passing; only the FIRST emission
  is kept.
* Entry = close of the gate-confirmation candle.
* P&L computed at +50, +100, +200 candles ahead (forward look on the
  raw closes). Pip = $0.01 for XAUUSD. Setups whose horizon exceeds
  the window are flagged ``None`` for that horizon.

Outputs
-------
* ``sandbox/backtest_lit_pipeline.json`` — all stats + per-gate detail.
* ``sandbox/backtest_equity_curve.html`` — cumulative equity curve at
  horizon 100, with max-drawdown markers.
* Stats printed to stdout (raw tables).

Sandbox / throwaway. Imports allowed from ``core/`` and ``lit/``;
nothing in production imports back.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

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
from sabo_lit.lit.inducement_detector import (  # noqa: E402
    InducementDetectorConfig,
    InducementPatternDetector,
)
from sabo_lit.lit.liquidity_mapper import (  # noqa: E402
    LiquidityMapper,
    LiquidityMapperConfig,
)
from sabo_lit.lit.phase_classifier import (  # noqa: E402
    PhaseClassifier,
    PhaseClassifierConfig,
)
from sabo_lit.lit.structure_validator import (  # noqa: E402
    RuleBasedStructureValidator,
    StructureValidatorConfig,
)
from sabo_lit.lit.sweep_detector import (  # noqa: E402
    RuleBasedSweepDetector,
    SweepDetectorConfig,
)
from sabo_lit.sandbox.load_xauusd import load_candles  # noqa: E402


WINDOW_START = datetime(2024, 11, 19, 0, 0, tzinfo=timezone.utc)
WINDOW_END = datetime(2025, 5, 19, 0, 0, tzinfo=timezone.utc)
TRAILING_CONTEXT = 700
PIP_SIZE = 0.01
HORIZONS = (50, 100, 200)
PROGRESS_EVERY = 10_000

OUT_JSON = Path(__file__).parent / "backtest_lit_pipeline.json"
OUT_EQUITY_HTML = Path(__file__).parent / "backtest_equity_curve.html"


# -- configs -----------------------------------------------------------
def build_mapper_config() -> LiquidityMapperConfig:
    return LiquidityMapperConfig(
        pip_size=Decimal("0.01"),
        equal_level_tolerance_pips=Decimal("10"),
        min_equal_touches=2,
        max_touches_for_full_strength=5,
        swing_lookback=5,
        rolling_lookback_candles=96,
        previous_day_lookback_candles=576,
        rolling_zone_strength=0.6,
        previous_day_zone_strength=0.8,
        min_zone_strength=0.0,
    )


def build_detector_config() -> InducementDetectorConfig:
    return InducementDetectorConfig(
        pip_size=Decimal("0.01"),
        sweep_lookback_candles=12,
        min_penetration_pips=Decimal("5"),
        min_return_pips=Decimal("5"),
        base_confidence=0.5,
        flow_confirmation_bonus=0.0,
    )


def build_sweep_config() -> SweepDetectorConfig:
    return SweepDetectorConfig(
        pip_size=Decimal("0.01"),
        sweep_lookback_candles=12,
        min_penetration_pips=Decimal("5"),
    )


def build_phase_config() -> PhaseClassifierConfig:
    return PhaseClassifierConfig(inducement_freshness_candles=24)


def build_validator_config() -> StructureValidatorConfig:
    return StructureValidatorConfig(
        pip_size=Decimal("0.01"),
        swing_lookback=5,
        pivot_search_window=48,
        min_post_inducement_candles=2,
        min_break_distance_pips=Decimal("3"),
    )


# -- gate record -------------------------------------------------------
@dataclass
class GateRecord:
    inducement_event_id: str
    confirmed_at: str           # iso
    confirmed_idx: int          # index in full candles array
    direction: str              # "bullish" | "bearish"
    phase: str
    inducement_confidence: float
    zone_kind: str
    zone_upper: float
    zone_lower: float
    entry: float
    pnls: dict[int, float | None] = field(default_factory=dict)


# -- helpers -----------------------------------------------------------
def _pnl_pips(
    candles: list,
    entry_idx: int,
    horizon: int,
    direction: str,
    entry: float,
) -> float | None:
    target_idx = entry_idx + horizon
    if target_idx >= len(candles):
        return None
    future_close = float(candles[target_idx].close)
    if direction == "bullish":
        return (future_close - entry) / PIP_SIZE
    return (entry - future_close) / PIP_SIZE


def _quantiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    v = sorted(values)
    n = len(v)
    return {
        "min": v[0],
        "p25": v[int(0.25 * n)],
        "median": v[n // 2],
        "mean": statistics.mean(v),
        "p75": v[min(n - 1, int(0.75 * n))],
        "max": v[-1],
    }


def _win_rate(values: list[float]) -> float:
    if not values:
        return 0.0
    wins = sum(1 for v in values if v > 0)
    return wins / len(values)


def _profit_factor(values: list[float]) -> float:
    gains = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v < 0)
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def _drawdown(equity: list[float]) -> dict:
    if not equity:
        return {"max_dd_pips": 0.0, "max_dd_pct": 0.0, "longest_losing_streak": 0}
    peak = equity[0]
    max_dd = 0.0
    max_dd_pct = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > max_dd:
            max_dd = dd
        if peak != 0:
            pct = dd / abs(peak) if peak != 0 else 0.0
            if pct > max_dd_pct:
                max_dd_pct = pct
    # longest losing streak — counted from per-trade signs, not equity diffs.
    # diffs = consecutive pnl values. We'll recompute streak below from
    # gate-ordered pnls list outside.
    return {
        "max_dd_pips": max_dd,
        "max_dd_pct": max_dd_pct,
    }


def _longest_losing_streak(pnls: list[float]) -> int:
    best = 0
    cur = 0
    for p in pnls:
        if p < 0:
            cur += 1
            if cur > best:
                best = cur
        else:
            cur = 0
    return best


# ---------------------------------------------------------------------
def main() -> None:
    t0 = time.perf_counter()
    print("loading candles ...")
    all_candles = load_candles()
    print(f"loaded {len(all_candles):,}")

    eval_indices = [
        i for i, c in enumerate(all_candles)
        if WINDOW_START <= c.open_time <= WINDOW_END
    ]
    if not eval_indices:
        print("no candles in eval window — abort.")
        return

    first = eval_indices[0]
    last = eval_indices[-1]
    print(
        f"backtest window: idx {first}..{last} "
        f"({all_candles[first].open_time.isoformat()} -> "
        f"{all_candles[last].open_time.isoformat()}) "
        f"= {last - first + 1} candles"
    )

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
                f"  step {step:>6,}/{total_steps:,}  "
                f"elapsed={elapsed:>6.1f}s  "
                f"rate={rate:>6.0f}/s  eta={eta:>5.0f}s  "
                f"gates_so_far={len(gates)}"
            )

        candle = all_candles[i]
        lo = max(0, i - TRAILING_CONTEXT + 1)
        window = all_candles[lo : i + 1]

        zones: list[LiquidityZone] = mapper.map_zones(window)

        # inducement
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

        # sweep
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

        # phase
        phase: MarketPhase = phaser.classify(window, latest_induc, latest_sweep)
        phase_counter[phase.phase.value] += 1

        # validator (gate)
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
    print(f"loop done in {loop_elapsed:.1f}s")

    # ------------------------------------------------------------------
    # P&L per gate
    # ------------------------------------------------------------------
    for g in gates:
        for h in HORIZONS:
            g.pnls[h] = _pnl_pips(
                all_candles, g.confirmed_idx, h, g.direction, g.entry,
            )

    # ------------------------------------------------------------------
    # Section 1 — counts
    # ------------------------------------------------------------------
    n_gates = len(gates)
    by_direction = Counter(g.direction for g in gates)
    by_zone_kind = Counter(g.zone_kind for g in gates)

    print()
    print("=== 1. counts ===")
    print(f"  candles backtested:    {total_steps:,}")
    print(f"  unique inducements:    {len(inducement_history):,}")
    print(f"  unique sweeps:         {len(sweep_history):,}")
    print(f"  unique gate setups:    {n_gates:,}")
    print(f"  gates / 1k candles:    {n_gates / total_steps * 1000:.2f}")
    print()
    print("  by direction")
    for d in ("bullish", "bearish"):
        print(f"    {d:<10s} {by_direction.get(d, 0):>5d}")
    print()
    print("  by parent zone kind")
    for k in sorted(by_zone_kind):
        print(f"    {k:<22s} {by_zone_kind[k]:>5d}")

    # ------------------------------------------------------------------
    # Section 2 — distribution temporelle
    # ------------------------------------------------------------------
    gates_sorted = sorted(gates, key=lambda g: g.confirmed_idx)
    by_month: Counter[str] = Counter()
    by_weekday: Counter[str] = Counter()
    for g in gates_sorted:
        dt = datetime.fromisoformat(g.confirmed_at)
        by_month[dt.strftime("%Y-%m")] += 1
        by_weekday[dt.strftime("%a")] += 1

    inter_gates = [
        gates_sorted[i].confirmed_idx - gates_sorted[i - 1].confirmed_idx
        for i in range(1, len(gates_sorted))
    ]

    print()
    print("=== 2. distribution temporelle ===")
    print("  by month")
    for m in sorted(by_month):
        print(f"    {m}    {by_month[m]:>4d}")
    print()
    if inter_gates:
        s = sorted(inter_gates)
        n = len(s)
        print(
            f"  inter-gate distance (candles): "
            f"n={n} min={s[0]} med={s[n // 2]} "
            f"p90={s[min(n - 1, int(0.9 * n))]} max={s[-1]} "
            f"mean={statistics.mean(s):.1f}"
        )
    print()
    print("  by weekday (UTC)")
    for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
        print(f"    {d}   {by_weekday.get(d, 0):>4d}")

    # ------------------------------------------------------------------
    # Section 3 — P&L par horizon
    # ------------------------------------------------------------------
    print()
    print("=== 3. P&L par horizon (pips, $0.01) ===")
    print(
        f"  {'H':>4s}  {'n':>4s}  {'min':>8s} {'p25':>8s} {'med':>8s} "
        f"{'mean':>8s} {'p75':>8s} {'max':>8s}  "
        f"{'win%':>6s}  {'PF':>6s}"
    )
    for h in HORIZONS:
        values = [g.pnls[h] for g in gates if g.pnls[h] is not None]
        if not values:
            print(f"  {h:>4d}  {'-':>4s}  (no valid horizon)")
            continue
        q = _quantiles(values)
        wr = _win_rate(values) * 100
        pf = _profit_factor(values)
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(
            f"  {h:>4d}  {len(values):>4d}  "
            f"{q['min']:>+8.0f} {q['p25']:>+8.0f} {q['median']:>+8.0f} "
            f"{q['mean']:>+8.0f} {q['p75']:>+8.0f} {q['max']:>+8.0f}  "
            f"{wr:>5.1f}%  {pf_s:>6s}"
        )

    # ------------------------------------------------------------------
    # Section 4 — P&L par parent zone kind, horizon 100
    # ------------------------------------------------------------------
    print()
    print("=== 4. P&L par parent zone kind (horizon 100) ===")
    print(f"  {'kind':<22s} {'n':>4s} {'med':>8s} {'mean':>8s} {'win%':>6s}")
    by_kind_pnls: dict[str, list[float]] = defaultdict(list)
    for g in gates:
        v = g.pnls[100]
        if v is not None:
            by_kind_pnls[g.zone_kind].append(v)
    for kind in sorted(by_kind_pnls):
        vs = by_kind_pnls[kind]
        vs_s = sorted(vs)
        n = len(vs_s)
        med = vs_s[n // 2]
        mean = statistics.mean(vs)
        wr = _win_rate(vs) * 100
        print(
            f"  {kind:<22s} {n:>4d} {med:>+8.0f} {mean:>+8.0f} {wr:>5.1f}%"
        )

    # ------------------------------------------------------------------
    # Section 5 — P&L par direction, horizon 100
    # ------------------------------------------------------------------
    print()
    print("=== 5. P&L par direction (horizon 100) ===")
    print(
        f"  {'dir':<10s} {'n':>4s} {'med':>8s} {'mean':>8s} "
        f"{'win%':>6s} {'PF':>6s}"
    )
    by_dir_pnls: dict[str, list[float]] = defaultdict(list)
    for g in gates:
        v = g.pnls[100]
        if v is not None:
            by_dir_pnls[g.direction].append(v)
    for d in ("bullish", "bearish"):
        vs = by_dir_pnls.get(d, [])
        if not vs:
            print(f"  {d:<10s} {0:>4d}  (no data)")
            continue
        vs_s = sorted(vs)
        n = len(vs_s)
        med = vs_s[n // 2]
        mean = statistics.mean(vs)
        wr = _win_rate(vs) * 100
        pf = _profit_factor(vs)
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(
            f"  {d:<10s} {n:>4d} {med:>+8.0f} {mean:>+8.0f} "
            f"{wr:>5.1f}% {pf_s:>6s}"
        )

    # ------------------------------------------------------------------
    # Section 6 — Drawdown sur equity curve horizon 100
    # ------------------------------------------------------------------
    print()
    print("=== 6. equity curve horizon 100 (sizing 1 unit) ===")
    pnls_100_sorted = [
        g.pnls[100] for g in gates_sorted if g.pnls[100] is not None
    ]
    if not pnls_100_sorted:
        print("  (no valid horizon-100 P&Ls)")
        equity_series: list[float] = []
    else:
        equity_series = []
        running = 0.0
        for p in pnls_100_sorted:
            running += p
            equity_series.append(running)
        dd = _drawdown(equity_series)
        streak = _longest_losing_streak(pnls_100_sorted)
        print(f"  trades:                 {len(pnls_100_sorted)}")
        print(f"  final equity (pips):    {equity_series[-1]:+.0f}")
        print(f"  max drawdown (pips):    {dd['max_dd_pips']:.0f}")
        print(f"  max drawdown (% peak):  {dd['max_dd_pct'] * 100:.1f}%")
        print(f"  longest losing streak:  {streak}")

    # ------------------------------------------------------------------
    # Equity-curve viz
    # ------------------------------------------------------------------
    if equity_series:
        gate_ts = [
            datetime.fromisoformat(g.confirmed_at)
            for g in gates_sorted
            if g.pnls[100] is not None
        ]
        peaks = []
        peak = equity_series[0]
        for v in equity_series:
            if v > peak:
                peak = v
            peaks.append(peak)
        dd_series = [p - v for p, v in zip(peaks, equity_series)]
        max_dd_idx = max(range(len(dd_series)), key=lambda k: dd_series[k])

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=gate_ts, y=equity_series, mode="lines+markers",
                name="cumulative pips (h=100)",
                line=dict(color="#1f77b4", width=2),
                marker=dict(size=6),
            )
        )
        fig.add_trace(
            go.Scatter(
                x=gate_ts, y=peaks, mode="lines",
                name="running peak",
                line=dict(color="#999", width=1, dash="dot"),
            )
        )
        fig.add_trace(
            go.Scatter(
                x=[gate_ts[max_dd_idx]],
                y=[equity_series[max_dd_idx]],
                mode="markers",
                marker=dict(color="#d62728", size=14, symbol="x"),
                name=f"max DD ({dd_series[max_dd_idx]:.0f}p)",
            )
        )
        fig.update_layout(
            title=(
                "LIT pipeline — XAUUSD M5 backtest — equity curve (h=100) — "
                f"{WINDOW_START.date()} → {WINDOW_END.date()} "
                f"— final={equity_series[-1]:+.0f}p, "
                f"maxDD={dd_series[max_dd_idx]:.0f}p"
            ),
            xaxis_title="time (UTC)",
            yaxis_title="cumulative pips (1u sizing)",
            template="plotly_white",
            height=700,
            hovermode="x unified",
        )
        fig.write_html(str(OUT_EQUITY_HTML), include_plotlyjs="cdn")
        print(f"wrote: {OUT_EQUITY_HTML}")

    # ------------------------------------------------------------------
    # JSON dump
    # ------------------------------------------------------------------
    payload = {
        "window": {
            "start": WINDOW_START.isoformat(),
            "end": WINDOW_END.isoformat(),
        },
        "counts": {
            "candles_backtested": total_steps,
            "unique_inducements": len(inducement_history),
            "unique_sweeps": len(sweep_history),
            "unique_gates": n_gates,
            "by_direction": dict(by_direction),
            "by_zone_kind": dict(by_zone_kind),
        },
        "phase_counter": dict(phase_counter),
        "by_month": dict(by_month),
        "by_weekday": dict(by_weekday),
        "horizons": {
            h: {
                "n": len([g.pnls[h] for g in gates if g.pnls[h] is not None]),
                **_quantiles(
                    [g.pnls[h] for g in gates if g.pnls[h] is not None]
                ),
                "win_rate": _win_rate(
                    [g.pnls[h] for g in gates if g.pnls[h] is not None]
                ),
                "profit_factor": _profit_factor(
                    [g.pnls[h] for g in gates if g.pnls[h] is not None]
                ),
            }
            for h in HORIZONS
        },
        "drawdown_h100": (
            {**_drawdown(equity_series),
             "longest_losing_streak": _longest_losing_streak(pnls_100_sorted),
             "final_equity": equity_series[-1] if equity_series else 0.0}
            if equity_series else None
        ),
        "gates": [asdict(g) for g in gates_sorted],
    }
    OUT_JSON.write_text(json.dumps(payload, default=str))
    print(f"wrote: {OUT_JSON}")

    total_elapsed = time.perf_counter() - t0
    print(f"total elapsed: {total_elapsed:.1f}s")


if __name__ == "__main__":
    main()
