"""
Tests for ``sabo_lit.lit.structure_validator.RuleBasedStructureValidator``.

Coverage shape mirrors the earlier LIT tests: each validation axis
(phase gate, anchor, post-anchor freshness, reference pivot, BOS /
CHoCH break) has at least one POSITIVE and one NEGATIVE case, plus
edge cases (empty / mixed inputs, symbol guards, config validation,
traceability fields).

``ValidatedStructureState`` and ``InducementEvent`` are both
construction-restricted to ``sabo_lit.lit`` by ``CONSTRUCTION_RULES``,
so test fixtures mint ``InducementEvent`` instances through the real
``InducementPatternDetector`` (same path the production pipeline
uses) and read ``ValidatedStructureState`` from the validator's
output rather than constructing it directly. ``MarketPhase`` has no
construction rule and is built inline.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

import pytest

from sabo_lit.core import (
    Candle,
    InducementEvent,
    LiquidityZone,
    LiquidityZoneKind,
    MarketPhase,
    PhaseKind,
)
from sabo_lit.lit.inducement_detector import (
    InducementDetectorConfig,
    InducementPatternDetector,
)
from sabo_lit.lit.structure_validator import (
    RuleBasedStructureValidator,
    StructureValidatorConfig,
)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
_T0 = datetime(2026, 5, 19, 8, 0, 0, tzinfo=timezone.utc)
_M5 = timedelta(minutes=5)


def _config(**overrides: Any) -> StructureValidatorConfig:
    base: dict[str, Any] = {
        "pip_size": Decimal("0.0001"),
        "swing_lookback": 2,
        "pivot_search_window": 20,
        "min_post_inducement_candles": 1,
        "min_break_distance_pips": Decimal("0.0"),
    }
    base.update(overrides)
    return StructureValidatorConfig(**base)


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


def _phase(
    *,
    kind: PhaseKind = PhaseKind.PHASE_1_INDUCEMENT,
    symbol: str = "EURUSD",
    confidence: float = 0.7,
    timestamp: datetime | None = None,
) -> MarketPhase:
    return MarketPhase(
        symbol=symbol,
        timestamp=timestamp if timestamp is not None else _T0,
        phase=kind,
        confidence=confidence,
    )


def _make_inducement(
    *,
    timestamp: datetime,
    symbol: str = "EURUSD",
    direction: Literal["bullish", "bearish"] = "bearish",
    confidence: float = 0.7,
) -> InducementEvent:
    """Mint a real ``InducementEvent`` at ``timestamp`` via the production
    detector. Direct construction is forbidden by ``CONSTRUCTION_RULES``.
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
    if direction == "bearish":
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
            symbol=symbol, timeframe="M5", open_time=timestamp - _M5,
            open=Decimal("1.10000"), high=Decimal("1.10100"),
            low=Decimal("1.09990"), close=Decimal("1.10000"),
            volume=Decimal("100"),
        )
        rejection_candle = Candle(
            symbol=symbol, timeframe="M5", open_time=timestamp,
            open=Decimal("1.10000"), high=Decimal("1.10010"),
            low=Decimal("1.09990"), close=Decimal("1.10000"),
            volume=Decimal("100"),
        )
    else:
        zone = LiquidityZone(
            zone_id="cafebabecafebabe",
            symbol=symbol,
            kind=LiquidityZoneKind.EQUAL_LOWS,
            price_upper=Decimal("1.09955"),
            price_lower=Decimal("1.09945"),
            created_at=timestamp,
            strength_score=0.6,
        )
        sweep_candle = Candle(
            symbol=symbol, timeframe="M5", open_time=timestamp - _M5,
            open=Decimal("1.10000"), high=Decimal("1.10010"),
            low=Decimal("1.09900"), close=Decimal("1.10000"),
            volume=Decimal("100"),
        )
        rejection_candle = Candle(
            symbol=symbol, timeframe="M5", open_time=timestamp,
            open=Decimal("1.10000"), high=Decimal("1.10010"),
            low=Decimal("1.09990"), close=Decimal("1.10000"),
            volume=Decimal("100"),
        )
    event = det.detect([sweep_candle, rejection_candle], [zone], None)
    assert event is not None
    assert event.inferred_direction == direction
    return event


# ----------------------------------------------------------------------------
# Scenario builders
# ----------------------------------------------------------------------------
def _bearish_BOS_candles() -> tuple[list[Candle], InducementEvent]:
    """Build a candle series with: (a) a clean swing LOW before the
    inducement anchor, (b) the inducement anchor itself, (c) at least
    one post-anchor candle whose close pierces that swing low.

    Layout (open_time indices 0..14, M5 grid):
      idx 0..4    : drifting highs/lows around 1.10000
      idx 5       : swing LOW at 1.09950 (strict-less than its 2 neighbours)
      idx 6..9    : recovery, lows ~1.09980+
      idx 10      : inducement anchor (sweep+return)
      idx 11..14  : breakdown — close < 1.09950 to confirm BOS
    """
    candles: list[Candle] = []
    for i in range(5):
        candles.append(_candle(i, high="1.10010", low="1.09990"))
    candles.append(_candle(5, high="1.09995", low="1.09950"))  # swing low
    for i in range(6, 10):
        candles.append(_candle(i, high="1.10010", low="1.09985"))
    # inducement anchor at idx 10 — value doesn't matter for the
    # validator, only the timestamp does
    candles.append(_candle(10, high="1.10010", low="1.09980"))
    # post-anchor breakdown
    candles.append(_candle(11, high="1.10000", low="1.09930", close="1.09940"))
    for i in range(12, 15):
        candles.append(_candle(i, high="1.09960", low="1.09930", close="1.09935"))
    inducement = _make_inducement(
        timestamp=_T0 + 10 * _M5, direction="bearish",
    )
    return candles, inducement


def _bullish_BOS_candles() -> tuple[list[Candle], InducementEvent]:
    candles: list[Candle] = []
    for i in range(5):
        candles.append(_candle(i, high="1.10010", low="1.09990"))
    candles.append(_candle(5, high="1.10050", low="1.10010"))  # swing high
    for i in range(6, 10):
        candles.append(_candle(i, high="1.10015", low="1.09990"))
    candles.append(_candle(10, high="1.10020", low="1.09990"))
    candles.append(_candle(11, high="1.10070", low="1.10000", close="1.10060"))
    for i in range(12, 15):
        candles.append(_candle(i, high="1.10080", low="1.10040", close="1.10065"))
    inducement = _make_inducement(
        timestamp=_T0 + 10 * _M5, direction="bullish",
    )
    return candles, inducement


# ----------------------------------------------------------------------------
# Positive cases — gate passes
# ----------------------------------------------------------------------------
def test_bearish_BOS_emits_validated_state() -> None:
    val = RuleBasedStructureValidator(_config())
    candles, induc = _bearish_BOS_candles()
    out = val.validate(induc, _phase(), candles)
    assert out is not None
    assert out.gate_passed is True
    assert out.inferred_direction == "bearish"
    assert out.inducement_event_id == induc.event_id
    assert out.sweep_event_id is None


def test_bullish_BOS_emits_validated_state() -> None:
    val = RuleBasedStructureValidator(_config())
    candles, induc = _bullish_BOS_candles()
    out = val.validate(induc, _phase(), candles)
    assert out is not None
    assert out.gate_passed is True
    assert out.inferred_direction == "bullish"
    assert out.inducement_event_id == induc.event_id


def test_phase_2_mitigation_also_passes_when_break_confirmed() -> None:
    """PHASE_2_MITIGATION is equally acceptable to the phase gate."""
    val = RuleBasedStructureValidator(_config())
    candles, induc = _bearish_BOS_candles()
    out = val.validate(
        induc, _phase(kind=PhaseKind.PHASE_2_MITIGATION), candles,
    )
    assert out is not None
    assert out.phase.phase == PhaseKind.PHASE_2_MITIGATION


# ----------------------------------------------------------------------------
# Phase gate — UNDEFINED
# ----------------------------------------------------------------------------
def test_phase_undefined_rejected() -> None:
    val = RuleBasedStructureValidator(_config())
    candles, induc = _bearish_BOS_candles()
    out = val.validate(induc, _phase(kind=PhaseKind.UNDEFINED), candles)
    assert out is None


# ----------------------------------------------------------------------------
# Anchor failures
# ----------------------------------------------------------------------------
def test_inducement_predates_all_candles_returns_none() -> None:
    val = RuleBasedStructureValidator(_config())
    candles, _ = _bearish_BOS_candles()
    induc = _make_inducement(
        timestamp=_T0 - 50 * _M5, direction="bearish",
    )
    assert val.validate(induc, _phase(), candles) is None


def test_inducement_at_last_candle_leaves_no_post_window() -> None:
    """Anchor == last candle → zero post-anchor candles → reject."""
    val = RuleBasedStructureValidator(_config(min_post_inducement_candles=1))
    candles, _ = _bearish_BOS_candles()
    induc = _make_inducement(
        timestamp=candles[-1].open_time, direction="bearish",
    )
    assert val.validate(induc, _phase(), candles) is None


# ----------------------------------------------------------------------------
# Post-anchor freshness
# ----------------------------------------------------------------------------
def test_post_anchor_too_short_returns_none() -> None:
    val = RuleBasedStructureValidator(_config(min_post_inducement_candles=10))
    candles, induc = _bearish_BOS_candles()
    # post candles = candles[11:] = 4 candles < 10
    assert val.validate(induc, _phase(), candles) is None


# ----------------------------------------------------------------------------
# Pivot failures
# ----------------------------------------------------------------------------
def test_no_reference_pivot_returns_none() -> None:
    """Flat candles before the anchor → no strict swing → reject."""
    val = RuleBasedStructureValidator(_config())
    flat = [_candle(i, high="1.10010", low="1.09990") for i in range(10)]
    flat.append(_candle(10, high="1.10010", low="1.09990"))
    # post-anchor: still no break possible since nothing to break
    flat.extend(
        _candle(i, high="1.10010", low="1.09990") for i in range(11, 15)
    )
    induc = _make_inducement(
        timestamp=_T0 + 10 * _M5, direction="bearish",
    )
    assert val.validate(induc, _phase(), flat) is None


def test_pivot_outside_search_window_returns_none() -> None:
    """Pivot exists but is older than ``pivot_search_window``."""
    val = RuleBasedStructureValidator(
        _config(pivot_search_window=3, min_post_inducement_candles=1),
    )
    candles, induc = _bearish_BOS_candles()
    # The single swing low is at idx 5; anchor is idx 10; distance 5 > 3
    assert val.validate(induc, _phase(), candles) is None


# ----------------------------------------------------------------------------
# Break-of-structure failures
# ----------------------------------------------------------------------------
def test_no_break_after_anchor_returns_none() -> None:
    """Pivot present, post-anchor candles all closing ABOVE pivot low."""
    val = RuleBasedStructureValidator(_config())
    candles, induc = _bearish_BOS_candles()
    # Overwrite post-anchor candles so close never breaches 1.09950
    candles = candles[:11] + [
        _candle(i, high="1.10010", low="1.09970", close="1.09980")
        for i in range(11, 15)
    ]
    assert val.validate(induc, _phase(), candles) is None


def test_break_below_distance_threshold_returns_none() -> None:
    """Close pierces the pivot low but by less than the threshold."""
    val = RuleBasedStructureValidator(
        _config(min_break_distance_pips=Decimal("10.0")),
    )
    candles, induc = _bearish_BOS_candles()
    # Pivot low = 1.09950; threshold = 10 pips = 0.0010; target = 1.09850.
    # The BOS candles in the fixture close at 1.09940 / 1.09935 — only
    # ~1 pip past the pivot, well above the target.
    assert val.validate(induc, _phase(), candles) is None


def test_break_only_before_anchor_returns_none() -> None:
    """Suppose price had already broken below the swing low BEFORE the
    inducement anchor and then recovered: that pre-anchor break must
    NOT count — only post-anchor closes are eligible."""
    val = RuleBasedStructureValidator(_config())
    candles, induc = _bearish_BOS_candles()
    # Inject a pre-anchor breakdown candle at idx 8 (between swing low
    # and inducement), then keep post-anchor candles benign.
    candles[8] = _candle(8, high="1.10000", low="1.09900", close="1.09905")
    candles = candles[:11] + [
        _candle(i, high="1.10010", low="1.09970", close="1.09980")
        for i in range(11, 15)
    ]
    assert val.validate(induc, _phase(), candles) is None


# ----------------------------------------------------------------------------
# Output details
# ----------------------------------------------------------------------------
def test_validated_state_timestamp_is_last_candle_open_time() -> None:
    val = RuleBasedStructureValidator(_config())
    candles, induc = _bearish_BOS_candles()
    out = val.validate(induc, _phase(), candles)
    assert out is not None
    assert out.timestamp == candles[-1].open_time


def test_validated_state_carries_input_phase() -> None:
    val = RuleBasedStructureValidator(_config())
    candles, induc = _bearish_BOS_candles()
    phase = _phase(kind=PhaseKind.PHASE_2_MITIGATION, confidence=0.42)
    out = val.validate(induc, phase, candles)
    assert out is not None
    assert out.phase is phase
    assert out.phase.confidence == 0.42


def test_validated_state_symbol_matches_candles() -> None:
    val = RuleBasedStructureValidator(_config())
    base_candles, _ = _bearish_BOS_candles()
    candles = [
        Candle(
            symbol="XAUUSD",
            timeframe=c.timeframe,
            open_time=c.open_time,
            open=c.open, high=c.high, low=c.low, close=c.close,
            volume=c.volume,
        )
        for c in base_candles
    ]
    induc = _make_inducement(
        timestamp=_T0 + 10 * _M5, symbol="XAUUSD", direction="bearish",
    )
    phase = _phase(symbol="XAUUSD")
    out = val.validate(induc, phase, candles)
    assert out is not None
    assert out.symbol == "XAUUSD"


# ----------------------------------------------------------------------------
# Symbol guards
# ----------------------------------------------------------------------------
def test_empty_candles_raises() -> None:
    val = RuleBasedStructureValidator(_config())
    induc = _make_inducement(timestamp=_T0, direction="bearish")
    with pytest.raises(ValueError, match="candles must be non-empty"):
        val.validate(induc, _phase(), [])


def test_mixed_candle_symbols_rejected() -> None:
    val = RuleBasedStructureValidator(_config())
    candles = [_candle(0, symbol="EURUSD"), _candle(1, symbol="GBPUSD")]
    induc = _make_inducement(timestamp=_T0, direction="bearish")
    with pytest.raises(ValueError, match="mixed candle symbols"):
        val.validate(induc, _phase(), candles)


def test_inducement_symbol_mismatch_rejected() -> None:
    val = RuleBasedStructureValidator(_config())
    candles, _ = _bearish_BOS_candles()
    induc = _make_inducement(
        timestamp=_T0 + 10 * _M5, symbol="GBPUSD", direction="bearish",
    )
    with pytest.raises(ValueError, match="inducement symbol"):
        val.validate(induc, _phase(), candles)


def test_phase_symbol_mismatch_rejected() -> None:
    val = RuleBasedStructureValidator(_config())
    candles, induc = _bearish_BOS_candles()
    with pytest.raises(ValueError, match="phase symbol"):
        val.validate(induc, _phase(symbol="GBPUSD"), candles)


# ----------------------------------------------------------------------------
# Config validation
# ----------------------------------------------------------------------------
def test_config_pip_size_must_be_positive() -> None:
    with pytest.raises(ValueError, match="pip_size"):
        RuleBasedStructureValidator(_config(pip_size=Decimal("0")))


def test_config_swing_lookback_must_be_positive() -> None:
    with pytest.raises(ValueError, match="swing_lookback"):
        RuleBasedStructureValidator(_config(swing_lookback=0))


def test_config_pivot_search_window_must_be_positive() -> None:
    with pytest.raises(ValueError, match="pivot_search_window"):
        RuleBasedStructureValidator(_config(pivot_search_window=0))


def test_config_min_post_inducement_candles_must_be_positive() -> None:
    with pytest.raises(ValueError, match="min_post_inducement_candles"):
        RuleBasedStructureValidator(_config(min_post_inducement_candles=0))


def test_config_min_break_distance_must_be_non_negative() -> None:
    with pytest.raises(ValueError, match="min_break_distance_pips"):
        RuleBasedStructureValidator(
            _config(min_break_distance_pips=Decimal("-1")),
        )
