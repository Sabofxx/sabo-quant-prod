"""
Sandbox eval — full LIT pipeline on XAUUSD M5, 2025-05-12 → 2025-05-16 UTC.

Pipeline order (Phase 1.4, complete):

  LiquidityMapper
    -> InducementPatternDetector  (latest inducement, deduped by zone_id)
    -> RuleBasedSweepDetector     (latest sweep, deduped by sweep_id)
    -> PhaseClassifier            (consumes both)
    -> RuleBasedStructureValidator (the gate)

A "setup" is any tick where ``validate`` returns a non-``None``
``ValidatedStructureState``. Setups are deduped across consecutive
ticks by ``inducement_event_id`` and stored once with the timestamp of
the FIRST tick that passed the gate.

KNOWN LIMIT (sweep_event_id always None on validated states):
``RuleBasedStructureValidator.validate`` takes ``(inducement, phase,
candles)`` — no sweep input. ``ValidatedStructureState.sweep_event_id``
therefore stays ``None`` even when a fresh sweep made the phase flip
to PHASE_2_MITIGATION. Wiring sweep into the validator output is a
separate contract phase change (would extend ``StructureValidator``
ABC signature).

Sandbox / throwaway. Imports allowed from ``core/`` and ``lit/``;
nothing in production imports back.
"""
from __future__ import annotations

import json
import statistics
import sys
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

_PARENT = Path(__file__).resolve().parents[2]
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

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


WINDOW_START = datetime(2025, 5, 12, 0, 0, tzinfo=timezone.utc)
WINDOW_END = datetime(2025, 5, 16, 23, 55, tzinfo=timezone.utc)
TRAILING_CONTEXT = 700
OUT_JSON = Path(__file__).parent / "eval_lit_pipeline.json"


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
        sweep_lookback_candles=12,            # 1h of M5
        min_penetration_pips=Decimal("5"),    # $0.05 wick beyond band
        min_return_pips=Decimal("5"),         # close back $0.05 inside
        base_confidence=0.5,
        flow_confirmation_bonus=0.0,          # no microstructure feed
    )


def build_sweep_config() -> SweepDetectorConfig:
    return SweepDetectorConfig(
        pip_size=Decimal("0.01"),
        sweep_lookback_candles=12,            # 1h of M5
        min_penetration_pips=Decimal("5"),    # $0.05 wick beyond band
    )


def build_phase_config() -> PhaseClassifierConfig:
    return PhaseClassifierConfig(
        inducement_freshness_candles=24,      # 2h of M5
    )


def build_validator_config() -> StructureValidatorConfig:
    return StructureValidatorConfig(
        pip_size=Decimal("0.01"),
        swing_lookback=5,
        pivot_search_window=48,               # 4h of M5
        min_post_inducement_candles=2,
        min_break_distance_pips=Decimal("3"),
    )


# -- bookkeeping -------------------------------------------------------
@dataclass
class Setup:
    """Materialised gate-passed setup for stats + viz."""

    setup_id: str
    induced_at: datetime
    confirmed_at: datetime
    direction: str
    phase: str
    inducement_confidence: float
    inducement_event_id: str
    zone_kind: str
    zone_upper: float
    zone_lower: float
    hypothetical_entry: float
    extra: dict[str, Any] = field(default_factory=dict)


def main() -> None:
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
        f"eval window: idx {first}..{last} "
        f"({all_candles[first].open_time.isoformat()} -> "
        f"{all_candles[last].open_time.isoformat()}) "
        f"= {last - first + 1} candles"
    )

    mapper = LiquidityMapper(build_mapper_config())
    detector = InducementPatternDetector(build_detector_config())
    sweeper = RuleBasedSweepDetector(build_sweep_config())
    phaser = PhaseClassifier(build_phase_config())
    validator = RuleBasedStructureValidator(build_validator_config())

    seen_setups: dict[str, Setup] = {}                # by inducement_event_id
    inducement_history: dict[str, InducementEvent] = {}  # by zone_id
    sweep_history: dict[str, SweepEvent] = {}            # by sweep_id
    phase_counter: Counter[str] = Counter()
    inducement_emit_steps: list[int] = []
    sweep_emit_steps: list[int] = []
    gate_emit_steps: list[int] = []
    validated_with_sweep_id_count = 0

    # stats bookkeeping
    setups_per_direction: Counter[str] = Counter()
    setups_per_phase: Counter[str] = Counter()
    setups_per_zone_kind: Counter[str] = Counter()

    candles_window_per_step: list[dict] = []

    for step, i in enumerate(eval_indices):
        candle = all_candles[i]
        lo = max(0, i - TRAILING_CONTEXT + 1)
        window = all_candles[lo : i + 1]

        # ---- 1. mapper ------------------------------------------------
        zones: list[LiquidityZone] = mapper.map_zones(window)

        # ---- 2. detector ---------------------------------------------
        induc = detector.detect(window, zones, None)
        latest_induc: InducementEvent | None = None
        if induc is not None:
            inducement_emit_steps.append(step)
            zid = induc.inducement_zone.zone_id
            # Dedup by zone_id: only the FIRST inducement on each zone
            # is kept (sliding window would otherwise re-emit it every
            # tick — that's "dedup gratuit" from Phase 0.3).
            if zid not in inducement_history:
                inducement_history[zid] = induc
            latest_induc = inducement_history[zid]

        # Take the freshest inducement still inside the freshness window
        # (the classifier itself will UNDEFINED-decay older ones, but we
        # also want to feed it the freshest available, not the latest
        # zone-deduped instance).
        if latest_induc is None and inducement_history:
            latest_induc = max(
                inducement_history.values(), key=lambda e: e.timestamp,
            )

        # ---- 2b. sweep detector --------------------------------------
        sweep_now = sweeper.detect(window, zones)
        if sweep_now is not None:
            sweep_emit_steps.append(step)
            sid = sweep_now.sweep_id
            if sid not in sweep_history:
                sweep_history[sid] = sweep_now
        # Latest sweep across history = freshest by timestamp
        latest_sweep: SweepEvent | None = None
        if sweep_history:
            latest_sweep = max(
                sweep_history.values(), key=lambda s: s.timestamp,
            )

        # ---- 3. phase classifier ------------------------------------
        phase: MarketPhase = phaser.classify(
            window, latest_induc, latest_sweep,
        )
        phase_counter[phase.phase.value] += 1

        # ---- 4. validator -------------------------------------------
        if latest_induc is not None and phase.phase != PhaseKind.UNDEFINED:
            try:
                gate_out: ValidatedStructureState | None = validator.validate(
                    latest_induc, phase, window,
                )
            except ValueError as e:
                # Defensive — should not fire because we already checked
                # phase != UNDEFINED and symbols match by construction.
                print(f"  validator ValueError at step {step}: {e}")
                gate_out = None

            if gate_out is not None:
                gate_emit_steps.append(step)
                if gate_out.sweep_event_id is not None:
                    validated_with_sweep_id_count += 1
                key = gate_out.inducement_event_id
                if key not in seen_setups:
                    hypo_entry = float(candle.close)
                    setup = Setup(
                        setup_id=uuid.uuid4().hex,
                        induced_at=latest_induc.timestamp,
                        confirmed_at=candle.open_time,
                        direction=gate_out.inferred_direction,
                        phase=phase.phase.value,
                        inducement_confidence=latest_induc.confidence,
                        inducement_event_id=key,
                        zone_kind=latest_induc.inducement_zone.kind.value,
                        zone_upper=float(
                            latest_induc.inducement_zone.price_upper,
                        ),
                        zone_lower=float(
                            latest_induc.inducement_zone.price_lower,
                        ),
                        hypothetical_entry=hypo_entry,
                    )
                    seen_setups[key] = setup
                    setups_per_direction[setup.direction] += 1
                    setups_per_phase[setup.phase] += 1
                    setups_per_zone_kind[setup.zone_kind] += 1

        candles_window_per_step.append({
            "t": candle.open_time.isoformat(),
            "o": float(candle.open),
            "h": float(candle.high),
            "l": float(candle.low),
            "c": float(candle.close),
        })

    # ------------------------------------------------------------------
    # stats
    # ------------------------------------------------------------------
    print()
    print("=== pipeline emission counts ===")
    print(f"  steps:                 {len(eval_indices)}")
    print(f"  ticks with inducement: {len(inducement_emit_steps)}")
    print(f"  unique inducements:    {len(inducement_history)}")
    print(f"  ticks with sweep:      {len(sweep_emit_steps)}")
    print(f"  unique sweeps:         {len(sweep_history)}")
    print(f"  ticks with gate pass:  {len(gate_emit_steps)}")
    print(f"  unique gate setups:    {len(seen_setups)}")
    print(
        f"  ticks gated w/ sweep_event_id non-None: "
        f"{validated_with_sweep_id_count}  "
        f"(structural 0 — validator does not consume sweep yet)"
    )

    print()
    print("=== phase distribution across ticks ===")
    for p in ("phase_1_inducement", "phase_2_mitigation", "undefined"):
        n = phase_counter.get(p, 0)
        print(f"  {p:<22s} {n:>6d}")

    print()
    print("=== gate setups — by direction ===")
    for d in ("bearish", "bullish"):
        print(f"  {d:<10s} {setups_per_direction.get(d, 0):>4d}")

    print()
    print("=== gate setups — by phase ===")
    for p, n in setups_per_phase.most_common():
        print(f"  {p:<22s} {n:>4d}")

    print()
    print("=== gate setups — by parent zone kind ===")
    for k, n in setups_per_zone_kind.most_common():
        print(f"  {k:<22s} {n:>4d}")

    print()
    print("=== inter-setup distance (candles, gate-confirmed events) ===")
    setups_sorted = sorted(seen_setups.values(), key=lambda s: s.confirmed_at)
    if len(setups_sorted) < 2:
        print("  (need >= 2 setups for a distribution)")
    else:
        confirm_indices = []
        ts_to_idx = {
            all_candles[i].open_time: i for i in eval_indices
        }
        for s in setups_sorted:
            if s.confirmed_at in ts_to_idx:
                confirm_indices.append(ts_to_idx[s.confirmed_at])
        confirm_indices.sort()
        gaps = [
            confirm_indices[i] - confirm_indices[i - 1]
            for i in range(1, len(confirm_indices))
        ]
        if gaps:
            gaps_sorted = sorted(gaps)
            n = len(gaps_sorted)
            print(
                f"  n={n} min={gaps_sorted[0]} "
                f"med={gaps_sorted[n // 2]} "
                f"p90={gaps_sorted[min(n - 1, int(0.9 * n))]} "
                f"max={gaps_sorted[-1]} "
                f"mean={statistics.mean(gaps):.1f}"
            )

    # ------------------------------------------------------------------
    # dump for viz
    # ------------------------------------------------------------------
    payload = {
        "window": {
            "start": WINDOW_START.isoformat(),
            "end": WINDOW_END.isoformat(),
        },
        "candles": candles_window_per_step,
        "inducements": [
            {
                "event_id": e.event_id,
                "timestamp": e.timestamp.isoformat(),
                "direction": e.inferred_direction,
                "confidence": e.confidence,
                "zone_kind": e.inducement_zone.kind.value,
                "zone_upper": float(e.inducement_zone.price_upper),
                "zone_lower": float(e.inducement_zone.price_lower),
                "zone_id": e.inducement_zone.zone_id,
            }
            for e in inducement_history.values()
        ],
        "setups": [
            {
                "setup_id": s.setup_id,
                "induced_at": s.induced_at.isoformat(),
                "confirmed_at": s.confirmed_at.isoformat(),
                "direction": s.direction,
                "phase": s.phase,
                "inducement_confidence": s.inducement_confidence,
                "inducement_event_id": s.inducement_event_id,
                "zone_kind": s.zone_kind,
                "zone_upper": s.zone_upper,
                "zone_lower": s.zone_lower,
                "hypothetical_entry": s.hypothetical_entry,
            }
            for s in setups_sorted
        ],
    }
    OUT_JSON.write_text(json.dumps(payload))
    print()
    print(f"wrote: {OUT_JSON}")


if __name__ == "__main__":
    main()
