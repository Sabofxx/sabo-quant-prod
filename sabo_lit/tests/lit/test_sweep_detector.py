"""
Tests for ``sabo_lit.lit.sweep_detector.RuleBasedSweepDetector``.

Coverage shape mirrors ``test_inducement_detector.py``: each detection
axis (sweep direction, zone-kind routing, multi-zone disambiguation,
deterministic id) has a POSITIVE and a NEGATIVE case, plus edge cases
(empty inputs, mixed symbols, config validation, traceability fields).

``SweepEvent`` is construction-restricted to ``sabo_lit.lit`` by
``CONSTRUCTION_RULES`` — tests get events through the production
detector only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from sabo_lit.core import Candle, LiquidityZone, LiquidityZoneKind
from sabo_lit.lit.sweep_detector import (
    RuleBasedSweepDetector,
    SweepDetectorConfig,
)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
_T0 = datetime(2026, 5, 19, 8, 0, 0, tzinfo=timezone.utc)
_M5 = timedelta(minutes=5)


def _config(**overrides: Any) -> SweepDetectorConfig:
    base: dict[str, Any] = {
        "pip_size": Decimal("0.0001"),
        "sweep_lookback_candles": 5,
        "min_penetration_pips": Decimal("1.0"),
    }
    base.update(overrides)
    return SweepDetectorConfig(**base)


def _candle(
    i: int = 0,
    *,
    symbol: str = "EURUSD",
    high: str = "1.10010",
    low: str = "1.09990",
    open_: str = "1.10000",
    close: str = "1.10000",
    timestamp: datetime | None = None,
) -> Candle:
    t = timestamp if timestamp is not None else _T0 + i * _M5
    return Candle(
        symbol=symbol,
        timeframe="M5",
        open_time=t,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("100"),
    )


def _zone(
    *,
    kind: LiquidityZoneKind,
    upper: str,
    lower: str,
    symbol: str = "EURUSD",
    strength: float = 0.6,
    created_at: datetime | None = None,
    zone_id: str = "deadbeefdeadbeef",
) -> LiquidityZone:
    return LiquidityZone(
        zone_id=zone_id,
        symbol=symbol,
        kind=kind,
        price_upper=Decimal(upper),
        price_lower=Decimal(lower),
        created_at=created_at if created_at is not None else _T0,
        strength_score=strength,
    )


# ----------------------------------------------------------------------------
# Empty / degenerate
# ----------------------------------------------------------------------------
def test_empty_candles_returns_none() -> None:
    det = RuleBasedSweepDetector(_config())
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS, upper="1.10055", lower="1.10045",
    )
    assert det.detect([], [zone]) is None


def test_empty_zones_returns_none() -> None:
    det = RuleBasedSweepDetector(_config())
    assert det.detect([_candle(i) for i in range(5)], []) is None


# ----------------------------------------------------------------------------
# Upside sweep
# ----------------------------------------------------------------------------
def test_upside_sweep_emits_upside_direction() -> None:
    det = RuleBasedSweepDetector(_config())
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS, upper="1.10055", lower="1.10045",
    )
    candles = [_candle(i) for i in range(4)]
    candles.append(_candle(4, high="1.10100", low="1.09990", close="1.10080"))
    event = det.detect(candles, [zone])
    assert event is not None
    assert event.swept_direction == "upside"
    assert event.swept_zone.zone_id == zone.zone_id


def test_high_zone_not_pierced_returns_none() -> None:
    det = RuleBasedSweepDetector(_config())
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS, upper="1.10100", lower="1.10090",
    )
    candles = [_candle(i, high="1.10010", low="1.09990") for i in range(5)]
    assert det.detect(candles, [zone]) is None


def test_penetration_below_threshold_returns_none() -> None:
    det = RuleBasedSweepDetector(_config(min_penetration_pips=Decimal("5.0")))
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS, upper="1.10055", lower="1.10045",
    )
    candles = [_candle(i) for i in range(4)]
    # high = 1.10058 -> 3 pip penetration, threshold = 5 pips
    candles.append(_candle(4, high="1.10058", low="1.09990", close="1.10000"))
    assert det.detect(candles, [zone]) is None


def test_sweep_outside_lookback_returns_none() -> None:
    det = RuleBasedSweepDetector(_config(sweep_lookback_candles=3))
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS, upper="1.10055", lower="1.10045",
    )
    candles = [_candle(i) for i in range(10)]
    candles[2] = _candle(2, high="1.10100", low="1.09990", close="1.10000")
    assert det.detect(candles, [zone]) is None


# ----------------------------------------------------------------------------
# Downside sweep
# ----------------------------------------------------------------------------
def test_downside_sweep_emits_downside_direction() -> None:
    det = RuleBasedSweepDetector(_config())
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_LOWS, upper="1.09955", lower="1.09945",
    )
    candles = [_candle(i, high="1.10010", low="1.09990") for i in range(4)]
    candles.append(_candle(4, high="1.10010", low="1.09900", close="1.10000"))
    event = det.detect(candles, [zone])
    assert event is not None
    assert event.swept_direction == "downside"


def test_low_zone_not_pierced_returns_none() -> None:
    det = RuleBasedSweepDetector(_config())
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_LOWS, upper="1.09800", lower="1.09790",
    )
    candles = [_candle(i, high="1.10010", low="1.09990") for i in range(5)]
    assert det.detect(candles, [zone]) is None


# ----------------------------------------------------------------------------
# Zone-kind routing
# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kind",
    [LiquidityZoneKind.ROLLING_HIGH, LiquidityZoneKind.PREVIOUS_DAY_HIGH],
)
def test_other_high_kinds_emit_upside(kind: LiquidityZoneKind) -> None:
    det = RuleBasedSweepDetector(_config())
    zone = _zone(kind=kind, upper="1.10055", lower="1.10045")
    candles = [_candle(i) for i in range(4)]
    candles.append(_candle(4, high="1.10100", low="1.09990", close="1.10080"))
    event = det.detect(candles, [zone])
    assert event is not None
    assert event.swept_direction == "upside"


@pytest.mark.parametrize(
    "kind",
    [LiquidityZoneKind.ROLLING_LOW, LiquidityZoneKind.PREVIOUS_DAY_LOW],
)
def test_other_low_kinds_emit_downside(kind: LiquidityZoneKind) -> None:
    det = RuleBasedSweepDetector(_config())
    zone = _zone(kind=kind, upper="1.09955", lower="1.09945")
    candles = [_candle(i) for i in range(4)]
    candles.append(_candle(4, high="1.10010", low="1.09900", close="1.09930"))
    event = det.detect(candles, [zone])
    assert event is not None
    assert event.swept_direction == "downside"


def test_trendline_liquidity_is_skipped() -> None:
    det = RuleBasedSweepDetector(_config())
    zone = _zone(
        kind=LiquidityZoneKind.TRENDLINE_LIQUIDITY,
        upper="1.10055", lower="1.10045",
    )
    candles = [_candle(i) for i in range(4)]
    candles.append(_candle(4, high="1.10100", low="1.09990", close="1.10000"))
    assert det.detect(candles, [zone]) is None


# ----------------------------------------------------------------------------
# Multi-zone disambiguation
# ----------------------------------------------------------------------------
def test_freshest_sweep_wins() -> None:
    det = RuleBasedSweepDetector(_config(sweep_lookback_candles=10))
    zone_a = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        upper="1.10055", lower="1.10045",
        zone_id="aaaaaaaaaaaaaaaa",
    )
    zone_b = _zone(
        kind=LiquidityZoneKind.EQUAL_LOWS,
        upper="1.09955", lower="1.09945",
        zone_id="bbbbbbbbbbbbbbbb",
    )
    candles = [_candle(i) for i in range(10)]
    # zone_a swept (upside) at idx 2 — older
    candles[2] = _candle(2, high="1.10100", low="1.09990", close="1.10000")
    # zone_b swept (downside) at idx 7 — newer
    candles[7] = _candle(7, high="1.10010", low="1.09900", close="1.10000")
    event = det.detect(candles, [zone_a, zone_b])
    assert event is not None
    assert event.swept_zone.zone_id == "bbbbbbbbbbbbbbbb"
    assert event.swept_direction == "downside"


def test_stronger_zone_wins_on_tie() -> None:
    """Same sweep candle for both zones → tie-break on strength."""
    det = RuleBasedSweepDetector(_config())
    zone_weak = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        upper="1.10055", lower="1.10045",
        strength=0.3,
        zone_id="11111111aaaaaaaa",
    )
    zone_strong = _zone(
        kind=LiquidityZoneKind.ROLLING_HIGH,
        upper="1.10065", lower="1.10055",
        strength=0.9,
        zone_id="22222222bbbbbbbb",
    )
    candles = [_candle(i) for i in range(5)]
    candles[4] = _candle(4, high="1.10100", low="1.09990", close="1.10080")
    event = det.detect(candles, [zone_weak, zone_strong])
    assert event is not None
    assert event.swept_zone.zone_id == "22222222bbbbbbbb"


# ----------------------------------------------------------------------------
# Mixed-symbol guards
# ----------------------------------------------------------------------------
def test_mixed_candle_symbols_rejected() -> None:
    det = RuleBasedSweepDetector(_config())
    candles = [_candle(0, symbol="EURUSD"), _candle(1, symbol="GBPUSD")]
    with pytest.raises(ValueError, match="mixed candle symbols"):
        det.detect(candles, [])


def test_zone_symbol_mismatch_rejected() -> None:
    det = RuleBasedSweepDetector(_config())
    candles = [_candle(i, symbol="EURUSD") for i in range(3)]
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        upper="1.10055", lower="1.10045",
        symbol="GBPUSD",
    )
    with pytest.raises(ValueError, match="zone symbol"):
        det.detect(candles, [zone])


# ----------------------------------------------------------------------------
# Event identity + traceability
# ----------------------------------------------------------------------------
def test_sweep_id_is_16_char_lowercase_hex() -> None:
    """CONVENTIONS.md §13 — ``SweepEvent.sweep_id`` is a 16-char
    lowercase hex string (sha256 prefix)."""
    det = RuleBasedSweepDetector(_config())
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS, upper="1.10055", lower="1.10045",
    )
    candles = [_candle(i) for i in range(4)]
    candles.append(_candle(4, high="1.10100", low="1.09990", close="1.10000"))
    event = det.detect(candles, [zone])
    assert event is not None
    assert len(event.sweep_id) == 16
    int(event.sweep_id, 16)
    assert event.sweep_id == event.sweep_id.lower()


def test_sweep_id_is_deterministic_across_calls() -> None:
    det = RuleBasedSweepDetector(_config())
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS, upper="1.10055", lower="1.10045",
    )
    candles = [_candle(i) for i in range(4)]
    candles.append(_candle(4, high="1.10100", low="1.09990", close="1.10000"))
    a = det.detect(candles, [zone])
    b = det.detect(candles, [zone])
    assert a is not None and b is not None
    assert a.sweep_id == b.sweep_id


def test_event_timestamp_is_sweep_candle_open_time() -> None:
    """Sweep timestamp = candle that did the sweeping, NOT the caller's
    current tick. PhaseClassifier needs this to compare ordering vs the
    latest inducement and decide PHASE_2_MITIGATION."""
    det = RuleBasedSweepDetector(_config(sweep_lookback_candles=5))
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS, upper="1.10055", lower="1.10045",
    )
    candles = [_candle(i) for i in range(5)]
    candles[2] = _candle(2, high="1.10100", low="1.09990", close="1.10080")
    # Candles at idx 3, 4 are benign — the sweep is at idx 2
    event = det.detect(candles, [zone])
    assert event is not None
    assert event.timestamp == candles[2].open_time


def test_swept_zone_carries_through() -> None:
    det = RuleBasedSweepDetector(_config())
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        upper="1.10055", lower="1.10045",
        zone_id="cafebabecafebabe",
    )
    candles = [_candle(i) for i in range(4)]
    candles.append(_candle(4, high="1.10100", low="1.09990", close="1.10000"))
    event = det.detect(candles, [zone])
    assert event is not None
    assert event.swept_zone is zone


def test_rejection_strength_is_zone_strength() -> None:
    det = RuleBasedSweepDetector(_config())
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        upper="1.10055", lower="1.10045",
        strength=0.42,
    )
    candles = [_candle(i) for i in range(4)]
    candles.append(_candle(4, high="1.10100", low="1.09990", close="1.10000"))
    event = det.detect(candles, [zone])
    assert event is not None
    assert event.rejection_strength == pytest.approx(0.42)


def test_symbol_carries_through() -> None:
    det = RuleBasedSweepDetector(_config())
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        upper="1.10055", lower="1.10045",
        symbol="XAUUSD",
    )
    candles = [_candle(i, symbol="XAUUSD") for i in range(4)]
    candles.append(_candle(
        4, symbol="XAUUSD", high="1.10100", low="1.09990", close="1.10000",
    ))
    event = det.detect(candles, [zone])
    assert event is not None
    assert event.symbol == "XAUUSD"


# ----------------------------------------------------------------------------
# Config validation
# ----------------------------------------------------------------------------
def test_config_pip_size_must_be_positive() -> None:
    with pytest.raises(ValueError, match="pip_size"):
        RuleBasedSweepDetector(_config(pip_size=Decimal("0")))


def test_config_sweep_lookback_must_be_positive() -> None:
    with pytest.raises(ValueError, match="sweep_lookback_candles"):
        RuleBasedSweepDetector(_config(sweep_lookback_candles=0))


def test_config_min_penetration_must_be_non_negative() -> None:
    with pytest.raises(ValueError, match="min_penetration_pips"):
        RuleBasedSweepDetector(_config(min_penetration_pips=Decimal("-1")))
