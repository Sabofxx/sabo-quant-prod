"""
InducementPatternDetector — concrete implementation.

Detects a confirmed LIT inducement: price sweeps a previously-mapped
``LiquidityZone`` (takes out the stop level), then immediately rejects
back through the band. At most one ``InducementEvent`` is returned per
call — the FRESHEST sweep wins when several zones qualify.

LIT semantics
-------------

* Upside sweep on a HIGH-side zone (``EQUAL_HIGHS``, ``ROLLING_HIGH``,
  ``PREVIOUS_DAY_HIGH``): liquidity above the level has been taken;
  the rejection back below the band implies stops above were the
  target. Inferred direction is **bearish**.
* Downside sweep on a LOW-side zone (``EQUAL_LOWS``, ``ROLLING_LOW``,
  ``PREVIOUS_DAY_LOW``): symmetric. Inferred direction is **bullish**.
* ``TRENDLINE_LIQUIDITY`` is not handled in Phase 1 — it is silently
  skipped, mirroring ``LiquidityMapper``'s Phase 1 exclusion.

Sole producer of ``InducementEvent``
------------------------------------
``InducementEvent.confirmed`` is ``Literal[True]``; a
``ConstructionRule`` in ``governance/dependency_rules.py`` restricts
instantiation to ``sabo_lit.lit``. This module is that single producer.

Statelessness
-------------
Same ``(candles, zones, flow)`` triple returns the same ``InducementEvent``
byte-for-byte, including ``event_id`` — that id is a deterministic
16-char hash of ``(kind, inducement_zone.zone_id, timestamp)`` per
CONVENTIONS.md §13. Callers can deduplicate across ticks by ``event_id``
alone (or, equivalently, by ``event.inducement_zone.zone_id`` since the
hash is injective on that key given a fixed timestamp).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from sabo_lit.core import Candle
from sabo_lit.core import InducementDetector as InducementDetectorABC
from sabo_lit.core import (
    InducementEvent,
    LiquidityZone,
    LiquidityZoneKind,
    OrderFlowSnapshot,
)


_HIGH_SIDE_KINDS: frozenset[LiquidityZoneKind] = frozenset(
    {
        LiquidityZoneKind.EQUAL_HIGHS,
        LiquidityZoneKind.ROLLING_HIGH,
        LiquidityZoneKind.PREVIOUS_DAY_HIGH,
    }
)

_LOW_SIDE_KINDS: frozenset[LiquidityZoneKind] = frozenset(
    {
        LiquidityZoneKind.EQUAL_LOWS,
        LiquidityZoneKind.ROLLING_LOW,
        LiquidityZoneKind.PREVIOUS_DAY_LOW,
    }
)


@dataclass(frozen=True, kw_only=True)
class InducementDetectorConfig:
    """All knobs of ``InducementPatternDetector``. Immutable.

    Every threshold is explicit; the detector itself contains no
    defaults — the bootstrap must pass a fully populated config.
    """

    # -- symbol scale ---------------------------------------------------
    pip_size: Decimal

    # -- sweep window ---------------------------------------------------
    sweep_lookback_candles: int
    """How many trailing candles to scan for the sweep. The rejection
    is always tested on the LAST candle of the input list."""

    # -- thresholds (in pips) ------------------------------------------
    min_penetration_pips: Decimal
    """Sweep candle must pierce the zone band by at least this many pips
    (HIGH-side: ``high - price_upper``; LOW-side: ``price_lower - low``).
    A penetration of zero would let any candle that merely touches the
    band qualify."""

    min_return_pips: Decimal
    """The CURRENT (last) candle must close back through the band by at
    least this many pips on the rejection side. For a HIGH-side sweep,
    ``price_upper - close`` >= threshold; for LOW-side, symmetric."""

    # -- confidence -----------------------------------------------------
    base_confidence: float
    """Confidence floor assigned when the geometric pattern is confirmed
    with no microstructure input."""

    flow_confirmation_bonus: float
    """Bonus added to ``base_confidence`` when an ``OrderFlowSnapshot``
    is provided and its ``delta`` agrees with the inferred direction
    (negative delta with bearish; positive delta with bullish). Capped
    at 1.0 after addition."""


class InducementPatternDetector(InducementDetectorABC):
    """Geometric + optional-flow inducement detector.

    Pure-compute. Sync. One detector per symbol — ``detect`` rejects
    mixed-symbol inputs rather than silently routing across instruments.
    """

    def __init__(self, config: InducementDetectorConfig) -> None:
        self._validate_config(config)
        self._config = config

    @staticmethod
    def _validate_config(config: InducementDetectorConfig) -> None:
        if config.pip_size <= Decimal(0):
            raise ValueError("pip_size must be > 0")
        if config.sweep_lookback_candles < 1:
            raise ValueError("sweep_lookback_candles must be >= 1")
        if config.min_penetration_pips < Decimal(0):
            raise ValueError("min_penetration_pips must be >= 0")
        if config.min_return_pips < Decimal(0):
            raise ValueError("min_return_pips must be >= 0")
        if not (0.0 <= config.base_confidence <= 1.0):
            raise ValueError("base_confidence must be in [0, 1]")
        if not (0.0 <= config.flow_confirmation_bonus <= 1.0):
            raise ValueError("flow_confirmation_bonus must be in [0, 1]")

    @property
    def config(self) -> InducementDetectorConfig:
        return self._config

    # -- public API ------------------------------------------------------
    def detect(
        self,
        candles: list[Candle],
        zones: list[LiquidityZone],
        flow: OrderFlowSnapshot | None,
    ) -> InducementEvent | None:
        # Without candles we cannot even identify the symbol; treat as
        # a no-op rather than an error.
        if not candles:
            return None
        symbol = candles[0].symbol
        # Symbol consistency is a programmer-error check and runs ALWAYS,
        # even when zones is empty — silently routing across symbols
        # would be a worse failure than a noisy ValueError.
        for c in candles:
            if c.symbol != symbol:
                raise ValueError(
                    f"detect: mixed candle symbols ({symbol!r} and "
                    f"{c.symbol!r}); instantiate one detector per symbol."
                )
        for z in zones:
            if z.symbol != symbol:
                raise ValueError(
                    f"detect: zone symbol {z.symbol!r} does not match "
                    f"candle symbol {symbol!r}."
                )
        if flow is not None and flow.symbol != symbol:
            raise ValueError(
                f"detect: flow symbol {flow.symbol!r} does not match "
                f"candle symbol {symbol!r}."
            )
        if not zones:
            return None

        last = candles[-1]
        window = candles[-self._config.sweep_lookback_candles :]
        pen_threshold = (
            self._config.min_penetration_pips * self._config.pip_size
        )
        ret_threshold = self._config.min_return_pips * self._config.pip_size

        # Candidates collected across all zones; pick the freshest sweep.
        candidates: list[
            tuple[Candle, LiquidityZone, Literal["bullish", "bearish"]]
        ] = []

        for zone in zones:
            is_high = zone.kind in _HIGH_SIDE_KINDS
            is_low = zone.kind in _LOW_SIDE_KINDS
            if not (is_high or is_low):
                # TRENDLINE_LIQUIDITY (or any future neutral kind) is not
                # handled in Phase 1.
                continue

            if is_high:
                # Rejection must be present on the LAST candle first —
                # cheap check that prunes most zones.
                if zone.price_upper - last.close < ret_threshold:
                    continue
            else:
                if last.close - zone.price_lower < ret_threshold:
                    continue

            sweep_candle = self._find_sweep(window, zone, pen_threshold, is_high)
            if sweep_candle is None:
                continue

            direction: Literal["bullish", "bearish"] = (
                "bearish" if is_high else "bullish"
            )
            candidates.append((sweep_candle, zone, direction))

        if not candidates:
            return None

        # Freshest sweep wins; tie-break on stronger zone.
        candidates.sort(
            key=lambda t: (t[0].open_time, t[1].strength_score),
            reverse=True,
        )
        sweep_candle, zone, direction = candidates[0]

        confidence = self._confidence(direction, flow)

        return InducementEvent(
            event_id=self._event_id(zone, last.open_time),
            symbol=symbol,
            timestamp=last.open_time,
            inducement_zone=zone,
            confirmed=True,
            inferred_direction=direction,
            confidence=confidence,
        )

    @staticmethod
    def _event_id(zone: LiquidityZone, timestamp: datetime) -> str:
        """Deterministic 16-char hex identity of an InducementEvent.

        Hash inputs: literal tag ``"inducement"``, the parent zone's
        deterministic ``zone_id``, and ``timestamp.isoformat()``. Same
        inputs → same id by construction. See CONVENTIONS.md §13.
        """
        payload = f"inducement|{zone.zone_id}|{timestamp.isoformat()}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    # -- internals ------------------------------------------------------
    @staticmethod
    def _find_sweep(
        window: list[Candle],
        zone: LiquidityZone,
        pen_threshold: Decimal,
        is_high: bool,
    ) -> Candle | None:
        """Return the MOST RECENT candle in ``window`` whose wick pierced
        the zone band by at least ``pen_threshold``. ``None`` if no
        candle qualifies."""
        for candle in reversed(window):
            if is_high:
                if candle.high - zone.price_upper >= pen_threshold:
                    return candle
            else:
                if zone.price_lower - candle.low >= pen_threshold:
                    return candle
        return None

    def _confidence(
        self,
        direction: Literal["bullish", "bearish"],
        flow: OrderFlowSnapshot | None,
    ) -> float:
        base = self._config.base_confidence
        if flow is None:
            return base
        # Flow agreement: a bearish inducement is corroborated by net
        # sell pressure (delta < 0); a bullish one by net buy pressure.
        agrees = (direction == "bearish" and flow.delta < Decimal(0)) or (
            direction == "bullish" and flow.delta > Decimal(0)
        )
        if not agrees:
            return base
        return min(1.0, base + self._config.flow_confirmation_bonus)
