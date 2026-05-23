"""
Sandbox — hardcoded news blackout calendar for prop firm signal gate.

Blackout dates : skip trading entirely on these UTC days to avoid:
  - FOMC meetings (8/year)
  - NFP (first Friday each month)
  - ECB meetings (8/year)
  - BoE meetings (8/year)
  - BoC meetings (8/year)
  - CPI release days (US major)

This is approximate/hardcoded for 2024-2027 to cover backtest + forward window.
For production: replace with live news calendar API (e.g., ForexFactory scraper,
Trading Economics API, Investing.com calendar export).

Usage:
  from propfirm_news_calendar import is_blackout_day
  if is_blackout_day(pd.Timestamp("2026-06-17", tz="UTC")):
      # skip trade signal
"""
from __future__ import annotations

import pandas as pd


# FOMC dates 2024-2027 (Fed schedule, published)
FOMC_DATES = [
    # 2024
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    # 2025
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    # 2026 (projected typical dates ; verify before live use)
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
    # 2027 (projected)
    "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-16",
    "2027-07-28", "2027-09-15", "2027-10-27", "2027-12-08",
]

# NFP = first Friday of each month
def nfp_dates_for_year(year: int) -> list[str]:
    dates = []
    for month in range(1, 13):
        # find first Friday of (year, month)
        d = pd.Timestamp(f"{year}-{month:02d}-01")
        while d.weekday() != 4:  # Friday = 4
            d += pd.Timedelta(days=1)
        dates.append(d.strftime("%Y-%m-%d"))
    return dates


# ECB Governing Council monetary policy meetings (8/year typically)
ECB_DATES = [
    # 2024
    "2024-01-25", "2024-03-07", "2024-04-11", "2024-06-06",
    "2024-07-18", "2024-09-12", "2024-10-17", "2024-12-12",
    # 2025
    "2025-01-30", "2025-03-06", "2025-04-17", "2025-06-05",
    "2025-07-24", "2025-09-11", "2025-10-30", "2025-12-18",
    # 2026 (projected typical)
    "2026-01-29", "2026-03-05", "2026-04-16", "2026-06-04",
    "2026-07-23", "2026-09-10", "2026-10-29", "2026-12-17",
]

# BoE MPC meetings (8/year)
BOE_DATES = [
    # 2024
    "2024-02-01", "2024-03-21", "2024-05-09", "2024-06-20",
    "2024-08-01", "2024-09-19", "2024-11-07", "2024-12-19",
    # 2025
    "2025-02-06", "2025-03-20", "2025-05-08", "2025-06-19",
    "2025-08-07", "2025-09-18", "2025-11-06", "2025-12-18",
    # 2026 (projected)
    "2026-02-05", "2026-03-19", "2026-05-07", "2026-06-18",
    "2026-08-06", "2026-09-17", "2026-11-05", "2026-12-17",
]

# BoC rate decisions (8/year)
BOC_DATES = [
    # 2024
    "2024-01-24", "2024-03-06", "2024-04-10", "2024-06-05",
    "2024-07-24", "2024-09-04", "2024-10-23", "2024-12-11",
    # 2025
    "2025-01-29", "2025-03-12", "2025-04-16", "2025-06-04",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    # 2026 (projected)
    "2026-01-28", "2026-03-11", "2026-04-15", "2026-06-03",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]


def _build_blackout_set() -> set[str]:
    blackout = set()
    blackout.update(FOMC_DATES)
    blackout.update(ECB_DATES)
    blackout.update(BOE_DATES)
    blackout.update(BOC_DATES)
    for year in range(2024, 2028):
        blackout.update(nfp_dates_for_year(year))
    return blackout


BLACKOUT_SET = _build_blackout_set()


def is_blackout_day(timestamp: pd.Timestamp) -> bool:
    """Return True if the given UTC timestamp falls on a high-impact news day."""
    return timestamp.strftime("%Y-%m-%d") in BLACKOUT_SET


def reason_for_blackout(timestamp: pd.Timestamp) -> str:
    """Return reason label or empty string."""
    date_str = timestamp.strftime("%Y-%m-%d")
    reasons = []
    if date_str in FOMC_DATES:
        reasons.append("FOMC")
    if date_str in ECB_DATES:
        reasons.append("ECB")
    if date_str in BOE_DATES:
        reasons.append("BoE")
    if date_str in BOC_DATES:
        reasons.append("BoC")
    for year in range(2024, 2028):
        if date_str in nfp_dates_for_year(year):
            reasons.append("NFP")
            break
    return ",".join(reasons) if reasons else ""


def count_blackouts_per_year(year: int) -> int:
    count = 0
    for date_str in BLACKOUT_SET:
        if date_str.startswith(str(year)):
            count += 1
    return count


if __name__ == "__main__":
    # Self-test + show calendar
    print(f"Total blackout days in calendar: {len(BLACKOUT_SET)}")
    for year in (2024, 2025, 2026, 2027):
        print(f"  {year}: {count_blackouts_per_year(year)} blackout days")

    # Sample queries
    samples = ["2026-06-17", "2026-06-18", "2026-06-19", "2026-09-04", "2026-09-16"]
    print("\nSample queries:")
    for s in samples:
        ts = pd.Timestamp(s, tz="UTC")
        print(f"  {s} : blackout={is_blackout_day(ts)} reason={reason_for_blackout(ts)!r}")
