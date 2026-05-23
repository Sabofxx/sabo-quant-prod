"""
Sandbox loader for XAUUSD M5 bid candles — pre-check step.

Reads sandbox/data/xauusd-m5-bid-2019-11-01-2026-05-19.csv (CSV with
header `timestamp,open,high,low,close`; `timestamp` is ms since the Unix
epoch, UTC) and constructs the first 5 ``Candle`` objects to confirm
the format before any analysis runs.

The CSV has NO volume column. We populate ``volume=Decimal(0)`` as an
explicit, documented placeholder — this is honest about what's missing
rather than fabricating a value. LiquidityMapper only reads OHLC
structure in Phase 1, so a zero volume does not bias the result, but
later modules that lean on volume (delta, CVD, exhaustion) MUST be
re-checked against this caveat before they consume this dataset.

This file lives in sandbox/ — code is throwaway, governance rules
relaxed, but the firewall still holds: nothing in production may import
from here.
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

# Make `import sabo_lit.*` work when this file is run directly.
_PARENT = Path(__file__).resolve().parents[2]
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from sabo_lit.core import Candle  # noqa: E402

CSV_PATH = Path(__file__).parent / "data" / "xauusd-m5-bid-2019-11-01-2026-05-19.csv"


def load_candles(limit: int | None = None) -> list[Candle]:
    """Stream the CSV and build ``Candle`` objects.

    ``volume`` is set to ``Decimal(0)`` because the source file is
    bid-only OHLC with no volume column. This is an assumed placeholder,
    not real data — see module docstring.
    """
    out: list[Candle] = []
    with CSV_PATH.open(newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if limit is not None and i >= limit:
                break
            ts_ms = int(row["timestamp"])
            open_time = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            out.append(
                Candle(
                    symbol="XAUUSD",
                    timeframe="M5",
                    open_time=open_time,
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),
                    # PLACEHOLDER — source CSV is bid-only, no volume column.
                    volume=Decimal(0),
                )
            )
    return out


def main() -> None:
    first_five = load_candles(limit=5)

    print(f"CSV: {CSV_PATH.name}")
    print(f"Constructed {len(first_five)} Candle objects (first 5).\n")

    for i, c in enumerate(first_five):
        print(f"#{i}: {c}")

    first = first_five[0]
    print()
    print(f"First open_time:    {first.open_time.isoformat()}")
    print(f"First open_time tz: {first.open_time.tzinfo}")

    expected = datetime(2019, 11, 1, 0, 0, 0, tzinfo=timezone.utc)
    if first.open_time == expected:
        print(f"OK: first candle is {expected.isoformat()} as expected.")
    else:
        print(
            "MISMATCH: first candle open_time does not equal "
            f"{expected.isoformat()} — STOP and ask before continuing."
        )


if __name__ == "__main__":
    main()
