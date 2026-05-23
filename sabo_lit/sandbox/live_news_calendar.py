"""
Live economic-news blackout calendar with cache and hardcoded fallback.

Primary source:
  - Trading Economics Calendar API when TRADING_ECONOMICS_KEY is configured.

Fallback:
  - sandbox/propfirm_news_calendar.py hardcoded high-impact calendar.

This module is intentionally conservative. If the API fails, it does not crash
daily signal generation; it falls back to the static blackout set and reports the
source in the CLI output.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

import propfirm_news_calendar as fallback_news


HERE = Path(__file__).parent
CACHE_DIR = HERE / "cache"
DEFAULT_CACHE = CACHE_DIR / "live_news_calendar.json"
DEFAULT_API_URL = "https://api.tradingeconomics.com/calendar"

WATCH_COUNTRIES = {"United States", "Euro Area", "United Kingdom", "Canada", "Japan"}
WATCH_TERMS = {
    "fomc": "FOMC",
    "federal reserve": "FOMC",
    "non farm payrolls": "NFP",
    "non-farm payrolls": "NFP",
    "nonfarm payrolls": "NFP",
    "nfp": "NFP",
    "ecb": "ECB",
    "european central bank": "ECB",
    "boe": "BoE",
    "bank of england": "BoE",
    "boc": "BoC",
    "bank of canada": "BoC",
    "boj": "BoJ",
    "bank of japan": "BoJ",
    "cpi": "CPI",
    "inflation rate": "CPI",
}


@dataclass(frozen=True)
class NewsEvent:
    date: str
    label: str
    country: str
    event: str
    category: str
    source: str


def normalize_timestamp(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def cache_is_fresh(path: Path, max_age_hours: int = 12) -> bool:
    if not path.exists():
        return False
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return datetime.now(UTC) - modified < timedelta(hours=max_age_hours)


def load_cache_payload(path: Path = DEFAULT_CACHE) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_cache(path: Path = DEFAULT_CACHE) -> list[NewsEvent]:
    raw = load_cache_payload(path)
    return [NewsEvent(**event) for event in raw.get("events", [])]


def write_cache(events: list[NewsEvent], path: Path = DEFAULT_CACHE, start: str = "", end: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "start": start,
        "end": end,
        "events": [asdict(event) for event in sorted(events, key=lambda item: item.date)],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def event_label(row: dict[str, Any]) -> str | None:
    text = " ".join(
        str(row.get(key, ""))
        for key in ("Event", "event", "Category", "category", "Ticker", "ticker")
    ).lower()
    for needle, label in WATCH_TERMS.items():
        if needle in text:
            return label
    return None


def parse_trading_economics_rows(rows: list[dict[str, Any]]) -> list[NewsEvent]:
    events = []
    for row in rows:
        country = str(row.get("Country") or row.get("country") or "")
        if country and country not in WATCH_COUNTRIES:
            continue
        label = event_label(row)
        if label is None:
            continue
        date_value = row.get("Date") or row.get("date")
        if not date_value:
            continue
        date = normalize_timestamp(str(date_value)).strftime("%Y-%m-%d")
        events.append(
            NewsEvent(
                date=date,
                label=label,
                country=country,
                event=str(row.get("Event") or row.get("event") or ""),
                category=str(row.get("Category") or row.get("category") or ""),
                source="trading_economics",
            )
        )
    unique = {(event.date, event.label, event.country, event.event): event for event in events}
    return list(unique.values())


def fetch_trading_economics(start: pd.Timestamp, end: pd.Timestamp) -> list[NewsEvent]:
    api_key = os.environ.get("TRADING_ECONOMICS_KEY", "").strip()
    if not api_key:
        raise RuntimeError("TRADING_ECONOMICS_KEY not configured")
    base_url = os.environ.get("TRADING_ECONOMICS_BASE_URL", DEFAULT_API_URL)
    query = urllib.parse.urlencode(
        {
            "c": api_key,
            "d1": start.strftime("%Y-%m-%d"),
            "d2": end.strftime("%Y-%m-%d"),
            "importance": "3",
            "f": "json",
        }
    )
    request = urllib.request.Request(
        f"{base_url}?{query}",
        headers={"User-Agent": "sabo-quant-live-news-calendar/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Trading Economics request failed: {exc}") from exc
    if not isinstance(data, list):
        raise RuntimeError(f"unexpected Trading Economics response type: {type(data).__name__}")
    return parse_trading_economics_rows(data)


def fallback_events(start: pd.Timestamp, end: pd.Timestamp) -> list[NewsEvent]:
    events = []
    current = start.floor("D")
    while current <= end.floor("D"):
        if fallback_news.is_blackout_day(current):
            events.append(
                NewsEvent(
                    date=current.strftime("%Y-%m-%d"),
                    label=fallback_news.reason_for_blackout(current) or "STATIC_BLACKOUT",
                    country="",
                    event="hardcoded fallback",
                    category="fallback",
                    source="propfirm_news_calendar",
                )
            )
        current += pd.Timedelta(days=1)
    return events


def load_events(
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    refresh: bool = False,
    cache_path: Path = DEFAULT_CACHE,
) -> tuple[list[NewsEvent], str]:
    start_ts = normalize_timestamp(start).floor("D")
    end_ts = normalize_timestamp(end).floor("D")
    if not refresh and cache_is_fresh(cache_path):
        payload = load_cache_payload(cache_path)
        cache_start = payload.get("start", "")
        cache_end = payload.get("end", "")
        if cache_start <= start_ts.strftime("%Y-%m-%d") and cache_end >= end_ts.strftime("%Y-%m-%d"):
            cached = [
                NewsEvent(**event)
                for event in payload.get("events", [])
                if start_ts.strftime("%Y-%m-%d") <= event["date"] <= end_ts.strftime("%Y-%m-%d")
            ]
            return cached, "cache"
    try:
        events = fetch_trading_economics(start_ts, end_ts)
        if events:
            write_cache(events, cache_path, start_ts.strftime("%Y-%m-%d"), end_ts.strftime("%Y-%m-%d"))
            return events, "trading_economics"
    except RuntimeError as exc:
        # Only warn when key was attempted but failed; silent when no key configured
        # (fallback is the documented default for users without a TE API subscription).
        if os.environ.get("TRADING_ECONOMICS_KEY", "").strip():
            print(f"WARNING: {exc}; using hardcoded fallback.")
    events = fallback_events(start_ts, end_ts)
    write_cache(events, cache_path, start_ts.strftime("%Y-%m-%d"), end_ts.strftime("%Y-%m-%d"))
    return events, "fallback"


def blackout_map(start: str | pd.Timestamp, end: str | pd.Timestamp, refresh: bool = False) -> dict[str, str]:
    events, _source = load_events(start, end, refresh=refresh)
    out: dict[str, list[str]] = {}
    for event in events:
        out.setdefault(event.date, []).append(event.label)
    return {date: ",".join(sorted(set(labels))) for date, labels in out.items()}


def is_blackout_day(timestamp: pd.Timestamp, refresh: bool = False) -> bool:
    ts = normalize_timestamp(timestamp)
    return ts.strftime("%Y-%m-%d") in blackout_map(ts, ts, refresh=refresh)


def reason_for_blackout(timestamp: pd.Timestamp, refresh: bool = False) -> str:
    ts = normalize_timestamp(timestamp)
    return blackout_map(ts, ts, refresh=refresh).get(ts.strftime("%Y-%m-%d"), "")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch/cache live macro blackout dates.")
    parser.add_argument("--start", default=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d"))
    parser.add_argument("--end", default=(pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=120)).strftime("%Y-%m-%d"))
    parser.add_argument("--refresh", action="store_true", help="Bypass cache and try API.")
    parser.add_argument("--cache", default=str(DEFAULT_CACHE), help="Cache JSON path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    events, source = load_events(args.start, args.end, refresh=args.refresh, cache_path=Path(args.cache))
    print(f"News events: {len(events)} source={source}")
    for event in sorted(events, key=lambda item: (item.date, item.label)):
        print(f"  {event.date} {event.label:<5} {event.country:<15} {event.event}")
    print(f"Cache: {args.cache}")


if __name__ == "__main__":
    main()
