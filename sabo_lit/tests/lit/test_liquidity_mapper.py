"""
Tests for ``sabo_lit.lit.liquidity_mapper.LiquidityMapper``.

Coverage rationale: for each zone kind, at least one PRESENCE test (the
pattern is there, the zone is emitted), one ABSENCE test (the pattern is
near-miss or unrelated, no zone), plus EDGE cases (empty input, too few
candles, mixed symbols, strict-greater swing pivots, config validation).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from sabo_lit.core import Candle, LiquidityZoneKind
from sabo_lit.lit.liquidity_mapper import LiquidityMapper, LiquidityMapperConfig


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
_T0 = datetime(2026, 5, 19, 8, 0, 0, tzinfo=timezone.utc)
_M5 = timedelta(minutes=5)


def _config(**overrides: Any) -> LiquidityMapperConfig:
    """Default config that's permissive enough to make swing detection
    reproducible in small fixtures."""
    base: dict[str, Any] = {
        "pip_size": Decimal("0.0001"),
        "equal_level_tolerance_pips": Decimal("1.0"),
        "min_equal_touches": 2,
        "max_touches_for_full_strength": 5,
        "swing_lookback": 2,
        "rolling_lookback_candles": 20,
        "previous_day_lookback_candles": 300,
        "rolling_zone_strength": 0.5,
        "previous_day_zone_strength": 0.6,
        "min_zone_strength": 0.0,
    }
    base.update(overrides)
    return LiquidityMapperConfig(**base)


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


# ----------------------------------------------------------------------------
# Empty / degenerate
# ----------------------------------------------------------------------------
def test_empty_candles_returns_empty() -> None:
    assert LiquidityMapper(_config()).map_zones([]) == []


def test_too_few_candles_for_swings_still_returns_rolling_extremes() -> None:
    """``swing_lookback=2`` needs at least 5 candles to find any swing.
    Three candles still produce rolling-high/low (single-window max/min),
    which is the documented Phase 1 behaviour."""
    mapper = LiquidityMapper(_config(swing_lookback=2))
    candles = [_candle(i) for i in range(3)]
    zones = mapper.map_zones(candles)
    kinds = {z.kind for z in zones}
    assert LiquidityZoneKind.EQUAL_HIGHS not in kinds
    assert LiquidityZoneKind.EQUAL_LOWS not in kinds
    assert LiquidityZoneKind.ROLLING_HIGH in kinds
    assert LiquidityZoneKind.ROLLING_LOW in kinds


def test_mixed_symbols_rejected() -> None:
    mapper = LiquidityMapper(_config())
    with pytest.raises(ValueError, match="mixed symbols"):
        mapper.map_zones([
            _candle(0, symbol="EURUSD"),
            _candle(1, symbol="GBPUSD"),
        ])


# ----------------------------------------------------------------------------
# Equal highs — presence
# ----------------------------------------------------------------------------
def test_two_equal_highs_at_same_price_form_zone() -> None:
    """Two swing highs at exactly the same price -> EQUAL_HIGHS zone whose
    band covers that price."""
    mapper = LiquidityMapper(_config(swing_lookback=2, min_equal_touches=2))
    candles = [_candle(i) for i in range(15)]
    # Swap in two true swing highs at 1.10050
    candles[5] = _candle(5, high="1.10050", low="1.10000")
    candles[11] = _candle(11, high="1.10050", low="1.10000")
    zones = mapper.map_zones(candles)
    eq_highs = [z for z in zones if z.kind == LiquidityZoneKind.EQUAL_HIGHS]
    assert len(eq_highs) == 1
    z = eq_highs[0]
    assert z.price_lower <= Decimal("1.10050") <= z.price_upper


def test_equal_highs_within_tolerance_cluster_together() -> None:
    """Two highs within ``equal_level_tolerance_pips`` form one cluster."""
    mapper = LiquidityMapper(
        _config(
            swing_lookback=2,
            equal_level_tolerance_pips=Decimal("2.0"),
            min_equal_touches=2,
        )
    )
    candles = [_candle(i) for i in range(15)]
    candles[5] = _candle(5, high="1.10050", low="1.10000")
    candles[11] = _candle(11, high="1.10051", low="1.10000")  # 1 pip apart
    zones = mapper.map_zones(candles)
    eq_highs = [z for z in zones if z.kind == LiquidityZoneKind.EQUAL_HIGHS]
    assert len(eq_highs) == 1


# ----------------------------------------------------------------------------
# Equal highs — absence
# ----------------------------------------------------------------------------
def test_unequal_highs_do_not_cluster() -> None:
    """Highs separated by more than tolerance must NOT cluster — proves the
    tolerance comparison actually fires."""
    mapper = LiquidityMapper(_config(swing_lookback=2, min_equal_touches=2))
    candles = [_candle(i) for i in range(15)]
    candles[5] = _candle(5, high="1.10050", low="1.10000")
    candles[11] = _candle(11, high="1.10070", low="1.10000")  # 20 pips, tol=1
    zones = mapper.map_zones(candles)
    eq_highs = [z for z in zones if z.kind == LiquidityZoneKind.EQUAL_HIGHS]
    assert eq_highs == []


def test_single_swing_does_not_form_equal_zone() -> None:
    """One swing alone is not a 'cluster' — needs >= min_equal_touches."""
    mapper = LiquidityMapper(_config(swing_lookback=2, min_equal_touches=2))
    candles = [_candle(i) for i in range(15)]
    candles[7] = _candle(7, high="1.10050", low="1.10000")
    zones = mapper.map_zones(candles)
    eq_highs = [z for z in zones if z.kind == LiquidityZoneKind.EQUAL_HIGHS]
    assert eq_highs == []


def test_flat_section_is_not_a_swing() -> None:
    """A run of identical highs/lows is not a swing — strict greater /
    strict less is required, so no equal-zones are emitted from a flat
    series.

    Documents the "strict greater than" invariant in
    ``_swing_highs`` / ``_swing_lows``.
    """
    mapper = LiquidityMapper(_config(swing_lookback=2))
    candles = [_candle(i, high="1.10010", low="1.09990") for i in range(15)]
    zones = mapper.map_zones(candles)
    swing_zones = [
        z for z in zones
        if z.kind in (LiquidityZoneKind.EQUAL_HIGHS, LiquidityZoneKind.EQUAL_LOWS)
    ]
    assert swing_zones == []


# ----------------------------------------------------------------------------
# Equal lows — symmetric presence
# ----------------------------------------------------------------------------
def test_two_equal_lows_at_same_price_form_zone() -> None:
    mapper = LiquidityMapper(_config(swing_lookback=2, min_equal_touches=2))
    candles = [_candle(i, high="1.10010", low="1.10005") for i in range(15)]
    for idx in (5, 11):
        candles[idx] = _candle(idx, high="1.10000", low="1.09950")
    zones = mapper.map_zones(candles)
    eq_lows = [z for z in zones if z.kind == LiquidityZoneKind.EQUAL_LOWS]
    assert len(eq_lows) == 1
    z = eq_lows[0]
    assert z.price_lower <= Decimal("1.09950") <= z.price_upper


# ----------------------------------------------------------------------------
# Strength scaling
# ----------------------------------------------------------------------------
def test_three_equal_highs_have_higher_strength_than_two() -> None:
    """``strength_score = min(1.0, touches / max_touches_for_full_strength)``
    — three touches must score strictly higher than two."""
    cfg = _config(
        swing_lookback=2,
        min_equal_touches=2,
        max_touches_for_full_strength=5,
    )
    mapper = LiquidityMapper(cfg)

    two_touches = [_candle(i) for i in range(15)]
    for idx in (5, 11):
        two_touches[idx] = _candle(idx, high="1.10050", low="1.10000")
    z2 = [
        z for z in mapper.map_zones(two_touches)
        if z.kind == LiquidityZoneKind.EQUAL_HIGHS
    ][0]

    three_touches = [_candle(i) for i in range(21)]
    for idx in (5, 11, 17):
        three_touches[idx] = _candle(idx, high="1.10050", low="1.10000")
    z3 = [
        z for z in mapper.map_zones(three_touches)
        if z.kind == LiquidityZoneKind.EQUAL_HIGHS
    ][0]

    assert z3.strength_score > z2.strength_score


# ----------------------------------------------------------------------------
# Rolling extremes
# ----------------------------------------------------------------------------
def test_rolling_high_is_highest_in_lookback_window() -> None:
    """A high spike OUTSIDE the lookback window must not become the
    ROLLING_HIGH — proves the window slicing is what's checked."""
    mapper = LiquidityMapper(
        _config(rolling_lookback_candles=10, swing_lookback=2)
    )
    candles = [_candle(i) for i in range(15)]
    candles[1] = _candle(1, high="1.20000", low="1.09990")   # outside last 10
    candles[12] = _candle(12, high="1.10100", low="1.09990")  # inside last 10
    zones = mapper.map_zones(candles)
    rolling_highs = [z for z in zones if z.kind == LiquidityZoneKind.ROLLING_HIGH]
    assert len(rolling_highs) == 1
    rh = rolling_highs[0]
    assert rh.price_lower <= Decimal("1.10100") <= rh.price_upper
    # And the 1.20000 spike is NOT inside this zone.
    assert not (rh.price_lower <= Decimal("1.20000") <= rh.price_upper)


def test_rolling_high_and_low_both_emitted() -> None:
    mapper = LiquidityMapper(_config())
    zones = mapper.map_zones([_candle(i) for i in range(10)])
    kinds = {z.kind for z in zones}
    assert LiquidityZoneKind.ROLLING_HIGH in kinds
    assert LiquidityZoneKind.ROLLING_LOW in kinds


# ----------------------------------------------------------------------------
# Previous-day extremes
# ----------------------------------------------------------------------------
def test_previous_day_extremes_emitted_when_two_days_present() -> None:
    """PDH/PDL come from the day BEFORE the latest day in the window."""
    mapper = LiquidityMapper(_config())
    day1 = datetime(2026, 5, 18, 10, 0, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 5, 19, 10, 0, 0, tzinfo=timezone.utc)
    candles: list[Candle] = []
    for i in range(5):
        candles.append(
            _candle(timestamp=day1 + i * _M5, high="1.10050", low="1.09950")
        )
    for i in range(5):
        candles.append(
            _candle(timestamp=day2 + i * _M5, high="1.10020", low="1.09980")
        )
    zones = mapper.map_zones(candles)
    pdh = [z for z in zones if z.kind == LiquidityZoneKind.PREVIOUS_DAY_HIGH]
    pdl = [z for z in zones if z.kind == LiquidityZoneKind.PREVIOUS_DAY_LOW]
    assert len(pdh) == 1
    assert len(pdl) == 1
    # The PDH/PDL come from day1, NOT from day2.
    assert pdh[0].price_lower <= Decimal("1.10050") <= pdh[0].price_upper
    assert pdl[0].price_lower <= Decimal("1.09950") <= pdl[0].price_upper


def test_previous_day_not_emitted_when_only_one_day() -> None:
    """No 'previous day' exists when all candles are same-date → no PDH/PDL."""
    mapper = LiquidityMapper(_config())
    day1 = datetime(2026, 5, 19, 10, 0, 0, tzinfo=timezone.utc)
    candles = [_candle(timestamp=day1 + i * _M5) for i in range(5)]
    zones = mapper.map_zones(candles)
    kinds = {z.kind for z in zones}
    assert LiquidityZoneKind.PREVIOUS_DAY_HIGH not in kinds
    assert LiquidityZoneKind.PREVIOUS_DAY_LOW not in kinds


# ----------------------------------------------------------------------------
# Output filter
# ----------------------------------------------------------------------------
def test_min_zone_strength_drops_weak_zones() -> None:
    """Setting ``min_zone_strength`` above ``rolling_zone_strength`` drops
    the rolling zones — proves the filter is wired."""
    mapper = LiquidityMapper(
        _config(
            rolling_zone_strength=0.5,
            previous_day_zone_strength=0.6,
            min_zone_strength=0.55,
        )
    )
    zones = mapper.map_zones([_candle(i) for i in range(10)])
    assert all(z.kind != LiquidityZoneKind.ROLLING_HIGH for z in zones)
    assert all(z.kind != LiquidityZoneKind.ROLLING_LOW for z in zones)


# ----------------------------------------------------------------------------
# Trendline liquidity is NOT emitted in Phase 1
# ----------------------------------------------------------------------------
def test_trendline_liquidity_not_emitted_in_phase_1() -> None:
    """Documented Phase 1 omission — the enum value exists but the mapper
    must never produce one."""
    mapper = LiquidityMapper(_config())
    candles = [_candle(i, high=f"1.100{i:02d}", low=f"1.099{i:02d}") for i in range(20)]
    zones = mapper.map_zones(candles)
    assert all(z.kind != LiquidityZoneKind.TRENDLINE_LIQUIDITY for z in zones)


# ----------------------------------------------------------------------------
# Config validation
# ----------------------------------------------------------------------------
def test_config_min_equal_touches_must_be_at_least_two() -> None:
    with pytest.raises(ValueError, match="min_equal_touches"):
        LiquidityMapper(_config(min_equal_touches=1))


def test_config_max_touches_must_be_at_least_min_touches() -> None:
    with pytest.raises(ValueError, match="max_touches"):
        LiquidityMapper(
            _config(min_equal_touches=3, max_touches_for_full_strength=2)
        )


def test_config_pip_size_must_be_positive() -> None:
    with pytest.raises(ValueError, match="pip_size"):
        LiquidityMapper(_config(pip_size=Decimal("0")))


def test_config_equal_tolerance_must_be_positive() -> None:
    with pytest.raises(ValueError, match="equal_level_tolerance_pips"):
        LiquidityMapper(_config(equal_level_tolerance_pips=Decimal("0")))


def test_config_swing_lookback_must_be_positive() -> None:
    with pytest.raises(ValueError, match="swing_lookback"):
        LiquidityMapper(_config(swing_lookback=0))


def test_config_strength_must_be_in_unit_interval() -> None:
    with pytest.raises(ValueError, match="rolling_zone_strength"):
        LiquidityMapper(_config(rolling_zone_strength=1.5))


# ----------------------------------------------------------------------------
# Traceability — deterministic zone_id
# ----------------------------------------------------------------------------
def _multi_kind_candles() -> list[Candle]:
    """Fixture exercising EQUAL_HIGHS, ROLLING_*, and PDH/PDL together."""
    day1 = datetime(2026, 5, 18, 10, 0, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 5, 19, 10, 0, 0, tzinfo=timezone.utc)
    candles: list[Candle] = [
        _candle(timestamp=day1 + i * _M5) for i in range(15)
    ]
    for idx in (5, 11):
        candles[idx] = _candle(
            timestamp=day1 + idx * _M5, high="1.10050", low="1.10000"
        )
    for i in range(5):
        candles.append(
            _candle(timestamp=day2 + i * _M5, high="1.10020", low="1.09980")
        )
    return candles


def test_zone_ids_are_16_char_lowercase_hex() -> None:
    """CONVENTIONS.md §13 — ``LiquidityZone.zone_id`` is a 16-char
    lowercase hex string (sha256 prefix)."""
    mapper = LiquidityMapper(_config())
    zones = mapper.map_zones(_multi_kind_candles())
    assert zones
    for z in zones:
        assert len(z.zone_id) == 16
        int(z.zone_id, 16)  # raises on non-hex
        assert z.zone_id == z.zone_id.lower()


def test_zone_ids_are_unique_within_one_call() -> None:
    """Different (kind, mid-bucket, anchor) → different ids in the same
    snapshot. No accidental collision across the kinds emitted here."""
    mapper = LiquidityMapper(_config())
    zones = mapper.map_zones(_multi_kind_candles())
    ids = [z.zone_id for z in zones]
    assert len(set(ids)) == len(ids)


def test_zone_ids_are_deterministic_across_calls() -> None:
    """Same input → byte-for-byte identical zone_ids. The whole point of
    the contract: a sliding-window consumer can dedupe by zone_id alone.
    """
    mapper = LiquidityMapper(_config())
    candles = _multi_kind_candles()
    first = mapper.map_zones(candles)
    second = mapper.map_zones(candles)
    assert [z.zone_id for z in first] == [z.zone_id for z in second]


def test_zone_id_stable_across_growing_window() -> None:
    """Appending a non-disturbing candle (no new swing, no new extreme)
    must NOT change the id of an existing ROLLING_HIGH zone: the anchor
    candle is the same in both calls.
    """
    mapper = LiquidityMapper(
        _config(rolling_lookback_candles=10, swing_lookback=2)
    )
    base = [_candle(i) for i in range(15)]
    base[12] = _candle(12, high="1.10100", low="1.09990")  # the rolling high
    first = mapper.map_zones(base)
    rh1 = [z for z in first if z.kind == LiquidityZoneKind.ROLLING_HIGH][0]

    extended = base + [_candle(i, high="1.10010", low="1.09990") for i in range(15, 18)]
    second = mapper.map_zones(extended)
    rh2 = [z for z in second if z.kind == LiquidityZoneKind.ROLLING_HIGH][0]

    assert rh1.zone_id == rh2.zone_id


def test_zones_carry_symbol_through() -> None:
    mapper = LiquidityMapper(_config())
    candles = [_candle(i, symbol="XAUUSD") for i in range(15)]
    for idx in (5, 11):
        candles[idx] = _candle(
            idx, symbol="XAUUSD", high="1.10050", low="1.10000"
        )
    zones = mapper.map_zones(candles)
    assert zones  # sanity
    assert all(z.symbol == "XAUUSD" for z in zones)


def test_zone_created_at_points_to_most_recent_constituent() -> None:
    """For an equal-highs cluster, ``created_at`` is the open_time of the
    most recent swing pivot, not the first."""
    mapper = LiquidityMapper(_config(swing_lookback=2, min_equal_touches=2))
    candles = [_candle(i) for i in range(15)]
    candles[5] = _candle(5, high="1.10050", low="1.10000")
    candles[11] = _candle(11, high="1.10050", low="1.10000")
    zones = mapper.map_zones(candles)
    z = next(z for z in zones if z.kind == LiquidityZoneKind.EQUAL_HIGHS)
    assert z.created_at == candles[11].open_time
