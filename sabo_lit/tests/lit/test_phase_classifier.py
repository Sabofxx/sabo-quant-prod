"""
Tests for ``sabo_lit.lit.phase_classifier.PhaseClassifier``.

Coverage axes: phase routing (UNDEFINED / PHASE_1 / PHASE_2) under
every relevant combination of ``latest_inducement`` /
``latest_sweep``, freshness decay, symbol guards, config validation,
and the small but important details (timestamp, confidence carry).

InducementEvent construction
----------------------------
``InducementEvent`` is construction-restricted to ``sabo_lit.lit`` by
``CONSTRUCTION_RULES`` — direct ``InducementEvent(...)`` in a test
module would fail the dependency scan. So the helper below mints
events via the real ``InducementPatternDetector``: the same path the
production pipeline uses. ``SweepEvent`` and ``LiquidityZone`` have
no such restriction and are built directly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from sabo_lit.core import (
    Candle,
    InducementEvent,
    LiquidityZone,
    LiquidityZoneKind,
    PhaseKind,
    SweepEvent,
)
from sabo_lit.lit.inducement_detector import (
    InducementDetectorConfig,
    InducementPatternDetector,
)
from sabo_lit.lit.phase_classifier import PhaseClassifier, PhaseClassifierConfig
from sabo_lit.lit.sweep_detector import (
    RuleBasedSweepDetector,
    SweepDetectorConfig,
)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
_T0 = datetime(2026, 5, 19, 8, 0, 0, tzinfo=timezone.utc)
_M5 = timedelta(minutes=5)


def _config(**overrides: Any) -> PhaseClassifierConfig:
    base: dict[str, Any] = {"inducement_freshness_candles": 20}
    base.update(overrides)
    return PhaseClassifierConfig(**base)


def _candle(i: int = 0, *, symbol: str = "EURUSD") -> Candle:
    return Candle(
        symbol=symbol,
        timeframe="M5",
        open_time=_T0 + i * _M5,
        open=Decimal("1.10000"),
        high=Decimal("1.10010"),
        low=Decimal("1.09990"),
        close=Decimal("1.10000"),
        volume=Decimal("100"),
    )


def _zone(*, symbol: str = "EURUSD") -> LiquidityZone:
    return LiquidityZone(
        zone_id="deadbeefdeadbeef",
        symbol=symbol,
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        price_upper=Decimal("1.10055"),
        price_lower=Decimal("1.10045"),
        created_at=_T0,
        strength_score=0.6,
    )


def _make_inducement(
    *,
    timestamp: datetime,
    symbol: str = "EURUSD",
    confidence: float = 0.7,
) -> InducementEvent:
    """Mint a real ``InducementEvent`` at exactly ``timestamp``.

    Runs the production detector on a minimal sweep+return fixture so
    the event flows through ``sabo_lit.lit`` — the only module allowed
    to instantiate ``InducementEvent`` per ``CONSTRUCTION_RULES``.
    """
    det = InducementPatternDetector(
        InducementDetectorConfig(
            pip_size=Decimal("0.0001"),
            sweep_lookback_candles=2,
            min_penetration_pips=Decimal("1.0"),
            min_return_pips=Decimal("0.0"),
            base_confidence=confidence,
            flow_confirmation_bonus=0.0,
        )
    )
    zone = LiquidityZone(
        zone_id="deadbeefdeadbeef",
        symbol=symbol,
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        price_upper=Decimal("1.10055"),
        price_lower=Decimal("1.10045"),
        created_at=timestamp,
        strength_score=0.6,
    )
    sweep_candle = Candle(
        symbol=symbol, timeframe="M5",
        open_time=timestamp - _M5,
        open=Decimal("1.10000"), high=Decimal("1.10100"),
        low=Decimal("1.09990"), close=Decimal("1.10000"),
        volume=Decimal("100"),
    )
    rejection_candle = Candle(
        symbol=symbol, timeframe="M5",
        open_time=timestamp,
        open=Decimal("1.10000"), high=Decimal("1.10010"),
        low=Decimal("1.09990"), close=Decimal("1.10000"),
        volume=Decimal("100"),
    )
    event = det.detect([sweep_candle, rejection_candle], [zone], None)
    assert event is not None
    return event


def _inducement(
    *,
    i: int,
    symbol: str = "EURUSD",
    confidence: float = 0.7,
) -> InducementEvent:
    """Inducement anchored at candle index ``i``."""
    return _make_inducement(
        timestamp=_T0 + i * _M5, symbol=symbol, confidence=confidence,
    )


def _sweep(*, i: int, symbol: str = "EURUSD") -> SweepEvent:
    """Mint a real ``SweepEvent`` at exactly ``_T0 + i * _M5`` via the
    production detector. Direct construction is forbidden by
    ``CONSTRUCTION_RULES``."""
    det = RuleBasedSweepDetector(
        SweepDetectorConfig(
            pip_size=Decimal("0.0001"),
            sweep_lookback_candles=2,
            min_penetration_pips=Decimal("1.0"),
        )
    )
    sweep_ts = _T0 + i * _M5
    zone = LiquidityZone(
        zone_id="deadbeefdeadbeef",
        symbol=symbol,
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        price_upper=Decimal("1.10055"),
        price_lower=Decimal("1.10045"),
        created_at=sweep_ts,
        strength_score=0.6,
    )
    prior = Candle(
        symbol=symbol, timeframe="M5",
        open_time=sweep_ts - _M5,
        open=Decimal("1.10000"), high=Decimal("1.10010"),
        low=Decimal("1.09990"), close=Decimal("1.10000"),
        volume=Decimal("100"),
    )
    sweep_candle = Candle(
        symbol=symbol, timeframe="M5",
        open_time=sweep_ts,
        open=Decimal("1.10000"), high=Decimal("1.10100"),
        low=Decimal("1.09990"), close=Decimal("1.10080"),
        volume=Decimal("100"),
    )
    event = det.detect([prior, sweep_candle], [zone])
    assert event is not None
    return event


# ----------------------------------------------------------------------------
# UNDEFINED routing
# ----------------------------------------------------------------------------
def test_no_inducement_returns_undefined() -> None:
    pc = PhaseClassifier(_config())
    out = pc.classify([_candle(i) for i in range(5)], None, None)
    assert out.phase == PhaseKind.UNDEFINED
    assert out.confidence == 0.0


def test_no_inducement_but_sweep_present_still_undefined() -> None:
    """A sweep alone, with no inducement on record, cannot anchor a
    phase — the LIT chain has no head."""
    pc = PhaseClassifier(_config())
    out = pc.classify([_candle(i) for i in range(5)], None, _sweep(i=2))
    assert out.phase == PhaseKind.UNDEFINED


def test_stale_inducement_falls_back_to_undefined() -> None:
    """Inducement older than ``inducement_freshness_candles`` decays
    to UNDEFINED."""
    pc = PhaseClassifier(_config(inducement_freshness_candles=3))
    candles = [_candle(i) for i in range(10)]
    # Inducement anchored at candle index 2; latest is index 9 -> distance 7.
    out = pc.classify(candles, _inducement(i=2), None)
    assert out.phase == PhaseKind.UNDEFINED


def test_inducement_predating_all_candles_is_stale() -> None:
    pc = PhaseClassifier(_config(inducement_freshness_candles=5))
    candles = [_candle(i) for i in range(5)]
    # Inducement at i=-10 — predates every candle in the window.
    out = pc.classify(candles, _inducement(i=-10), None)
    assert out.phase == PhaseKind.UNDEFINED


# ----------------------------------------------------------------------------
# PHASE_1_INDUCEMENT routing
# ----------------------------------------------------------------------------
def test_fresh_inducement_no_sweep_is_phase_1() -> None:
    pc = PhaseClassifier(_config(inducement_freshness_candles=10))
    candles = [_candle(i) for i in range(6)]
    out = pc.classify(candles, _inducement(i=3), None)
    assert out.phase == PhaseKind.PHASE_1_INDUCEMENT


def test_fresh_inducement_with_older_sweep_is_phase_1() -> None:
    """Sweep timestamp is BEFORE inducement timestamp — sweep is from a
    previous chain and irrelevant; inducement still leads."""
    pc = PhaseClassifier(_config(inducement_freshness_candles=10))
    candles = [_candle(i) for i in range(8)]
    out = pc.classify(
        candles,
        _inducement(i=5),
        _sweep(i=2),  # older than inducement
    )
    assert out.phase == PhaseKind.PHASE_1_INDUCEMENT


def test_fresh_inducement_with_equal_timestamp_sweep_is_phase_1() -> None:
    """``latest_sweep.timestamp > inducement.timestamp`` is the trigger;
    equality stays in phase 1 (no strictly-newer sweep)."""
    pc = PhaseClassifier(_config(inducement_freshness_candles=10))
    candles = [_candle(i) for i in range(6)]
    out = pc.classify(
        candles,
        _inducement(i=3),
        _sweep(i=3),
    )
    assert out.phase == PhaseKind.PHASE_1_INDUCEMENT


# ----------------------------------------------------------------------------
# PHASE_2_MITIGATION routing
# ----------------------------------------------------------------------------
def test_fresh_inducement_with_newer_sweep_is_phase_2() -> None:
    pc = PhaseClassifier(_config(inducement_freshness_candles=10))
    candles = [_candle(i) for i in range(8)]
    out = pc.classify(
        candles,
        _inducement(i=3),
        _sweep(i=6),
    )
    assert out.phase == PhaseKind.PHASE_2_MITIGATION


# ----------------------------------------------------------------------------
# Output details
# ----------------------------------------------------------------------------
def test_market_phase_timestamp_is_last_candle_open_time() -> None:
    pc = PhaseClassifier(_config())
    candles = [_candle(i) for i in range(7)]
    out = pc.classify(candles, None, None)
    assert out.timestamp == candles[-1].open_time


def test_market_phase_symbol_matches_candle_symbol() -> None:
    pc = PhaseClassifier(_config())
    candles = [_candle(i, symbol="XAUUSD") for i in range(3)]
    out = pc.classify(candles, None, None)
    assert out.symbol == "XAUUSD"


def test_phase_1_confidence_is_inducement_confidence() -> None:
    pc = PhaseClassifier(_config(inducement_freshness_candles=10))
    candles = [_candle(i) for i in range(6)]
    out = pc.classify(candles, _inducement(i=3, confidence=0.42), None)
    assert out.confidence == 0.42


def test_phase_2_confidence_is_inducement_confidence() -> None:
    pc = PhaseClassifier(_config(inducement_freshness_candles=10))
    candles = [_candle(i) for i in range(8)]
    out = pc.classify(
        candles,
        _inducement(i=3, confidence=0.55),
        _sweep(i=6),
    )
    assert out.confidence == 0.55


def test_undefined_confidence_is_zero() -> None:
    pc = PhaseClassifier(_config())
    out = pc.classify([_candle(i) for i in range(3)], None, None)
    assert out.confidence == 0.0


# ----------------------------------------------------------------------------
# Mixed-symbol / empty guards
# ----------------------------------------------------------------------------
def test_empty_candles_raises() -> None:
    pc = PhaseClassifier(_config())
    with pytest.raises(ValueError, match="candles must be non-empty"):
        pc.classify([], None, None)


def test_mixed_candle_symbols_rejected() -> None:
    pc = PhaseClassifier(_config())
    candles = [_candle(0, symbol="EURUSD"), _candle(1, symbol="GBPUSD")]
    with pytest.raises(ValueError, match="mixed candle symbols"):
        pc.classify(candles, None, None)


def test_inducement_symbol_mismatch_rejected() -> None:
    pc = PhaseClassifier(_config())
    candles = [_candle(i, symbol="EURUSD") for i in range(3)]
    with pytest.raises(ValueError, match="inducement symbol"):
        pc.classify(candles, _inducement(i=1, symbol="GBPUSD"), None)


def test_sweep_symbol_mismatch_rejected() -> None:
    pc = PhaseClassifier(_config())
    candles = [_candle(i, symbol="EURUSD") for i in range(3)]
    with pytest.raises(ValueError, match="sweep symbol"):
        pc.classify(candles, None, _sweep(i=1, symbol="GBPUSD"))


# ----------------------------------------------------------------------------
# Freshness edge — boundary
# ----------------------------------------------------------------------------
def test_inducement_at_exactly_freshness_boundary_still_fresh() -> None:
    """Distance == freshness: boundary stays IN-window per ``<=``."""
    pc = PhaseClassifier(_config(inducement_freshness_candles=5))
    candles = [_candle(i) for i in range(10)]
    # Inducement at idx 4, last is idx 9 -> distance 5
    out = pc.classify(candles, _inducement(i=4), None)
    assert out.phase == PhaseKind.PHASE_1_INDUCEMENT


def test_inducement_one_past_boundary_is_stale() -> None:
    pc = PhaseClassifier(_config(inducement_freshness_candles=5))
    candles = [_candle(i) for i in range(11)]
    # Inducement at idx 4, last is idx 10 -> distance 6 > 5
    out = pc.classify(candles, _inducement(i=4), None)
    assert out.phase == PhaseKind.UNDEFINED


def test_inducement_timestamp_between_candles_anchors_to_earlier() -> None:
    """If inducement timestamp falls strictly between two candle
    open_times, the anchor is the LATEST candle at-or-before — i.e.
    the EARLIER of the two surrounding bars."""
    pc = PhaseClassifier(_config(inducement_freshness_candles=2))
    candles = [_candle(i) for i in range(6)]
    # Inducement between idx 3 and idx 4 (offset by 2 minutes).
    induc = _make_inducement(
        timestamp=_T0 + 3 * _M5 + timedelta(minutes=2),
    )
    # Anchor = idx 3; latest = idx 5; distance = 2 (within freshness 2).
    out = pc.classify(candles, induc, None)
    assert out.phase == PhaseKind.PHASE_1_INDUCEMENT


# ----------------------------------------------------------------------------
# Config validation
# ----------------------------------------------------------------------------
def test_config_freshness_must_be_positive() -> None:
    with pytest.raises(ValueError, match="inducement_freshness_candles"):
        PhaseClassifier(_config(inducement_freshness_candles=0))
