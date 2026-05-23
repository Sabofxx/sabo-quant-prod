"""
Sandbox eval — LiquidityMapper on XAUUSD M5, 2025-05-12 → 2025-05-16 UTC.

Raw 24/7 feed, no weekend filter, no regime gate. The mapper is called
on a growing trailing window at every candle; emitted zones are
deduplicated across calls by (kind, mid-price-bucket) so each conceptual
zone is born once, lives until price closes through its band, and dies.

Outputs:
  * counts by kind
  * lifetime distribution per kind (candles alive before invalidation)
  * touch-depth distribution per kind (penetration into band, in pips)
  * simultaneous-active-zones over time (saturation indicator)
  * JSON dump of (born, died, touches) for the viz script

Sandbox / throwaway. Does not import anything outside ``core/`` and
``lit/``. Production firewall holds.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

_PARENT = Path(__file__).resolve().parents[2]
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from sabo_lit.core import LiquidityZone  # noqa: E402
from sabo_lit.lit.liquidity_mapper import (  # noqa: E402
    LiquidityMapper,
    LiquidityMapperConfig,
)
from sabo_lit.sandbox.load_xauusd import load_candles  # noqa: E402


WINDOW_START = datetime(2025, 5, 12, 0, 0, tzinfo=timezone.utc)
WINDOW_END = datetime(2025, 5, 16, 23, 55, tzinfo=timezone.utc)

# trailing context fed to the mapper at each step — must cover
# previous_day_lookback_candles so PDH/PDL can fire.
TRAILING_CONTEXT = 700
OUT_JSON = Path(__file__).parent / "eval_liquidity_mapper.json"


def build_config() -> LiquidityMapperConfig:
    """Reasonable XAUUSD M5 config — no tuning, no fitting to result."""
    return LiquidityMapperConfig(
        pip_size=Decimal("0.01"),
        equal_level_tolerance_pips=Decimal("10"),   # $0.10 band
        min_equal_touches=2,
        max_touches_for_full_strength=5,
        swing_lookback=5,
        rolling_lookback_candles=96,                # 8h
        previous_day_lookback_candles=576,          # 2 calendar days
        rolling_zone_strength=0.6,
        previous_day_zone_strength=0.8,
        min_zone_strength=0.0,                       # emit everything
    )


@dataclass
class ZoneLife:
    key: tuple[str, str]            # (kind, mid-bucket)
    kind: str
    price_upper: Decimal
    price_lower: Decimal
    born_at: datetime
    died_at: datetime | None = None
    born_idx: int = 0
    died_idx: int | None = None
    touches: list[dict] = field(default_factory=list)  # depth pips, ts


def _bucket(price: Decimal, pip_size: Decimal, bucket_pips: Decimal) -> str:
    step = pip_size * bucket_pips
    return str((price / step).quantize(Decimal(1)) * step)


def _identity(zone: LiquidityZone, pip_size: Decimal) -> tuple[str, str]:
    mid = (zone.price_upper + zone.price_lower) / Decimal(2)
    return (zone.kind.value, _bucket(mid, pip_size, Decimal("5")))


def main() -> None:
    print("loading candles ...")
    all_candles = load_candles()
    print(f"loaded {len(all_candles):,}")

    eval_indices = [
        i for i, c in enumerate(all_candles)
        if WINDOW_START <= c.open_time <= WINDOW_END
    ]
    if not eval_indices:
        print("no candles in eval window — abort.")
        return

    first = eval_indices[0]
    last = eval_indices[-1]
    print(
        f"eval window: idx {first}..{last} "
        f"({all_candles[first].open_time.isoformat()} -> "
        f"{all_candles[last].open_time.isoformat()}) "
        f"= {last - first + 1} candles"
    )

    config = build_config()
    mapper = LiquidityMapper(config)
    pip = config.pip_size

    # zone identity -> ZoneLife
    lives: dict[tuple[str, str], ZoneLife] = {}
    # currently-active identities (price has not yet breached band)
    active: dict[tuple[str, str], ZoneLife] = {}
    # for saturation: count active zones at each step
    active_count_series: list[tuple[datetime, int]] = []

    for i in eval_indices:
        candle = all_candles[i]
        # trailing window: TRAILING_CONTEXT candles ending at i (inclusive)
        lo = max(0, i - TRAILING_CONTEXT + 1)
        window = all_candles[lo : i + 1]

        # 1. emit zones from current window
        emitted = mapper.map_zones(window)

        # 2. register new identities
        for z in emitted:
            key = _identity(z, pip)
            if key not in lives:
                life = ZoneLife(
                    key=key,
                    kind=z.kind.value,
                    price_upper=z.price_upper,
                    price_lower=z.price_lower,
                    born_at=candle.open_time,
                    born_idx=i,
                )
                lives[key] = life
                active[key] = life

        # 3. invalidate active zones the candle CLOSED through
        c_close = candle.close
        dead_now: list[tuple[str, str]] = []
        for key, life in active.items():
            kind = life.kind
            upside = kind in {
                "equal_highs", "rolling_high", "previous_day_high",
            }
            if upside and c_close > life.price_upper:
                life.died_at = candle.open_time
                life.died_idx = i
                dead_now.append(key)
            elif (not upside) and c_close < life.price_lower:
                life.died_at = candle.open_time
                life.died_idx = i
                dead_now.append(key)
        for key in dead_now:
            active.pop(key, None)

        # 4. log touches: candle intersects band but did not close through
        for key, life in active.items():
            if candle.high >= life.price_lower and candle.low <= life.price_upper:
                mid = (life.price_upper + life.price_lower) / Decimal(2)
                # depth = how far inside band from mid, in pips
                depth_price = max(
                    Decimal(0),
                    min(candle.high, life.price_upper)
                    - max(candle.low, life.price_lower),
                )
                depth_pips = float(depth_price / pip)
                dist_close_to_mid_pips = float(abs(c_close - mid) / pip)
                life.touches.append({
                    "ts": candle.open_time.isoformat(),
                    "depth_pips": depth_pips,
                    "close_dist_mid_pips": dist_close_to_mid_pips,
                })

        active_count_series.append((candle.open_time, len(active)))

    # ------------------------------------------------------------------
    # stats
    # ------------------------------------------------------------------
    by_kind: dict[str, list[ZoneLife]] = defaultdict(list)
    for life in lives.values():
        by_kind[life.kind].append(life)

    print()
    print("=== counts by kind ===")
    print(f"  {'kind':<22s} {'total':>8s} {'invalidated':>14s} {'still_active':>14s}")
    for kind in sorted(by_kind):
        items = by_kind[kind]
        invalidated = sum(1 for life in items if life.died_at is not None)
        still = len(items) - invalidated
        print(f"  {kind:<22s} {len(items):>8d} {invalidated:>14d} {still:>14d}")

    print()
    print("=== lifetime in candles (invalidated zones only) ===")
    print(f"  {'kind':<22s} {'n':>6s} {'min':>6s} {'p25':>6s} {'med':>6s} {'p75':>6s} {'max':>8s}")
    for kind in sorted(by_kind):
        items = [
            (life.died_idx - life.born_idx) for life in by_kind[kind]
            if life.died_idx is not None
        ]
        if not items:
            print(f"  {kind:<22s} {0:>6d}   (none invalidated)")
            continue
        items.sort()
        n = len(items)

        def q(p: float, items: list[int] = items, n: int = n) -> int:
            return items[min(n - 1, int(p * n))]

        print(
            f"  {kind:<22s} {n:>6d} {items[0]:>6d} {q(0.25):>6d} "
            f"{items[n // 2]:>6d} {q(0.75):>6d} {items[-1]:>8d}"
        )

    print()
    print("=== touch count per zone (touches before invalidation) ===")
    print(f"  {'kind':<22s} {'n_zones':>8s} {'tot_touch':>10s} {'mean':>8s} {'max':>6s}")
    for kind in sorted(by_kind):
        items = by_kind[kind]
        counts = [len(life.touches) for life in items]
        if not counts:
            continue
        print(
            f"  {kind:<22s} {len(items):>8d} {sum(counts):>10d} "
            f"{statistics.mean(counts):>8.2f} {max(counts):>6d}"
        )

    print()
    print("=== penetration depth (pips) on touch ===")
    print(f"  {'kind':<22s} {'n_touch':>8s} {'min':>6s} {'med':>6s} {'p90':>6s} {'max':>8s}")
    for kind in sorted(by_kind):
        depths = [
            t["depth_pips"]
            for life in by_kind[kind]
            for t in life.touches
        ]
        if not depths:
            continue
        depths.sort()
        n = len(depths)
        print(
            f"  {kind:<22s} {n:>8d} {depths[0]:>6.1f} "
            f"{depths[n // 2]:>6.1f} {depths[min(n - 1, int(0.9 * n))]:>6.1f} "
            f"{depths[-1]:>8.1f}"
        )

    print()
    print("=== simultaneous active zones (saturation) ===")
    vals = [v for _, v in active_count_series]
    vals_sorted = sorted(vals)
    n = len(vals_sorted)
    print(f"  steps:                {n}")
    print(f"  min active:           {vals_sorted[0]}")
    print(f"  median active:        {vals_sorted[n // 2]}")
    print(f"  p90 active:           {vals_sorted[min(n - 1, int(0.9 * n))]}")
    print(f"  max active:           {vals_sorted[-1]}")
    print(f"  mean active:          {statistics.mean(vals):.1f}")

    # ------------------------------------------------------------------
    # dump for viz
    # ------------------------------------------------------------------
    payload = {
        "config": {
            "pip_size": str(pip),
            "equal_level_tolerance_pips": str(config.equal_level_tolerance_pips),
            "min_equal_touches": config.min_equal_touches,
            "swing_lookback": config.swing_lookback,
            "rolling_lookback_candles": config.rolling_lookback_candles,
            "previous_day_lookback_candles": config.previous_day_lookback_candles,
            "trailing_context": TRAILING_CONTEXT,
        },
        "window": {
            "start": WINDOW_START.isoformat(),
            "end": WINDOW_END.isoformat(),
        },
        "candles": [
            {
                "t": all_candles[i].open_time.isoformat(),
                "o": float(all_candles[i].open),
                "h": float(all_candles[i].high),
                "l": float(all_candles[i].low),
                "c": float(all_candles[i].close),
            }
            for i in eval_indices
        ],
        "zones": [
            {
                "kind": life.kind,
                "price_upper": float(life.price_upper),
                "price_lower": float(life.price_lower),
                "born_at": life.born_at.isoformat(),
                "died_at": life.died_at.isoformat() if life.died_at else None,
                "touches": len(life.touches),
            }
            for life in lives.values()
        ],
        "active_count_series": [
            {"t": t.isoformat(), "n": n} for t, n in active_count_series
        ],
    }
    OUT_JSON.write_text(json.dumps(payload))
    print()
    print(f"wrote: {OUT_JSON}")


if __name__ == "__main__":
    main()
