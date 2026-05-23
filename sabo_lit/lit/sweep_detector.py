"""
RuleBasedSweepDetector — sole producer of ``SweepEvent``.

Detects a liquidity sweep: any candle whose wick pierces a previously-
mapped ``LiquidityZone`` by at least ``min_penetration_pips``. A sweep
is NOT conditional on a subsequent rejection (that is the
``InducementPatternDetector``'s job — sweep + return); a pure sweep
fires the moment the level is taken out.

Direction
---------
* HIGH-side zone (``EQUAL_HIGHS``, ``ROLLING_HIGH``,
  ``PREVIOUS_DAY_HIGH``) pierced upward  → ``swept_direction="upside"``.
* LOW-side  zone (``EQUAL_LOWS``,  ``ROLLING_LOW``,
  ``PREVIOUS_DAY_LOW``) pierced downward → ``swept_direction="downside"``.
* ``TRENDLINE_LIQUIDITY`` is skipped — same Phase 1 exclusion as
  ``LiquidityMapper`` and ``InducementPatternDetector``.

Multi-zone disambiguation
-------------------------
At most one ``SweepEvent`` per call. When several zones qualify, the
FRESHEST sweep candle wins; ties broken by ``zone.strength_score``.
Same convention as ``InducementPatternDetector``.

Timestamp
---------
``SweepEvent.timestamp`` is the **sweep candle's open_time** (not the
caller's "current" tick). This is the moment the sweep actually
happened; PhaseClassifier relies on this to decide whether a sweep is
strictly newer than the latest inducement (PHASE_2_MITIGATION trigger).

Deterministic id
----------------
``SweepEvent.sweep_id = sha256("sweep|<zone_id>|<timestamp.isoformat()>")[:16]``.
Same input → same id, per CONVENTIONS.md §13.

Sole producer
-------------
``CONSTRUCTION_RULES`` in ``governance/dependency_rules.py`` restricts
instantiation of ``SweepEvent`` to ``sabo_lit.lit``. This module is the
sole producer.

Pure compute, sync, stateless. One detector per symbol.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from sabo_lit.core import (
    Candle,
    LiquidityZone,
    LiquidityZoneKind,
    SweepEvent,
)
from sabo_lit.core import SweepDetector as SweepDetectorABC


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
class SweepDetectorConfig:
    """All knobs of ``RuleBasedSweepDetector``. Immutable.

    Every threshold is explicit; the detector itself contains no
    defaults — the bootstrap must pass a fully populated config.
    """

    pip_size: Decimal

    sweep_lookback_candles: int
    """How many trailing candles to scan for the sweep."""

    min_penetration_pips: Decimal
    """Sweep candle must pierce the zone band by at least this many pips
    (HIGH-side: ``high - price_upper``; LOW-side: ``price_lower - low``)."""


class RuleBasedSweepDetector(SweepDetectorABC):
    """Geometric sweep detector. One instance per symbol."""

    def __init__(self, config: SweepDetectorConfig) -> None:
        self._validate_config(config)
        self._config = config

    @staticmethod
    def _validate_config(config: SweepDetectorConfig) -> None:
        if config.pip_size <= Decimal(0):
            raise ValueError("pip_size must be > 0")
        if config.sweep_lookback_candles < 1:
            raise ValueError("sweep_lookback_candles must be >= 1")
        if config.min_penetration_pips < Decimal(0):
            raise ValueError("min_penetration_pips must be >= 0")

    @property
    def config(self) -> SweepDetectorConfig:
        return self._config

    # -- public API ------------------------------------------------------
    def detect(
        self,
        candles: list[Candle],
        liquidity_zones: list[LiquidityZone],
    ) -> SweepEvent | None:
        if not candles:
            return None
        symbol = candles[0].symbol
        for c in candles:
            if c.symbol != symbol:
                raise ValueError(
                    f"detect: mixed candle symbols ({symbol!r} and "
                    f"{c.symbol!r}); instantiate one detector per symbol."
                )
        for z in liquidity_zones:
            if z.symbol != symbol:
                raise ValueError(
                    f"detect: zone symbol {z.symbol!r} does not match "
                    f"candle symbol {symbol!r}."
                )
        if not liquidity_zones:
            return None

        window = candles[-self._config.sweep_lookback_candles :]
        pen_threshold = (
            self._config.min_penetration_pips * self._config.pip_size
        )

        candidates: list[
            tuple[Candle, LiquidityZone, Literal["upside", "downside"]]
        ] = []

        for zone in liquidity_zones:
            is_high = zone.kind in _HIGH_SIDE_KINDS
            is_low = zone.kind in _LOW_SIDE_KINDS
            if not (is_high or is_low):
                # TRENDLINE_LIQUIDITY (or any future neutral kind) skipped.
                continue
            sweep_candle = self._find_sweep(
                window, zone, pen_threshold, is_high,
            )
            if sweep_candle is None:
                continue
            direction: Literal["upside", "downside"] = (
                "upside" if is_high else "downside"
            )
            candidates.append((sweep_candle, zone, direction))

        if not candidates:
            return None

        candidates.sort(
            key=lambda t: (t[0].open_time, t[1].strength_score),
            reverse=True,
        )
        sweep_candle, zone, direction = candidates[0]

        return SweepEvent(
            sweep_id=self._sweep_id(zone, sweep_candle.open_time),
            symbol=symbol,
            timestamp=sweep_candle.open_time,
            swept_zone=zone,
            swept_direction=direction,
            # ``rejection_strength`` is a Phase 1.4 proxy: the swept
            # zone's own strength score. A pure-sweep event has no
            # close-back data to score from; finer estimation is left
            # to later phases that consume order-flow microstructure.
            rejection_strength=zone.strength_score,
        )

    # -- internals ------------------------------------------------------
    @staticmethod
    def _find_sweep(
        window: list[Candle],
        zone: LiquidityZone,
        pen_threshold: Decimal,
        is_high: bool,
    ) -> Candle | None:
        """Most recent candle in ``window`` whose wick pierces the zone
        band by at least ``pen_threshold``. ``None`` if none qualifies.
        """
        for candle in reversed(window):
            if is_high:
                if candle.high - zone.price_upper >= pen_threshold:
                    return candle
            else:
                if zone.price_lower - candle.low >= pen_threshold:
                    return candle
        return None

    @staticmethod
    def _sweep_id(zone: LiquidityZone, timestamp: datetime) -> str:
        """Deterministic 16-char hex identity of a SweepEvent.

        Hash inputs: literal ``"sweep"``, the swept zone's
        deterministic ``zone_id``, and ``timestamp.isoformat()``. Same
        inputs → same id. See CONVENTIONS.md §13.
        """
        payload = f"sweep|{zone.zone_id}|{timestamp.isoformat()}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
