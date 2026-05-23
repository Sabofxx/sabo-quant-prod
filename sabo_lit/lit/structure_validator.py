"""
StructureValidator — THE GATE.

The single, sovereign producer of ``ValidatedStructureState``. Downstream
consumers (arbiter, strategy, risk, execution, position manager) type
their inputs as ``ValidatedStructureState``; feeding an unvalidated
structure forward is a static type error AND a CI failure (the
``ConstructionRule`` in ``governance/dependency_rules.py`` restricts
construction of this class to ``sabo_lit.lit``).

Validation criteria (Phase 1)
-----------------------------

1. **Phase gate.** ``phase.phase`` must be ``PHASE_1_INDUCEMENT`` or
   ``PHASE_2_MITIGATION``; ``UNDEFINED`` is rejected outright.

2. **Anchor.** The inducement's ``timestamp`` is mapped onto the candle
   list by picking the latest candle whose ``open_time`` is at-or-before
   ``inducement.timestamp``. If the inducement predates every candle in
   the input, validation fails — no anchor, no structure.

3. **Post-anchor freshness.** At least ``min_post_inducement_candles``
   candles must follow the anchor; otherwise the structural break has
   had no time to print.

4. **Reference pivot.** A swing pivot of the opposing kind must exist
   inside the lookback window ending at (and including) the anchor:
       * BEARISH inducement → most-recent swing LOW before the anchor.
       * BULLISH inducement → most-recent swing HIGH before the anchor.
   Swings use the strict-greater / strict-less convention (no flat
   plateau is a swing), matching ``LiquidityMapper``.

5. **Break of structure.** At least one POST-anchor candle's CLOSE must
   pierce the reference pivot by ``min_break_distance_pips``:
       * BEARISH → ``close < pivot.low  - threshold``
       * BULLISH → ``close > pivot.high + threshold``
   This is the BOS / CHoCH confirmation: the inducement's direction must
   be ratified by the structure post-anchor, not merely inferred from
   the sweep wick.

All five conditions met → ``ValidatedStructureState`` is emitted with
``gate_passed=True`` and ``inducement_event_id`` carrying the input
inducement's id. ``sweep_event_id`` stays ``None`` in Phase 1; the gate
does not consume sweep events directly (the phase classifier already
folds sweep timing into ``phase``).

Pure compute, sync, stateless. Same input → same output.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from sabo_lit.core import (
    Candle,
    InducementEvent,
    MarketPhase,
    PhaseKind,
    ValidatedStructureState,
)
from sabo_lit.core import StructureValidator as StructureValidatorABC


@dataclass(frozen=True, kw_only=True)
class StructureValidatorConfig:
    """All knobs of ``RuleBasedStructureValidator``. Immutable.

    Every threshold is explicit; the validator itself contains no
    defaults — the bootstrap must pass a fully populated config.
    """

    pip_size: Decimal
    """One pip in price units (EURUSD = 0.0001, XAUUSD = 0.01)."""

    swing_lookback: int
    """Candles required on each side of a pivot for it to count as a
    swing. Must match the convention used by ``LiquidityMapper`` — the
    same strict-greater / strict-less rule keeps the gate's pivot
    definition compatible with the mapper's."""

    pivot_search_window: int
    """How many candles BEFORE the anchor to scan for a reference
    pivot. Larger windows tolerate longer setup legs but increase the
    risk of latching onto stale structure."""

    min_post_inducement_candles: int
    """Minimum number of candles that must follow the anchor candle in
    the input list before the gate will consider validating. Below this,
    the structural break has had no opportunity to print."""

    min_break_distance_pips: Decimal
    """Distance, in pips, beyond the reference pivot that a post-anchor
    candle's CLOSE must reach to count as a break. Filters out one-tick
    pokes that immediately reverse."""


class RuleBasedStructureValidator(StructureValidatorABC):
    """Concrete ``StructureValidator``: phase gate + BOS / CHoCH check.

    Pure compute. One instance per symbol — ``validate`` rejects
    mixed-symbol inputs rather than silently cross-routing.
    """

    def __init__(self, config: StructureValidatorConfig) -> None:
        self._validate_config(config)
        self._config = config

    @staticmethod
    def _validate_config(config: StructureValidatorConfig) -> None:
        if config.pip_size <= Decimal(0):
            raise ValueError("pip_size must be > 0")
        if config.swing_lookback < 1:
            raise ValueError("swing_lookback must be >= 1")
        if config.pivot_search_window < 1:
            raise ValueError("pivot_search_window must be >= 1")
        if config.min_post_inducement_candles < 1:
            raise ValueError("min_post_inducement_candles must be >= 1")
        if config.min_break_distance_pips < Decimal(0):
            raise ValueError("min_break_distance_pips must be >= 0")

    @property
    def config(self) -> StructureValidatorConfig:
        return self._config

    # -- public API ------------------------------------------------------
    def validate(
        self,
        inducement: InducementEvent,
        phase: MarketPhase,
        candles: list[Candle],
    ) -> ValidatedStructureState | None:
        if not candles:
            raise ValueError("validate: candles must be non-empty")
        symbol = candles[0].symbol
        for c in candles:
            if c.symbol != symbol:
                raise ValueError(
                    f"validate: mixed candle symbols ({symbol!r} and "
                    f"{c.symbol!r})."
                )
        if inducement.symbol != symbol:
            raise ValueError(
                f"validate: inducement symbol {inducement.symbol!r} does "
                f"not match candle symbol {symbol!r}."
            )
        if phase.symbol != symbol:
            raise ValueError(
                f"validate: phase symbol {phase.symbol!r} does not match "
                f"candle symbol {symbol!r}."
            )

        # 1. Phase gate — UNDEFINED is unambiguously rejected.
        if phase.phase == PhaseKind.UNDEFINED:
            return None

        # 2. Anchor — map inducement timestamp onto a candle index.
        anchor_idx = self._anchor_index(inducement, candles)
        if anchor_idx is None:
            return None

        # 3. Post-anchor freshness.
        post = candles[anchor_idx + 1 :]
        if len(post) < self._config.min_post_inducement_candles:
            return None

        # 4. Reference pivot of opposing kind.
        direction = inducement.inferred_direction
        pivot = self._reference_pivot(candles, anchor_idx, direction)
        if pivot is None:
            return None

        # 5. Break of structure.
        if not self._break_confirmed(post, pivot, direction):
            return None

        last = candles[-1]
        return ValidatedStructureState(
            symbol=symbol,
            timestamp=last.open_time,
            inferred_direction=direction,
            phase=phase,
            gate_passed=True,
            inducement_event_id=inducement.event_id,
            sweep_event_id=None,
        )

    # -- internals ------------------------------------------------------
    @staticmethod
    def _anchor_index(
        inducement: InducementEvent, candles: list[Candle]
    ) -> int | None:
        """Latest candle whose ``open_time`` is at-or-before the
        inducement timestamp. ``None`` if the inducement predates every
        candle in the window."""
        induc_ts = inducement.timestamp
        for i in range(len(candles) - 1, -1, -1):
            if candles[i].open_time <= induc_ts:
                return i
        return None

    def _reference_pivot(
        self,
        candles: list[Candle],
        anchor_idx: int,
        direction: Literal["bullish", "bearish"],
    ) -> Candle | None:
        """Most recent swing of opposing kind inside the lookback window
        ending at ``anchor_idx`` (inclusive). Returns the pivot
        ``Candle`` or ``None`` if no qualifying pivot exists.

        Swing convention (matches ``LiquidityMapper``): pivot at index
        ``i`` needs ``swing_lookback`` strictly-greater / strictly-less
        neighbours on each side.
        """
        n = self._config.swing_lookback
        window_start = max(n, anchor_idx - self._config.pivot_search_window + 1)
        window_end = anchor_idx - n  # need n neighbours to the right
        if window_end < window_start:
            return None
        # Walk newest-first so we return the FRESHEST qualifying pivot.
        for i in range(window_end, window_start - 1, -1):
            if direction == "bearish":
                # We need a swing LOW (price will break below it).
                cl = candles[i].low
                left = all(
                    candles[i - k].low > cl for k in range(1, n + 1)
                )
                right = all(
                    candles[i + k].low > cl for k in range(1, n + 1)
                )
                if left and right:
                    return candles[i]
            else:
                # Bullish — need a swing HIGH.
                ch = candles[i].high
                left = all(
                    candles[i - k].high < ch for k in range(1, n + 1)
                )
                right = all(
                    candles[i + k].high < ch for k in range(1, n + 1)
                )
                if left and right:
                    return candles[i]
        return None

    def _break_confirmed(
        self,
        post: list[Candle],
        pivot: Candle,
        direction: Literal["bullish", "bearish"],
    ) -> bool:
        threshold = (
            self._config.min_break_distance_pips * self._config.pip_size
        )
        if direction == "bearish":
            target = pivot.low - threshold
            return any(c.close < target for c in post)
        target = pivot.high + threshold
        return any(c.close > target for c in post)
