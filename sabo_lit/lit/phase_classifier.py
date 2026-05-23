"""
PhaseClassifier — concrete implementation.

Classifies the current LIT phase for a symbol given the freshest
confirmed inducement and (optionally) the freshest sweep.

Phase semantics
---------------

* ``UNDEFINED``           — no fresh inducement on record.
* ``PHASE_1_INDUCEMENT``  — an inducement has been confirmed and is
                            still within its freshness window; either
                            no sweep has happened since, or the sweep
                            predates the inducement (so the inducement
                            still leads the chain).
* ``PHASE_2_MITIGATION``  — a sweep has happened STRICTLY AFTER the
                            inducement; the LIT chain is in the
                            mitigation leg.

"Freshness" is measured in CANDLE COUNT (not wall time): the inducement
is fresh while the latest candle is no more than
``inducement_freshness_candles`` past the candle whose ``open_time``
matches (or first precedes) the inducement timestamp. Counting candles,
not seconds, makes the classifier timeframe-agnostic — a 5-minute and
a 1-hour stream both express staleness as a multiple of their own
cadence.

Stateless: same ``(candles, latest_inducement, latest_sweep)`` triple
returns the same ``MarketPhase``. ``MarketPhase`` has no construction
rule, so the classifier owns the only sensible construction site by
convention rather than by enforcement.
"""
from __future__ import annotations

from dataclasses import dataclass

from sabo_lit.core import Candle, InducementEvent, MarketPhase, SweepEvent
from sabo_lit.core import PhaseClassifier as PhaseClassifierABC
from sabo_lit.core import PhaseKind


@dataclass(frozen=True, kw_only=True)
class PhaseClassifierConfig:
    """All knobs of ``PhaseClassifier``. Immutable.

    The classifier has a single tunable — how long an inducement stays
    "fresh" before it decays to ``UNDEFINED`` — measured in candles to
    stay timeframe-agnostic.
    """

    inducement_freshness_candles: int
    """Distance (in candle count) from the inducement candle to the
    latest candle, past which the inducement is considered stale and
    the phase falls back to ``UNDEFINED``."""


class PhaseClassifier(PhaseClassifierABC):
    """Pure-compute classifier over the latest inducement / sweep pair.
    """

    def __init__(self, config: PhaseClassifierConfig) -> None:
        self._validate_config(config)
        self._config = config

    @staticmethod
    def _validate_config(config: PhaseClassifierConfig) -> None:
        if config.inducement_freshness_candles < 1:
            raise ValueError("inducement_freshness_candles must be >= 1")

    @property
    def config(self) -> PhaseClassifierConfig:
        return self._config

    # -- public API ------------------------------------------------------
    def classify(
        self,
        candles: list[Candle],
        latest_inducement: InducementEvent | None,
        latest_sweep: SweepEvent | None,
    ) -> MarketPhase:
        if not candles:
            raise ValueError("classify: candles must be non-empty")
        symbol = candles[0].symbol
        for c in candles:
            if c.symbol != symbol:
                raise ValueError(
                    f"classify: mixed candle symbols ({symbol!r} and "
                    f"{c.symbol!r})."
                )
        if latest_inducement is not None and latest_inducement.symbol != symbol:
            raise ValueError(
                f"classify: inducement symbol {latest_inducement.symbol!r} "
                f"does not match candle symbol {symbol!r}."
            )
        if latest_sweep is not None and latest_sweep.symbol != symbol:
            raise ValueError(
                f"classify: sweep symbol {latest_sweep.symbol!r} does not "
                f"match candle symbol {symbol!r}."
            )

        last = candles[-1]

        if latest_inducement is None or not self._is_fresh(
            latest_inducement, candles
        ):
            return MarketPhase(
                symbol=symbol,
                timestamp=last.open_time,
                phase=PhaseKind.UNDEFINED,
                confidence=0.0,
            )

        if (
            latest_sweep is not None
            and latest_sweep.timestamp > latest_inducement.timestamp
        ):
            phase = PhaseKind.PHASE_2_MITIGATION
        else:
            phase = PhaseKind.PHASE_1_INDUCEMENT

        return MarketPhase(
            symbol=symbol,
            timestamp=last.open_time,
            phase=phase,
            confidence=latest_inducement.confidence,
        )

    # -- internals ------------------------------------------------------
    def _is_fresh(
        self, inducement: InducementEvent, candles: list[Candle]
    ) -> bool:
        """An inducement is fresh while the LATEST candle is no more
        than ``inducement_freshness_candles`` past the candle whose
        ``open_time`` is the closest at-or-before the inducement
        timestamp. If the inducement timestamp predates every candle in
        the input, treat it as stale — the caller has effectively
        discarded its anchor."""
        induc_ts = inducement.timestamp
        anchor_idx: int | None = None
        for i in range(len(candles) - 1, -1, -1):
            if candles[i].open_time <= induc_ts:
                anchor_idx = i
                break
        if anchor_idx is None:
            return False
        distance = (len(candles) - 1) - anchor_idx
        return distance <= self._config.inducement_freshness_candles
