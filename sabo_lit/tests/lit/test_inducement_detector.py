"""
Tests for ``sabo_lit.lit.inducement_detector.InducementPatternDetector``.

Coverage shape mirrors ``test_liquidity_mapper.py``: for each
detection axis (sweep direction, return direction, freshness,
zone-kind routing, flow corroboration, multi-zone disambiguation),
at least one POSITIVE test and one NEGATIVE / near-miss test, plus
edge cases (empty inputs, mixed symbols, config validation).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from sabo_lit.core import (
    Candle,
    LiquidityZone,
    LiquidityZoneKind,
    OrderFlowSnapshot,
)
from sabo_lit.lit.inducement_detector import (
    InducementDetectorConfig,
    InducementPatternDetector,
)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
_T0 = datetime(2026, 5, 19, 8, 0, 0, tzinfo=timezone.utc)
_M5 = timedelta(minutes=5)


def _config(**overrides: Any) -> InducementDetectorConfig:
    base: dict[str, Any] = {
        "pip_size": Decimal("0.0001"),
        "sweep_lookback_candles": 5,
        "min_penetration_pips": Decimal("1.0"),
        "min_return_pips": Decimal("0.0"),
        "base_confidence": 0.5,
        "flow_confirmation_bonus": 0.3,
    }
    base.update(overrides)
    return InducementDetectorConfig(**base)


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


def _flow(*, delta: str, symbol: str = "EURUSD") -> OrderFlowSnapshot:
    return OrderFlowSnapshot(
        symbol=symbol,
        timestamp=_T0,
        bid_volume=Decimal("100"),
        ask_volume=Decimal("100"),
        delta=Decimal(delta),
        cumulative_delta=Decimal(delta),
        imbalance_ratio=0.0,
        spread=Decimal("0.00010"),
    )


# ----------------------------------------------------------------------------
# Empty / degenerate
# ----------------------------------------------------------------------------
def test_empty_candles_returns_none() -> None:
    det = InducementPatternDetector(_config())
    assert det.detect([], [_zone(kind=LiquidityZoneKind.EQUAL_HIGHS, upper="1.10050", lower="1.10040")], None) is None


def test_empty_zones_returns_none() -> None:
    det = InducementPatternDetector(_config())
    assert det.detect([_candle(i) for i in range(5)], [], None) is None


# ----------------------------------------------------------------------------
# High-side sweep + bearish inducement
# ----------------------------------------------------------------------------
def test_upside_sweep_with_rejection_emits_bearish_inducement() -> None:
    """Wick pierces an EQUAL_HIGHS zone; current close is back below.
    Inferred direction must be bearish."""
    det = InducementPatternDetector(_config())
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        upper="1.10055", lower="1.10045",
    )
    candles = [_candle(i) for i in range(4)]
    # Sweep candle: high pierces 1.10055 by 5 pips, close back at 1.10000
    candles.append(
        _candle(4, high="1.10100", low="1.09990", close="1.10000")
    )
    event = det.detect(candles, [zone], None)
    assert event is not None
    assert event.inferred_direction == "bearish"
    assert event.inducement_zone.zone_id == zone.zone_id
    assert event.confirmed is True


def test_upside_wick_without_close_back_emits_nothing() -> None:
    """Wick pierces the zone but the candle closes ABOVE the band — no
    rejection, no inducement."""
    det = InducementPatternDetector(_config(min_return_pips=Decimal("1.0")))
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        upper="1.10055", lower="1.10045",
    )
    candles = [_candle(i) for i in range(4)]
    candles.append(
        _candle(4, high="1.10100", low="1.09990", close="1.10080")
    )
    assert det.detect(candles, [zone], None) is None


def test_high_zone_not_pierced_emits_nothing() -> None:
    """Highest wick stays under the band — no sweep."""
    det = InducementPatternDetector(_config())
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        upper="1.10100", lower="1.10090",
    )
    candles = [_candle(i, high="1.10010", low="1.09990") for i in range(5)]
    assert det.detect(candles, [zone], None) is None


def test_penetration_below_threshold_emits_nothing() -> None:
    """Wick pierces, but by less than ``min_penetration_pips``."""
    det = InducementPatternDetector(_config(min_penetration_pips=Decimal("3.0")))
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        upper="1.10055", lower="1.10045",
    )
    candles = [_candle(i) for i in range(4)]
    # high = 1.10057 -> 2 pip penetration, threshold = 3 pips
    candles.append(_candle(4, high="1.10057", low="1.09990", close="1.10000"))
    assert det.detect(candles, [zone], None) is None


def test_sweep_outside_lookback_emits_nothing() -> None:
    """Sweep candle older than ``sweep_lookback_candles``."""
    det = InducementPatternDetector(_config(sweep_lookback_candles=3))
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        upper="1.10055", lower="1.10045",
    )
    candles = [_candle(i) for i in range(10)]
    candles[2] = _candle(2, high="1.10100", low="1.09990", close="1.10000")
    # last 3 candles are 7,8,9 — sweep at index 2 is out of window
    assert det.detect(candles, [zone], None) is None


def test_return_below_threshold_emits_nothing() -> None:
    """Sweep occurs, but the last close has not returned far enough."""
    det = InducementPatternDetector(_config(min_return_pips=Decimal("5.0")))
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        upper="1.10055", lower="1.10045",
    )
    candles = [_candle(i) for i in range(4)]
    # close at 1.10054 -> only 1 pip below price_upper; threshold = 5 pips
    candles.append(_candle(4, high="1.10100", low="1.09990", close="1.10054"))
    assert det.detect(candles, [zone], None) is None


# ----------------------------------------------------------------------------
# Low-side sweep + bullish inducement
# ----------------------------------------------------------------------------
def test_downside_sweep_with_rejection_emits_bullish_inducement() -> None:
    det = InducementPatternDetector(_config())
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_LOWS,
        upper="1.09955", lower="1.09945",
    )
    candles = [_candle(i, high="1.10010", low="1.09990") for i in range(4)]
    candles.append(
        _candle(4, high="1.10010", low="1.09900", close="1.10000")
    )
    event = det.detect(candles, [zone], None)
    assert event is not None
    assert event.inferred_direction == "bullish"
    assert event.inducement_zone.zone_id == zone.zone_id


def test_low_zone_not_pierced_emits_nothing() -> None:
    det = InducementPatternDetector(_config())
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_LOWS,
        upper="1.09800", lower="1.09790",
    )
    candles = [_candle(i, high="1.10010", low="1.09990") for i in range(5)]
    assert det.detect(candles, [zone], None) is None


# ----------------------------------------------------------------------------
# Zone-kind routing
# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kind", [
        LiquidityZoneKind.ROLLING_HIGH,
        LiquidityZoneKind.PREVIOUS_DAY_HIGH,
    ],
)
def test_other_high_kinds_also_emit_bearish(kind: LiquidityZoneKind) -> None:
    det = InducementPatternDetector(_config())
    zone = _zone(kind=kind, upper="1.10055", lower="1.10045")
    candles = [_candle(i) for i in range(4)]
    candles.append(_candle(4, high="1.10100", low="1.09990", close="1.10000"))
    event = det.detect(candles, [zone], None)
    assert event is not None
    assert event.inferred_direction == "bearish"


@pytest.mark.parametrize(
    "kind", [
        LiquidityZoneKind.ROLLING_LOW,
        LiquidityZoneKind.PREVIOUS_DAY_LOW,
    ],
)
def test_other_low_kinds_also_emit_bullish(kind: LiquidityZoneKind) -> None:
    det = InducementPatternDetector(_config())
    zone = _zone(kind=kind, upper="1.09955", lower="1.09945")
    candles = [_candle(i) for i in range(4)]
    candles.append(_candle(4, high="1.10010", low="1.09900", close="1.10000"))
    event = det.detect(candles, [zone], None)
    assert event is not None
    assert event.inferred_direction == "bullish"


def test_trendline_liquidity_is_skipped() -> None:
    """Phase 1 omits TRENDLINE_LIQUIDITY — even a textbook sweep on
    such a zone returns nothing."""
    det = InducementPatternDetector(_config())
    zone = _zone(
        kind=LiquidityZoneKind.TRENDLINE_LIQUIDITY,
        upper="1.10055", lower="1.10045",
    )
    candles = [_candle(i) for i in range(4)]
    candles.append(_candle(4, high="1.10100", low="1.09990", close="1.10000"))
    assert det.detect(candles, [zone], None) is None


# ----------------------------------------------------------------------------
# Multi-zone disambiguation
# ----------------------------------------------------------------------------
def test_freshest_sweep_wins_among_multiple_candidates() -> None:
    """Two qualifying zones; the one whose sweep candle is most recent
    is the one returned. Zones are placed on opposite sides so each is
    uniquely pierced by ONE candle in the window."""
    det = InducementPatternDetector(_config(sweep_lookback_candles=10))
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
    # Sweep zone_a (upside) at index 2 — high pierces 1.10055; close back.
    candles[2] = _candle(2, high="1.10100", low="1.09990", close="1.10000")
    # Sweep zone_b (downside) at index 7 — low pierces 1.09945; close back.
    candles[7] = _candle(7, high="1.10010", low="1.09900", close="1.10000")
    # Last candle's close (1.10000) is below zone_a.price_upper AND above
    # zone_b.price_lower → both rejection checks pass.
    event = det.detect(candles, [zone_a, zone_b], None)
    assert event is not None
    assert event.inducement_zone.zone_id == "bbbbbbbbbbbbbbbb"
    assert event.inferred_direction == "bullish"


def test_stronger_zone_wins_when_sweeps_tie() -> None:
    """Two qualifying zones with same-candle sweeps: stronger zone wins."""
    det = InducementPatternDetector(_config())
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
    candles[4] = _candle(4, high="1.10100", low="1.09990", close="1.10000")
    event = det.detect(candles, [zone_weak, zone_strong], None)
    assert event is not None
    assert event.inducement_zone.zone_id == "22222222bbbbbbbb"


# ----------------------------------------------------------------------------
# Confidence + flow corroboration
# ----------------------------------------------------------------------------
def test_confidence_defaults_to_base_without_flow() -> None:
    det = InducementPatternDetector(_config(base_confidence=0.4))
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        upper="1.10055", lower="1.10045",
    )
    candles = [_candle(i) for i in range(4)]
    candles.append(_candle(4, high="1.10100", low="1.09990", close="1.10000"))
    event = det.detect(candles, [zone], None)
    assert event is not None
    assert event.confidence == 0.4


def test_agreeing_flow_adds_confirmation_bonus() -> None:
    det = InducementPatternDetector(
        _config(base_confidence=0.4, flow_confirmation_bonus=0.3)
    )
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        upper="1.10055", lower="1.10045",
    )
    candles = [_candle(i) for i in range(4)]
    candles.append(_candle(4, high="1.10100", low="1.09990", close="1.10000"))
    # Bearish inducement -> negative delta agrees
    event = det.detect(candles, [zone], _flow(delta="-50"))
    assert event is not None
    assert event.confidence == pytest.approx(0.7)


def test_disagreeing_flow_keeps_base_confidence() -> None:
    det = InducementPatternDetector(
        _config(base_confidence=0.4, flow_confirmation_bonus=0.3)
    )
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        upper="1.10055", lower="1.10045",
    )
    candles = [_candle(i) for i in range(4)]
    candles.append(_candle(4, high="1.10100", low="1.09990", close="1.10000"))
    # Bearish inducement -> positive delta disagrees
    event = det.detect(candles, [zone], _flow(delta="+50"))
    assert event is not None
    assert event.confidence == 0.4


def test_confidence_capped_at_one_with_large_bonus() -> None:
    det = InducementPatternDetector(
        _config(base_confidence=0.9, flow_confirmation_bonus=0.5)
    )
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        upper="1.10055", lower="1.10045",
    )
    candles = [_candle(i) for i in range(4)]
    candles.append(_candle(4, high="1.10100", low="1.09990", close="1.10000"))
    event = det.detect(candles, [zone], _flow(delta="-50"))
    assert event is not None
    assert event.confidence == 1.0


# ----------------------------------------------------------------------------
# Mixed-symbol guards
# ----------------------------------------------------------------------------
def test_mixed_candle_symbols_rejected() -> None:
    det = InducementPatternDetector(_config())
    candles = [_candle(0, symbol="EURUSD"), _candle(1, symbol="GBPUSD")]
    with pytest.raises(ValueError, match="mixed candle symbols"):
        det.detect(candles, [], None)


def test_zone_symbol_mismatch_rejected() -> None:
    det = InducementPatternDetector(_config())
    candles = [_candle(i, symbol="EURUSD") for i in range(3)]
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        upper="1.10055", lower="1.10045",
        symbol="GBPUSD",
    )
    with pytest.raises(ValueError, match="zone symbol"):
        det.detect(candles, [zone], None)


def test_flow_symbol_mismatch_rejected() -> None:
    det = InducementPatternDetector(_config())
    candles = [_candle(i) for i in range(3)]
    with pytest.raises(ValueError, match="flow symbol"):
        det.detect(candles, [], _flow(delta="0", symbol="GBPUSD"))


# ----------------------------------------------------------------------------
# Event identity + traceability
# ----------------------------------------------------------------------------
def test_event_id_is_16_char_lowercase_hex() -> None:
    """CONVENTIONS.md §13 — ``InducementEvent.event_id`` is a 16-char
    lowercase hex string (sha256 prefix), deterministic in
    ``(kind, inducement_zone.zone_id, timestamp)``."""
    det = InducementPatternDetector(_config())
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        upper="1.10055", lower="1.10045",
    )
    candles = [_candle(i) for i in range(4)]
    candles.append(_candle(4, high="1.10100", low="1.09990", close="1.10000"))
    event = det.detect(candles, [zone], None)
    assert event is not None
    assert len(event.event_id) == 16
    int(event.event_id, 16)
    assert event.event_id == event.event_id.lower()


def test_event_id_is_deterministic_across_calls() -> None:
    """Same ``(zone, timestamp)`` produces the same ``event_id`` — the
    whole point of Phase 0.4: dedup by ``event_id`` works directly."""
    det = InducementPatternDetector(_config())
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        upper="1.10055", lower="1.10045",
    )
    candles = [_candle(i) for i in range(4)]
    candles.append(_candle(4, high="1.10100", low="1.09990", close="1.10000"))
    a = det.detect(candles, [zone], None)
    b = det.detect(candles, [zone], None)
    assert a is not None and b is not None
    assert a.event_id == b.event_id


def test_event_timestamp_is_last_candle_open_time() -> None:
    det = InducementPatternDetector(_config())
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        upper="1.10055", lower="1.10045",
    )
    candles = [_candle(i) for i in range(4)]
    candles.append(_candle(4, high="1.10100", low="1.09990", close="1.10000"))
    event = det.detect(candles, [zone], None)
    assert event is not None
    assert event.timestamp == candles[-1].open_time


def test_consecutive_calls_carry_same_zone_id() -> None:
    """The mapper's zone_id is deterministic; the detector must surface
    that id verbatim so callers can dedupe by it."""
    det = InducementPatternDetector(_config())
    zone = _zone(
        kind=LiquidityZoneKind.EQUAL_HIGHS,
        upper="1.10055", lower="1.10045",
        zone_id="cafebabecafebabe",
    )
    candles = [_candle(i) for i in range(4)]
    candles.append(_candle(4, high="1.10100", low="1.09990", close="1.10000"))
    a = det.detect(candles, [zone], None)
    b = det.detect(candles, [zone], None)
    assert a is not None and b is not None
    assert a.inducement_zone.zone_id == b.inducement_zone.zone_id == "cafebabecafebabe"


# ----------------------------------------------------------------------------
# Config validation
# ----------------------------------------------------------------------------
def test_config_pip_size_must_be_positive() -> None:
    with pytest.raises(ValueError, match="pip_size"):
        InducementPatternDetector(_config(pip_size=Decimal("0")))


def test_config_sweep_lookback_must_be_positive() -> None:
    with pytest.raises(ValueError, match="sweep_lookback_candles"):
        InducementPatternDetector(_config(sweep_lookback_candles=0))


def test_config_min_penetration_must_be_non_negative() -> None:
    with pytest.raises(ValueError, match="min_penetration_pips"):
        InducementPatternDetector(_config(min_penetration_pips=Decimal("-1")))


def test_config_min_return_must_be_non_negative() -> None:
    with pytest.raises(ValueError, match="min_return_pips"):
        InducementPatternDetector(_config(min_return_pips=Decimal("-1")))


def test_config_base_confidence_in_unit_interval() -> None:
    with pytest.raises(ValueError, match="base_confidence"):
        InducementPatternDetector(_config(base_confidence=1.5))


def test_config_flow_bonus_in_unit_interval() -> None:
    with pytest.raises(ValueError, match="flow_confirmation_bonus"):
        InducementPatternDetector(_config(flow_confirmation_bonus=2.0))
