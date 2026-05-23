"""
Integrity check for the XAUUSD M5 dataset.

Walks the raw CSV timestamps (no Candle construction — that would
build 688k Pydantic instances for nothing) and answers:

  * How many candles total, and what time span do they cover?
  * What does the distribution of inter-candle gaps look like?
  * Are there candles on Saturday / Sunday UTC? (tells us whether the
    source is "weekday-only" or near-24/7).
  * Which gaps > 1h are NOT explainable by the weekend (potential
    outages, holidays, vendor maintenance)?

A gap is classified as "weekend" iff the MIDPOINT of the gap falls on
Saturday or Sunday UTC. That correctly captures the standard
Fri 22:00 -> Sun 22:00 close as weekend, even when the exact close /
re-open times jitter, and it correctly flags a 4-hour gap on a Tuesday
as non-weekend.

Sandbox / throwaway. Run before any analysis to know whether the
sliding-window stats are reading a continuous tape or a Swiss cheese.
"""
from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

CSV_PATH = Path(__file__).parent / "data" / "xauusd-m5-bid-2019-11-01-2026-05-19.csv"
EXPECTED = timedelta(minutes=5)
GAP_THRESHOLD = timedelta(hours=1)

# How many non-weekend gaps to dump fully before truncating.
MAX_LIST = 60


def _midpoint_is_weekend(prev_dt: datetime, next_dt: datetime) -> bool:
    mid = prev_dt + (next_dt - prev_dt) / 2
    return mid.weekday() in (5, 6)  # 5 = Saturday, 6 = Sunday


def _bucket(delta: timedelta) -> str:
    s = delta.total_seconds()
    if s <= 300:
        return "exactly 5min"
    if s <= 600:
        return "5-10min"
    if s <= 1800:
        return "10-30min"
    if s <= 3600:
        return "30-60min"
    if s <= 4 * 3600:
        return "1-4h"
    if s <= 12 * 3600:
        return "4-12h"
    if s <= 24 * 3600:
        return "12-24h"
    if s <= 48 * 3600:
        return "24-48h"
    if s <= 72 * 3600:
        return "48-72h"
    return ">72h"


_BUCKET_ORDER = [
    "exactly 5min", "5-10min", "10-30min", "30-60min", "1-4h",
    "4-12h", "12-24h", "24-48h", "48-72h", ">72h",
]


def main() -> None:
    with CSV_PATH.open() as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        ts_ms = [int(row[0]) for row in reader]

    n = len(ts_ms)
    first_dt = datetime.fromtimestamp(ts_ms[0] / 1000, tz=timezone.utc)
    last_dt = datetime.fromtimestamp(ts_ms[-1] / 1000, tz=timezone.utc)
    span = last_dt - first_dt
    expected_if_continuous = int(span.total_seconds() / EXPECTED.total_seconds()) + 1

    print("=== file ===")
    print(f"  candles:                {n:,}")
    print(f"  first open_time:        {first_dt.isoformat()}")
    print(f"  last open_time:         {last_dt.isoformat()}")
    print(f"  span:                   {span.days} days, {span}")
    print(f"  expected if 24/7 M5:    {expected_if_continuous:,}")
    print(f"  delta (expected − got): {expected_if_continuous - n:,}")
    print()

    # ----- single pass over timestamps ----------------------------------
    histogram: Counter[str] = Counter()
    weekend_above_threshold: list[tuple[datetime, datetime, timedelta]] = []
    non_weekend_above_threshold: list[tuple[datetime, datetime, timedelta]] = []

    for i in range(1, n):
        delta_ms = ts_ms[i] - ts_ms[i - 1]
        delta = timedelta(milliseconds=delta_ms)
        histogram[_bucket(delta)] += 1
        if delta > GAP_THRESHOLD:
            prev_dt = datetime.fromtimestamp(ts_ms[i - 1] / 1000, tz=timezone.utc)
            next_dt = datetime.fromtimestamp(ts_ms[i] / 1000, tz=timezone.utc)
            if _midpoint_is_weekend(prev_dt, next_dt):
                weekend_above_threshold.append((prev_dt, next_dt, delta))
            else:
                non_weekend_above_threshold.append((prev_dt, next_dt, delta))

    print("=== inter-candle gap distribution ===")
    for b in _BUCKET_ORDER:
        count = histogram.get(b, 0)
        if count:
            print(f"  {b:14s}  {count:>10,}")
    print()

    # ----- weekend / weekday sanity -------------------------------------
    sat = sum(
        1 for ts in ts_ms
        if datetime.fromtimestamp(ts / 1000, tz=timezone.utc).weekday() == 5
    )
    sun = sum(
        1 for ts in ts_ms
        if datetime.fromtimestamp(ts / 1000, tz=timezone.utc).weekday() == 6
    )
    print("=== weekend coverage (UTC weekday of each candle) ===")
    print(f"  Saturday candles:       {sat:,}")
    print(f"  Sunday candles:         {sun:,}")
    print()

    # ----- gap > 1h summary --------------------------------------------
    total_above = len(weekend_above_threshold) + len(non_weekend_above_threshold)
    print(f"=== gaps > {GAP_THRESHOLD} ===")
    print(f"  total:                  {total_above:,}")
    print(f"  midpoint on weekend:    {len(weekend_above_threshold):,}")
    print(f"  midpoint NOT weekend:   {len(non_weekend_above_threshold):,}")
    print()

    if not non_weekend_above_threshold:
        print("No non-weekend gaps > 1h. Tape is clean during trading hours.")
        return

    print(
        f"=== non-weekend gaps > 1h "
        f"(showing {min(len(non_weekend_above_threshold), MAX_LIST)} of "
        f"{len(non_weekend_above_threshold)}) ==="
    )
    for prev_dt, next_dt, delta in non_weekend_above_threshold[:MAX_LIST]:
        missing_m5 = int(delta.total_seconds() / EXPECTED.total_seconds()) - 1
        weekday_label = prev_dt.strftime("%a")
        print(
            f"  {prev_dt.isoformat()} ({weekday_label})  ->  "
            f"{next_dt.isoformat()}  "
            f"[{delta}, {missing_m5} missing M5]"
        )
    if len(non_weekend_above_threshold) > MAX_LIST:
        print(f"  ... ({len(non_weekend_above_threshold) - MAX_LIST} more)")


if __name__ == "__main__":
    main()
