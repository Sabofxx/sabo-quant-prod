"""
Sandbox — download daily crypto klines from Binance public REST API.

No auth needed for public klines endpoint. Free. Rate limit 1200/min.

Coins targeted (max history):
  BTCUSDT, ETHUSDT, BNBUSDT, XRPUSDT, ADAUSDT  → from 2019-01-01
  SOLUSDT  → from 2020-08-11 (earliest listing)
  AVAXUSDT → from 2020-09-22

Saves CSV in same format as FX data:
  timestamp,open,high,low,close

To sandbox/data/{symbol}-d1-spot-2019-01-01-2026-01-01.csv (or actual start).
"""
from __future__ import annotations

import csv
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).parent
DATA = HERE / "data"
DATA.mkdir(exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "SOLUSDT", "AVAXUSDT"]
INTERVAL = "1d"
START_MS = int(datetime(2019, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
END_MS = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
LIMIT = 1000  # max per call

BASE_URL = "https://api.binance.com/api/v3/klines"


def fetch_klines(symbol: str, start_ms: int, end_ms: int) -> list:
    """Fetch all klines for symbol in [start_ms, end_ms]. Paginated by LIMIT."""
    out: list = []
    cur = start_ms
    while cur < end_ms:
        url = (f"{BASE_URL}?symbol={symbol}&interval={INTERVAL}"
               f"&startTime={cur}&endTime={end_ms}&limit={LIMIT}")
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                batch = json.loads(r.read())
        except Exception as e:
            print(f"  ERROR fetching {symbol} from {cur}: {e}")
            break
        if not batch:
            break
        out.extend(batch)
        last_open = batch[-1][0]
        if last_open <= cur:
            break
        # next start = last_open + 1 day in ms
        cur = last_open + 86_400_000
        time.sleep(0.1)  # polite throttle
    return out


def save_csv(symbol: str, klines: list) -> Path:
    """Save as timestamp,open,high,low,close (timestamp in ms, matching FX format)."""
    if not klines:
        raise ValueError(f"No klines for {symbol}")
    first_ms = klines[0][0]
    last_ms = klines[-1][0]
    first_date = datetime.fromtimestamp(first_ms / 1000, tz=timezone.utc).date()
    last_date = datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc).date()
    fname = f"{symbol.lower()}-d1-spot-{first_date}-{last_date}.csv"
    path = DATA / fname
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close"])
        for k in klines:
            # Binance kline: [open_time, open, high, low, close, volume, close_time, ...]
            w.writerow([k[0], k[1], k[2], k[3], k[4]])
    return path


def main() -> None:
    print(f"Downloading {len(SYMBOLS)} crypto daily klines from Binance...")
    print(f"Range: {datetime.fromtimestamp(START_MS/1000, tz=timezone.utc).date()} "
          f"→ {datetime.fromtimestamp(END_MS/1000, tz=timezone.utc).date()}")
    print()
    for sym in SYMBOLS:
        print(f"  Fetching {sym}...")
        klines = fetch_klines(sym, START_MS, END_MS)
        if not klines:
            print(f"    SKIP: no data for {sym}")
            continue
        path = save_csv(sym, klines)
        first = datetime.fromtimestamp(klines[0][0] / 1000, tz=timezone.utc).date()
        last = datetime.fromtimestamp(klines[-1][0] / 1000, tz=timezone.utc).date()
        print(f"    saved {len(klines)} klines  {first} → {last}  → {path.name}")
    print("\ndone")


if __name__ == "__main__":
    main()
