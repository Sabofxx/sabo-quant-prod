"""
Real market-data downloader — yfinance (indices/energy) + FRED (G10 short rates).

RULE: real data only. No synthetic, no approximation. If a source is unreachable
the function raises — the caller must stop on that instrument, never fabricate.

Writes data/<name>.csv in the same schema the runner/signals expect:
  timestamp(ms,UTC),open,high,low,close
FRED rates are written as data/g10_short_rates.csv: date,currency,rate (monthly).

Usage:
  python download_market_data.py --indices --oil --rates
"""
from __future__ import annotations

import argparse
import io
import time
import urllib.request
from pathlib import Path

import pandas as pd

DATA = Path(__file__).parent / "data"

# Capital-style internal symbol -> (yahoo ticker, output csv)
YF_INSTRUMENTS = {
    "US500":  ("^GSPC",  "us500-d1-2019-01-01-2026-01-01.csv"),
    "US100":  ("^NDX",   "us100-d1-2019-01-01-2026-01-01.csv"),
    "DE40":   ("^GDAXI", "de40-d1-2019-01-01-2026-01-01.csv"),
    "OILWTI": ("CL=F",   "oilwti-d1-2019-01-01-2026-01-01.csv"),
    "OILBRENT": ("BZ=F", "oilbrent-d1-2019-01-01-2026-01-01.csv"),
}

# G10 short rates via FRED OECD 3-month interbank (real, standard carry proxy)
FRED_RATES = {
    "USD": "IR3TIB01USM156N", "EUR": "IR3TIB01EZM156N", "GBP": "IR3TIB01GBM156N",
    "JPY": "IR3TIB01JPM156N", "AUD": "IR3TIB01AUM156N", "NZD": "IR3TIB01NZM156N",
    "CAD": "IR3TIB01CAM156N", "CHF": "IR3TIB01CHM156N",
}


def _write_ohlc(df: pd.DataFrame, csv_name: str, symbol: str) -> int:
    df = df.dropna()
    if df.empty:
        raise RuntimeError(f"{symbol}: empty download — REFUSE to write fake data")
    DATA.mkdir(parents=True, exist_ok=True)
    out = DATA / csv_name
    with out.open("w") as f:
        f.write("timestamp,open,high,low,close\n")
        for ts, r in df.iterrows():
            ms = int(pd.Timestamp(ts).timestamp() * 1000)
            f.write(f"{ms},{float(r['Open'])},{float(r['High'])},{float(r['Low'])},{float(r['Close'])}\n")
    return len(df)


def download_yf(symbol: str, ticker: str, csv_name: str) -> tuple[int, str]:
    import yfinance as yf
    last_err = None
    for attempt in range(4):
        try:
            df = yf.download(ticker, start="2019-01-01", progress=False, auto_adjust=True)
            if df is None or df.empty:
                last_err = "empty"; time.sleep(3); continue
            if isinstance(df.columns, pd.MultiIndex):       # flatten single-ticker multiindex
                df.columns = [c[0] for c in df.columns]
            n = _write_ohlc(df, csv_name, symbol)
            return n, f"{df.index.min().date()}->{df.index.max().date()}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"; time.sleep(4)
    raise RuntimeError(f"{symbol} ({ticker}) download FAILED after retries: {last_err}")


def download_fred_series(series_id: str) -> pd.Series:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read().decode("utf-8")
    df = pd.read_csv(io.StringIO(raw))
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna().set_index("date")["value"]


def download_rates() -> tuple[int, str]:
    frames = []
    for ccy, sid in FRED_RATES.items():
        s = download_fred_series(sid)
        s = s[s.index >= "2019-01-01"]
        if s.empty:
            raise RuntimeError(f"{ccy} ({sid}): empty FRED series — REFUSE fake")
        frames.append(pd.DataFrame({"date": s.index, "currency": ccy, "rate": s.values}))
        time.sleep(1)
    allr = pd.concat(frames).sort_values(["date", "currency"])
    DATA.mkdir(parents=True, exist_ok=True)
    out = DATA / "g10_short_rates.csv"
    allr.to_csv(out, index=False)
    span = f"{allr['date'].min().date()}->{allr['date'].max().date()}"
    return len(allr), span


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--indices", action="store_true")
    ap.add_argument("--oil", action="store_true")
    ap.add_argument("--rates", action="store_true")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    do_all = args.all or not (args.indices or args.oil or args.rates)

    if args.indices or do_all:
        for sym in ("US500", "US100", "DE40"):
            tk, csv = YF_INSTRUMENTS[sym]
            n, span = download_yf(sym, tk, csv)
            print(f"INDEX {sym} ({tk}): {n} rows {span} -> data/{csv}")
    if args.oil or do_all:
        for sym in ("OILWTI", "OILBRENT"):
            tk, csv = YF_INSTRUMENTS[sym]
            n, span = download_yf(sym, tk, csv)
            print(f"OIL {sym} ({tk}): {n} rows {span} -> data/{csv}")
    if args.rates or do_all:
        n, span = download_rates()
        print(f"RATES G10 (FRED OECD 3m): {n} rows {span} -> data/g10_short_rates.csv")


if __name__ == "__main__":
    main()
