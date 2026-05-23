"""
Sandbox — Capital.com REST API connector for live execution.

Reads delta orders from prop_delta_orders_latest.csv and submits to Capital.com
demo or live account. Logs fills to live_tracker.

Env vars required:
  CAPITAL_API_KEY        - X-CAP-API-KEY from Settings → API Integration
  CAPITAL_IDENTIFIER     - login email
  CAPITAL_API_PASSWORD   - custom API password set when generating API key
                           (NOT account login password)
  CAPITAL_ENVIRONMENT    - 'demo' or 'live' (default 'demo')

Usage:
  python capital_connector.py --account-info
  python capital_connector.py --positions
  python capital_connector.py --execute live/prop_delta_orders_latest.csv
  python capital_connector.py --execute live/prop_delta_orders_latest.csv --dry-run

NOTE on netting vs hedging:
  Capital.com defaults to hedging mode (new opposite order = new position,
  not netting). For delta-rebalance pattern we assume the account is set to
  netting OR that we close existing first via --reset before opening new.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import requests


HERE = Path(__file__).parent
LIVE_DIR = HERE / "live"
TRADE_LOG = LIVE_DIR / "capital_executions.jsonl"

DEMO_BASE = "https://demo-api-capital.backend-capital.com/api/v1"
LIVE_BASE = "https://api-capital.backend-capital.com/api/v1"

# Capital.com FX epics (verify per account via /markets?searchTerm=)
SYMBOL_MAP = {
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
    "USDJPY": "USDJPY",
    "AUDUSD": "AUDUSD",
    "NZDUSD": "NZDUSD",
    "USDCAD": "USDCAD",
}


class CapitalClient:
    def __init__(self, api_key: str, identifier: str, password: str, env: str = "demo"):
        self.api_key = api_key
        self.identifier = identifier
        self.password = password
        self.base = DEMO_BASE if env == "demo" else LIVE_BASE
        self.cst: str | None = None
        self.security_token: str | None = None
        self.account_id: str | None = None
        self.currency: str | None = None

    def _headers(self) -> dict:
        h = {"X-CAP-API-KEY": self.api_key, "Content-Type": "application/json"}
        if self.cst:
            h["CST"] = self.cst
        if self.security_token:
            h["X-SECURITY-TOKEN"] = self.security_token
        return h

    def login(self) -> None:
        url = f"{self.base}/session"
        payload = {"identifier": self.identifier, "password": self.password}
        r = requests.post(url, json=payload, headers=self._headers(), timeout=15)
        r.raise_for_status()
        self.cst = r.headers["CST"]
        self.security_token = r.headers["X-SECURITY-TOKEN"]
        body = r.json()
        self.account_id = body.get("currentAccountId")
        self.currency = body.get("currencyIsoCode") or body.get("currency")

    def account_summary(self) -> dict:
        r = requests.get(f"{self.base}/accounts", headers=self._headers(), timeout=15)
        r.raise_for_status()
        accs = r.json().get("accounts", [])
        for a in accs:
            if a.get("accountId") == self.account_id:
                return a
        return accs[0] if accs else {}

    def get_positions(self) -> list[dict]:
        r = requests.get(f"{self.base}/positions", headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json().get("positions", [])

    def get_market(self, epic: str) -> dict:
        r = requests.get(f"{self.base}/markets/{epic}", headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json()

    def get_price(self, epic: str) -> float:
        m = self.get_market(epic)
        snap = m["snapshot"]
        bid = float(snap["bid"])
        ofr = float(snap["offer"])
        return (bid + ofr) / 2.0

    def get_daily_candles(self, epic: str, max_bars: int = 30) -> list[dict]:
        """Returns list of {snapshotTime, openPrice{bid,ask}, closePrice{}, ...}."""
        url = f"{self.base}/prices/{epic}"
        params = {"resolution": "DAY", "max": max_bars}
        r = requests.get(url, headers=self._headers(), params=params, timeout=15)
        r.raise_for_status()
        return r.json().get("prices", [])

    def open_position(self, epic: str, direction: str, size: float) -> dict:
        url = f"{self.base}/positions"
        payload = {
            "epic": epic,
            "direction": direction,  # BUY | SELL
            "size": size,
            "guaranteedStop": False,
            "forceOpen": True,
        }
        r = requests.post(url, json=payload, headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json()

    def close_position(self, deal_id: str) -> dict:
        url = f"{self.base}/positions/{deal_id}"
        r = requests.delete(url, headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json()

    def close_all_positions(self) -> list[dict]:
        """Close every open position. Returns list of {deal_id, status, ...}."""
        results = []
        for p in self.get_positions():
            pos = p.get("position", {})
            market = p.get("market", {})
            deal_id = pos.get("dealId")
            epic = market.get("epic", "?")
            if not deal_id:
                continue
            try:
                resp = self.close_position(deal_id)
                results.append({
                    "deal_id": deal_id,
                    "epic": epic,
                    "status": "closed",
                    "reference": resp.get("dealReference"),
                })
                print(f"  CLOSED {epic} {deal_id}")
            except requests.HTTPError as e:
                err = f"{e.response.status_code}: {e.response.text[:200]}"
                results.append({"deal_id": deal_id, "epic": epic,
                                "status": "error", "error": err})
                print(f"  ERROR closing {epic} {deal_id}: {err}")
            except Exception as e:
                results.append({"deal_id": deal_id, "epic": epic,
                                "status": "error", "error": str(e)})
                print(f"  ERROR closing {epic} {deal_id}: {e}")
            time.sleep(0.2)
        return results


def positions_net_by_epic(client: CapitalClient) -> dict[str, dict]:
    """Return {epic: {"net_size": float, "deals": [(deal_id, direction, size)]}}."""
    out: dict[str, dict] = {}
    for p in client.get_positions():
        pos = p.get("position", {})
        market = p.get("market", {})
        epic = market.get("epic")
        if not epic:
            continue
        direction = pos.get("direction", "")
        size = float(pos.get("size", 0))
        deal_id = pos.get("dealId")
        signed = size if direction == "BUY" else -size
        if epic not in out:
            out[epic] = {"net_size": 0.0, "deals": []}
        out[epic]["net_size"] += signed
        out[epic]["deals"].append((deal_id, direction, size))
    return out


def usd_notional_to_size(epic: str, notional_usd: float, price: float) -> float:
    """Convert |USD notional| to Capital.com size (base currency units).
    For USD-base (USDJPY, USDCAD): size in USD = notional.
    For non-USD base (EURUSD, GBPUSD, AUDUSD, NZDUSD): size = USD / price.
    Capital.com size unit per FX pair = base currency, min step varies — verify
    via /markets/{epic} dealingRules.minDealSize before going live.
    """
    if epic.startswith("USD"):
        size = abs(notional_usd)
    else:
        size = abs(notional_usd) / price
    return round(size, 2)


def append_log(record: dict) -> None:
    LIVE_DIR.mkdir(exist_ok=True)
    with TRADE_LOG.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


LIVE_MAX_NOTIONAL_USD_DEFAULT = 250_000.0


def assert_live_safety(client: CapitalClient, executions_preview_notional: float,
                        live_max_notional: float, allow_live_unsafe: bool) -> None:
    """Gate live trading with explicit opt-in + per-order notional cap.

    When CAPITAL_ENVIRONMENT=live the runner must either pass --allow-live (or
    set ALLOW_LIVE=1) AND every individual delta_notional_usd must be below
    live_max_notional. This is a defense in depth: a misconfigured demo
    rebalance ($3M gross visible in current sandbox) would blow a real account.
    """
    if client.base == LIVE_BASE:
        if not allow_live_unsafe:
            print(
                "FATAL: CAPITAL_ENVIRONMENT=live but --allow-live not set. "
                "Re-run with --allow-live or set ALLOW_LIVE=1 to confirm.",
                file=sys.stderr,
            )
            sys.exit(5)
        if executions_preview_notional > live_max_notional:
            print(
                f"FATAL: live order notional ${executions_preview_notional:,.0f} > "
                f"cap ${live_max_notional:,.0f}. Bump --live-max-notional explicitly to override.",
                file=sys.stderr,
            )
            sys.exit(6)


def execute_delta_csv(client: CapitalClient, csv_path: Path,
                       min_notional: float = 100.0, dry_run: bool = False,
                       reset: bool = False,
                       allow_live: bool = False,
                       live_max_notional: float = LIVE_MAX_NOTIONAL_USD_DEFAULT) -> list[dict]:
    if not csv_path.exists():
        print(f"ERROR: delta CSV not found: {csv_path}", file=sys.stderr)
        sys.exit(3)

    # Live safety preflight: scan max single-order notional before any submit
    if client.base == LIVE_BASE and not dry_run:
        max_delta = 0.0
        with csv_path.open() as preview:
            for row in csv.DictReader(preview):
                max_delta = max(max_delta, abs(float(row.get("delta_notional_usd", 0) or 0)))
        assert_live_safety(client, max_delta, live_max_notional, allow_live)

    if reset and not dry_run:
        print("RESET mode: closing all existing positions first...")
        closes = client.close_all_positions()
        n_closed = sum(1 for c in closes if c["status"] == "closed")
        n_err = sum(1 for c in closes if c["status"] == "error")
        print(f"  Closed {n_closed} positions ({n_err} errors)")
        for c in closes:
            append_log({"ts": datetime.now(UTC).isoformat(timespec="seconds"),
                        "action": "close_all", **c})
        time.sleep(2)  # let settlement propagate before reading positions

    current = positions_net_by_epic(client)
    print(f"Current Capital positions: {len(current)} epics")
    for epic, info in current.items():
        print(f"  {epic}: net_size={info['net_size']:+.2f} ({len(info['deals'])} deals)")

    executions = []
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            internal = row["instrument"]
            epic = SYMBOL_MAP.get(internal)
            if not epic:
                print(f"  SKIP {internal} : not in Capital.com symbol map")
                continue
            delta_usd = float(row.get("delta_notional_usd", 0))
            if abs(delta_usd) < min_notional:
                print(f"  SKIP {epic} delta ${delta_usd:.0f} < ${min_notional} threshold")
                continue

            try:
                price = client.get_price(epic)
            except Exception as e:
                record = {
                    "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                    "account_id": row.get("account_id"),
                    "epic": epic,
                    "direction": "PRICE_ERROR",
                    "delta_usd": delta_usd,
                    "price_mid": 0.0,
                    "size": 0.0,
                    "dry_run": dry_run,
                    "status": "error",
                    "error": f"pricing failed: {e}",
                }
                print(f"  ERROR pricing {epic}: {e}")
                executions.append(record)
                append_log(record)
                continue
            size = usd_notional_to_size(epic, delta_usd, price)
            if size == 0:
                continue
            direction = "BUY" if delta_usd > 0 else "SELL"

            print(f"  ORDER {epic} {direction} size={size} "
                  f"(delta_usd ${delta_usd:+.0f} @ {price:.5f})")
            record = {
                "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                "account_id": row.get("account_id"),
                "epic": epic,
                "direction": direction,
                "delta_usd": delta_usd,
                "price_mid": price,
                "size": size,
                "dry_run": dry_run,
            }
            if dry_run:
                record["status"] = "dry_run_only"
            else:
                try:
                    resp = client.open_position(epic, direction, size)
                    record["status"] = "submitted"
                    record["deal_reference"] = resp.get("dealReference")
                except requests.HTTPError as e:
                    record["status"] = "error"
                    record["error"] = f"{e.response.status_code}: {e.response.text[:300]}"
                    print(f"    ERROR: {record['error']}")
                except Exception as e:
                    record["status"] = "error"
                    record["error"] = str(e)
                    print(f"    ERROR: {e}")
            executions.append(record)
            append_log(record)
            time.sleep(0.3)

    return executions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", type=Path, help="Path to delta orders CSV")
    parser.add_argument("--positions", action="store_true")
    parser.add_argument("--account-info", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--min-notional", type=float, default=100.0)
    parser.add_argument("--reset", action="store_true",
                          help="Close all open positions (standalone) OR close-then-open with --execute")
    parser.add_argument("--allow-live", action="store_true",
                          help="Required to submit on CAPITAL_ENVIRONMENT=live. "
                               "Also honored via env ALLOW_LIVE=1.")
    parser.add_argument("--live-max-notional", type=float,
                          default=LIVE_MAX_NOTIONAL_USD_DEFAULT,
                          help=f"Per-order notional cap on live (default ${LIVE_MAX_NOTIONAL_USD_DEFAULT:,.0f})")
    args = parser.parse_args()
    args.allow_live = args.allow_live or os.environ.get("ALLOW_LIVE", "") == "1"

    api_key = os.environ.get("CAPITAL_API_KEY")
    identifier = os.environ.get("CAPITAL_IDENTIFIER")
    password = os.environ.get("CAPITAL_API_PASSWORD")
    env = os.environ.get("CAPITAL_ENVIRONMENT", "demo")

    if not all([api_key, identifier, password]):
        print("ERROR: missing CAPITAL_API_KEY / CAPITAL_IDENTIFIER / CAPITAL_API_PASSWORD",
              file=sys.stderr)
        sys.exit(2)
    if env not in ("demo", "live"):
        print(f"ERROR: CAPITAL_ENVIRONMENT must be 'demo' or 'live', got {env}", file=sys.stderr)
        sys.exit(2)

    client = CapitalClient(api_key, identifier, password, env)
    client.login()
    print(f"Logged in. account_id={client.account_id} currency={client.currency} env={env}")

    if args.account_info:
        acc = client.account_summary()
        bal = acc.get("balance", {})
        print(json.dumps({
            "accountId": acc.get("accountId"),
            "accountName": acc.get("accountName"),
            "balance": bal.get("balance"),
            "available": bal.get("available"),
            "deposit": bal.get("deposit"),
            "currency": acc.get("currency"),
        }, indent=2))
        return

    if args.positions:
        nets = positions_net_by_epic(client)
        if not nets:
            print("No open positions.")
            return
        for epic, info in nets.items():
            print(f"  {epic}: net_size={info['net_size']:+.2f} ({len(info['deals'])} deals)")
        return

    if args.reset and not args.execute:
        closes = client.close_all_positions()
        n_closed = sum(1 for c in closes if c["status"] == "closed")
        print(f"Closed {n_closed} positions (of {len(closes)} attempted).")
        return

    if args.execute:
        executions = execute_delta_csv(client, args.execute,
                                        min_notional=args.min_notional,
                                        dry_run=args.dry_run,
                                        reset=args.reset,
                                        allow_live=args.allow_live,
                                        live_max_notional=args.live_max_notional)
        n_errors = sum(1 for item in executions if item.get("status") == "error")
        print(f"\nExecuted {len(executions)} orders ({n_errors} errors). Log: {TRADE_LOG}")
        if n_errors and not args.dry_run:
            sys.exit(4)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
