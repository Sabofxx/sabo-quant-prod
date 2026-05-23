"""
LiquidityMapper — concrete implementation.

Maps zones where stop liquidity is INFERRED to accumulate, from public
OHLCV candles. The mapper makes no claim to observe actual order-book
state or institutional flow — every zone is a pattern recognised in
public price action, named accordingly ("inferred", "swing", etc.).

Zone kinds emitted in Phase 1
-----------------------------

* ``EQUAL_HIGHS`` / ``EQUAL_LOWS``
    Clusters of swing-high / swing-low pivots whose prices fall within
    ``equal_level_tolerance_pips`` of each other. LIT rationale: when
    price prints a high (or low) two or more times at roughly the same
    level, stop orders accumulate just above (below) — long-trapped
    shorts' stops above equal highs, the symmetric case below equal
    lows. A sweep of one of these levels is the canonical inducement
    pattern the detector will look for in step 2.

* ``ROLLING_HIGH`` / ``ROLLING_LOW``
    Highest high / lowest low over the last
    ``rolling_lookback_candles``. This is a timezone-free
    approximation: Phase 1 stays out of true session classification
    (Sydney / Tokyo / London / NY), which is the regime engine's job
    (Phase 2). The name ``rolling`` (not ``session``) makes the absence
    of regime awareness explicit at the contract layer.

* ``PREVIOUS_DAY_HIGH`` / ``PREVIOUS_DAY_LOW``
    Highest high / lowest low of the previous CALENDAR day in the
    candles' timezone — well-documented classical LIT levels. Emitted
    only when the candle window covers at least two distinct dates.

Intentionally NOT emitted in Phase 1
------------------------------------

``TRENDLINE_LIQUIDITY`` — fitting a trendline through swing points
introduces too many choices (which swings, slope filter, R² threshold)
to be justified before live data informs the criteria. The
``LiquidityZoneKind`` enum lists it so later phases can add it without
a schema change.

Configuration
-------------
Every threshold, lookback, and strength constant comes from
``LiquidityMapperConfig``. The mapper body contains no magic numbers.
``pip_size`` is symbol-specific (EURUSD = 0.0001, XAUUSD = 0.01): one
mapper instance per symbol is the expected pattern.

The implementation is pure, synchronous compute. ``map_zones`` is
stateless across calls — the same input produces byte-for-byte identical
output. ``zone_id`` is a deterministic hash of
``(kind, level_mid quantized to equal_level_tolerance_pips,
anchor_timestamp)`` so a sliding-window consumer can deduplicate the
same conceptual zone across overlapping calls without a side-table.
See CONVENTIONS.md §13.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sabo_lit.core import Candle
from sabo_lit.core import LiquidityMapper as LiquidityMapperABC
from sabo_lit.core import LiquidityZone, LiquidityZoneKind


@dataclass(frozen=True, kw_only=True)
class LiquidityMapperConfig:
    """All knobs of ``LiquidityMapper``. Immutable after construction.

    Per the project convention, every threshold is explicit; the mapper
    itself contains no defaults — the bootstrap must pass a fully
    populated config.
    """

    # -- symbol scale ---------------------------------------------------
    pip_size: Decimal
    """One pip in price units (EURUSD = 0.0001, XAUUSD = 0.01)."""

    # -- equal-highs / equal-lows --------------------------------------
    equal_level_tolerance_pips: Decimal
    """Max price distance, in pips, between two swing pivots for them to
    be grouped into the same equal-level cluster. Also the width of
    point-zones (rolling, previous day)."""

    min_equal_touches: int
    """Minimum swing count in a cluster to emit an
    ``EQUAL_HIGHS`` / ``EQUAL_LOWS`` zone. Must be >= 2."""

    max_touches_for_full_strength: int
    """Touch count at which ``strength_score`` reaches 1.0. Strength
    scales linearly below; clamped to 1.0 above."""

    # -- swing pivot detection ------------------------------------------
    swing_lookback: int
    """Candles required on each side of a pivot for it to count as a
    swing high (strictly greater high) or swing low (strictly lower
    low)."""

    # -- rolling / previous-day windows --------------------------------
    rolling_lookback_candles: int
    previous_day_lookback_candles: int

    rolling_zone_strength: float
    previous_day_zone_strength: float

    # -- output filter --------------------------------------------------
    min_zone_strength: float
    """Zones with ``strength_score`` strictly below this threshold are
    dropped from the output. Set 0.0 to emit everything."""


class LiquidityMapper(LiquidityMapperABC):
    """Pure-compute implementation of the ``LiquidityMapper`` ABC."""

    def __init__(self, config: LiquidityMapperConfig) -> None:
        self._validate_config(config)
        self._config = config

    @staticmethod
    def _validate_config(config: LiquidityMapperConfig) -> None:
        if config.pip_size <= Decimal(0):
            raise ValueError("pip_size must be > 0")
        if config.equal_level_tolerance_pips <= Decimal(0):
            raise ValueError("equal_level_tolerance_pips must be > 0")
        if config.min_equal_touches < 2:
            raise ValueError("min_equal_touches must be >= 2")
        if config.max_touches_for_full_strength < config.min_equal_touches:
            raise ValueError(
                "max_touches_for_full_strength must be >= min_equal_touches"
            )
        if config.swing_lookback < 1:
            raise ValueError("swing_lookback must be >= 1")
        if config.rolling_lookback_candles < 1:
            raise ValueError("rolling_lookback_candles must be >= 1")
        if config.previous_day_lookback_candles < 1:
            raise ValueError("previous_day_lookback_candles must be >= 1")
        for name, val in (
            ("rolling_zone_strength", config.rolling_zone_strength),
            ("previous_day_zone_strength", config.previous_day_zone_strength),
            ("min_zone_strength", config.min_zone_strength),
        ):
            if not (0.0 <= val <= 1.0):
                raise ValueError(f"{name} must be in [0, 1]")

    @property
    def config(self) -> LiquidityMapperConfig:
        """Expose the bound config for governance / introspection."""
        return self._config

    # -- public API ------------------------------------------------------
    def map_zones(self, candles: list[Candle]) -> list[LiquidityZone]:
        """Emit all liquidity zones inferred from ``candles``.

        ``candles`` MUST be a single-symbol, chronologically-sorted
        list. Mixed symbols are rejected — instantiate one mapper per
        symbol. An empty list returns an empty list.
        """
        if not candles:
            return []
        symbol = candles[0].symbol
        for c in candles:
            if c.symbol != symbol:
                raise ValueError(
                    f"map_zones: mixed symbols ({symbol!r} and "
                    f"{c.symbol!r}); instantiate one mapper per symbol."
                )
        zones: list[LiquidityZone] = []
        zones.extend(self._equal_swings(candles, symbol))
        zones.extend(self._rolling_extremes(candles, symbol))
        zones.extend(self._previous_day_extremes(candles, symbol))
        return [
            z for z in zones
            if z.strength_score >= self._config.min_zone_strength
        ]

    # -- equal highs / equal lows ---------------------------------------
    def _equal_swings(
        self, candles: list[Candle], symbol: str
    ) -> list[LiquidityZone]:
        return [
            *self._cluster_swings(
                self._swing_highs(candles), candles, symbol,
                LiquidityZoneKind.EQUAL_HIGHS,
            ),
            *self._cluster_swings(
                self._swing_lows(candles), candles, symbol,
                LiquidityZoneKind.EQUAL_LOWS,
            ),
        ]

    def _swing_highs(self, candles: list[Candle]) -> list[int]:
        """Indices ``i`` where ``candles[i].high`` is strictly greater
        than the high of every candle in ``[i-n, i-1] U [i+1, i+n]``,
        with ``n = swing_lookback``.

        Strict greater-than means a flat plateau of equal highs is NOT a
        swing — neither candle dominates its neighbour.
        """
        n = self._config.swing_lookback
        out: list[int] = []
        for i in range(n, len(candles) - n):
            ch = candles[i].high
            left = all(candles[i - k].high < ch for k in range(1, n + 1))
            right = all(candles[i + k].high < ch for k in range(1, n + 1))
            if left and right:
                out.append(i)
        return out

    def _swing_lows(self, candles: list[Candle]) -> list[int]:
        n = self._config.swing_lookback
        out: list[int] = []
        for i in range(n, len(candles) - n):
            cl = candles[i].low
            left = all(candles[i - k].low > cl for k in range(1, n + 1))
            right = all(candles[i + k].low > cl for k in range(1, n + 1))
            if left and right:
                out.append(i)
        return out

    def _cluster_swings(
        self,
        swing_indices: list[int],
        candles: list[Candle],
        symbol: str,
        kind: LiquidityZoneKind,
    ) -> list[LiquidityZone]:
        """Sort swings by price; group consecutive ones whose price gap
        is within ``equal_level_tolerance_pips`` into clusters; emit one
        zone per cluster with at least ``min_equal_touches`` swings.

        Cluster boundaries: the gap is measured from the FIRST swing in
        the current cluster, not from the previous swing. This prevents
        drift, e.g. five swings each 1 pip apart never coalesce into one
        cluster spanning 4 pips when tolerance is 1 pip.
        """
        if not swing_indices:
            return []
        tol = self._config.equal_level_tolerance_pips * self._config.pip_size
        price_attr = (
            "high" if kind == LiquidityZoneKind.EQUAL_HIGHS else "low"
        )

        points: list[tuple[Decimal, int]] = sorted(
            ((getattr(candles[i], price_attr), i) for i in swing_indices),
            key=lambda t: t[0],
        )

        clusters: list[list[tuple[Decimal, int]]] = []
        current: list[tuple[Decimal, int]] = [points[0]]
        for price, idx in points[1:]:
            if price - current[0][0] <= tol:
                current.append((price, idx))
            else:
                clusters.append(current)
                current = [(price, idx)]
        clusters.append(current)

        out: list[LiquidityZone] = []
        for cluster in clusters:
            if len(cluster) < self._config.min_equal_touches:
                continue
            prices = [p for p, _ in cluster]
            most_recent_idx = max(i for _, i in cluster)
            mid = (max(prices) + min(prices)) / Decimal(2)
            half = tol / Decimal(2)
            strength = min(
                1.0,
                len(cluster) / float(
                    self._config.max_touches_for_full_strength
                ),
            )
            anchor_ts = candles[most_recent_idx].open_time
            out.append(
                LiquidityZone(
                    zone_id=self._zone_id(kind, mid, anchor_ts),
                    symbol=symbol,
                    kind=kind,
                    price_upper=mid + half,
                    price_lower=mid - half,
                    created_at=anchor_ts,
                    strength_score=strength,
                )
            )
        return out

    # -- rolling extremes -----------------------------------------------
    def _rolling_extremes(
        self, candles: list[Candle], symbol: str
    ) -> list[LiquidityZone]:
        n = self._config.rolling_lookback_candles
        window = candles[-n:] if len(candles) > n else candles
        if not window:
            return []
        highest = max(window, key=lambda c: c.high)
        lowest = min(window, key=lambda c: c.low)
        return [
            self._point_zone(
                symbol, LiquidityZoneKind.ROLLING_HIGH,
                highest.high, highest.open_time,
                self._config.rolling_zone_strength,
            ),
            self._point_zone(
                symbol, LiquidityZoneKind.ROLLING_LOW,
                lowest.low, lowest.open_time,
                self._config.rolling_zone_strength,
            ),
        ]

    # -- previous-day extremes ------------------------------------------
    def _previous_day_extremes(
        self, candles: list[Candle], symbol: str
    ) -> list[LiquidityZone]:
        n = self._config.previous_day_lookback_candles
        lookback = candles[-n:] if len(candles) > n else candles
        if not lookback:
            return []
        # Grouping by ``c.open_time.date()`` uses the timezone embedded
        # in ``open_time`` (tz-aware datetime) or naive date if naive —
        # the caller is responsible for feeding a consistent timezone.
        by_date: dict[date, list[Candle]] = defaultdict(list)
        for c in lookback:
            by_date[c.open_time.date()].append(c)
        dates_sorted = sorted(by_date)
        if len(dates_sorted) < 2:
            return []
        previous_day = dates_sorted[-2]
        prev_candles = by_date[previous_day]
        highest = max(prev_candles, key=lambda c: c.high)
        lowest = min(prev_candles, key=lambda c: c.low)
        return [
            self._point_zone(
                symbol, LiquidityZoneKind.PREVIOUS_DAY_HIGH,
                highest.high, highest.open_time,
                self._config.previous_day_zone_strength,
            ),
            self._point_zone(
                symbol, LiquidityZoneKind.PREVIOUS_DAY_LOW,
                lowest.low, lowest.open_time,
                self._config.previous_day_zone_strength,
            ),
        ]

    # -- helpers --------------------------------------------------------
    def _point_zone(
        self,
        symbol: str,
        kind: LiquidityZoneKind,
        price: Decimal,
        created_at: datetime,
        strength: float,
    ) -> LiquidityZone:
        """Build a tight band of width ``equal_level_tolerance_pips``
        centred on ``price``.

        Using the same width as equal-swing clusters means every emitted
        zone is dimensionally comparable for downstream consumers: a
        sweep detector can apply the same proximity test regardless of
        zone kind.
        """
        tol = self._config.equal_level_tolerance_pips * self._config.pip_size
        half = tol / Decimal(2)
        return LiquidityZone(
            zone_id=self._zone_id(kind, price, created_at),
            symbol=symbol,
            kind=kind,
            price_upper=price + half,
            price_lower=price - half,
            created_at=created_at,
            strength_score=strength,
        )

    def _zone_id(
        self,
        kind: LiquidityZoneKind,
        level_mid: Decimal,
        anchor_ts: datetime,
    ) -> str:
        """Deterministic 16-char hex identity of a zone.

        Hash inputs: ``kind.value``, ``level_mid`` quantized to one
        ``equal_level_tolerance_pips`` step (so two emissions whose mids
        land in the same tolerance bucket collapse to the same id), and
        ``anchor_ts.isoformat()`` (the source candle's open_time).

        Same inputs → same id, by construction. See CONVENTIONS.md §13.
        """
        step = (
            self._config.equal_level_tolerance_pips * self._config.pip_size
        )
        # Quantize mid to the nearest tolerance step before hashing. Note
        # this is identity-only — price_upper / price_lower keep the
        # exact mid +/- half-tol geometry.
        bucket = (level_mid / step).quantize(Decimal(1)) * step
        payload = f"{kind.value}|{bucket}|{anchor_ts.isoformat()}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
