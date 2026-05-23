"""
Sandbox — OANDA REST API connector for live execution.

Reads delta orders from prop_delta_orders_latest.csv and submits to OANDA
practice account (or live with env switch). Logs fills back to live_tracker.

Env vars required:
  OANDA_API_KEY        - API token from OANDA account
  OANDA_ACCOUNT_ID     - account ID e.g. 101-xxx-xxxxxxx-001
  OANDA_ENVIRONMENT    - 'practice' or 'live' (default 'practice')

Usage:
  python oanda_connector.py --execute live/prop_delta_orders_latest.csv
  python oanda_connector.py --positions       # just print current positions
  python oanda_connector.py --account-info    # print balance / NAV / margin
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

from oandapyV20 import API
from oandapyV20.endpoints.accounts import AccountSummary
from oandapyV20.endpoints.orders import OrderCreate
from oandapyV20.endpoints.positions import OpenPositions
from oandapyV20.endpoints.pricing import PricingInfo


HERE = Path(__file__).parent
LIVE_DIR = HERE / "live"
TRADE_LOG = LIVE_DIR / "oanda_executions.jsonl"

# OANDA uses "EUR_USD" naming. Map our internal "EURUSD" → OANDA format.
SYMBOL_MAP = {
    "EURUSD": "EUR_USD",
    "GBPUSD": "GBP_USD",
    "USDJPY": "USD_JPY",
    "AUDUSD": "AUD_USD",
    "NZDUSD": "NZD_USD",
    "USDCAD": "USD_CAD",
}


def get_client() -> API:
    key = os.environ.get("OANDA_API_KEY")
    env = os.environ.get("OANDA_ENVIRONMENT", "practice")
    if not key:
        print("ERROR: OANDA_API_KEY env var not set", file=sys.stderr)
        sys.exit(2)
    if env not in ("practice", "live"):
        print(f"ERROR: OANDA_ENVIRONMENT must be 'practice' or 'live', got {env}", file=sys.stderr)
        sys.exit(2)
    return API(access_token=key, environment=env)


def get_account_id() -> str:
    aid = os.environ.get("OANDA_ACCOUNT_ID")
    if not aid:
        print("ERROR: OANDA_ACCOUNT_ID env var not set", file=sys.stderr)
        sys.exit(2)
    return aid


def get_account_summary(client: API, account_id: str) -> dict:
    r = AccountSummary(accountID=account_id)
    client.request(r)
    return r.response["account"]


def get_positions_dict(client: API, account_id: str) -> dict[str, float]:
    """Return {oanda_instrument: net_units_signed}."""
    r = OpenPositions(accountID=account_id)
    client.request(r)
    out: dict[str, float] = {}
    for p in r.response.get("positions", []):
        long_units = float(p["long"]["units"]) if p["long"]["units"] else 0.0
        short_units = float(p["short"]["units"]) if p["short"]["units"] else 0.0
        out[p["instrument"]] = long_units + short_units
    return out


def get_price(client: API, account_id: str, instrument: str) -> float:
    r = PricingInfo(accountID=account_id, params={"instruments": instrument})
    client.request(r)
    p = r.response["prices"][0]
    bid = float(p["bids"][0]["price"])
    ask = float(p["asks"][0]["price"])
    return (bid + ask) / 2.0


def usd_notional_to_units(instrument: str, notional_usd: float, price: float) -> int:
    """Convert signed USD notional to OANDA units (base currency).
    For pairs where base is USD (USD_JPY, USD_CAD), units = notional.
    For pairs where quote is USD (EUR_USD, GBP_USD, AUD_USD, NZD_USD), units = notional / price.
    """
    if instrument.startswith("USD_"):
        return int(notional_usd)
    return int(notional_usd / price)


def submit_order(client: API, account_id: str, instrument: str, units: int) -> dict:
    """Send a MARKET order with FOK (fill or kill) time in force.
    Positive units = long, negative = short."""
    if units == 0:
        return {"status": "skipped_zero_units"}
    data = {
        "order": {
            "instrument": instrument,
            "units": str(int(units)),
            "type": "MARKET",
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
        }
    }
    r = OrderCreate(accountID=account_id, data=data)
    client.request(r)
    return r.response


def append_execution_log(record: dict) -> None:
    LIVE_DIR.mkdir(exist_ok=True)
    with TRADE_LOG.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def execute_delta_csv(client: API, account_id: str, csv_path: Path,
                       min_notional: float = 100.0, dry_run: bool = False) -> list[dict]:
    """Read delta CSV from signal generator, submit orders.
    Returns list of execution records (one per order attempted)."""
    if not csv_path.exists():
        print(f"ERROR: delta CSV not found: {csv_path}", file=sys.stderr)
        sys.exit(3)

    # Get current positions to confirm idempotency
    current = get_positions_dict(client, account_id)
    print(f"Current OANDA positions: {len(current)} non-zero instruments")
    for inst, units in current.items():
        print(f"  {inst}: {units:+.0f} units")

    executions = []
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            delta_usd = float(row.get("delta_notional_usd", 0))
            instrument_internal = row["instrument"]
            oanda_inst = SYMBOL_MAP.get(instrument_internal)
            if not oanda_inst:
                print(f"  SKIP {instrument_internal} : not in OANDA symbol map")
                continue
            if abs(delta_usd) < min_notional:
                print(f"  SKIP {oanda_inst} delta ${delta_usd:.0f} < ${min_notional} threshold")
                continue
            # Convert USD notional to OANDA units
            try:
                price = get_price(client, account_id, oanda_inst)
            except Exception as e:
                print(f"  ERROR pricing {oanda_inst}: {e}")
                continue
            units = usd_notional_to_units(oanda_inst, delta_usd, price)
            if units == 0:
                continue

            print(f"  ORDER {oanda_inst} units={units:+d} (delta_usd ${delta_usd:+.0f} @ {price:.5f})")
            record = {
                "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                "account_id": row.get("account_id"),
                "instrument": oanda_inst,
                "delta_usd": delta_usd,
                "price_mid": price,
                "units": units,
                "dry_run": dry_run,
            }
            if dry_run:
                record["status"] = "dry_run_only"
            else:
                try:
                    resp = submit_order(client, account_id, oanda_inst, units)
                    # Extract fill price if available
                    fill = resp.get("orderFillTransaction", {})
                    record["fill_price"] = float(fill.get("price", 0)) if fill else None
                    record["fill_units"] = int(fill.get("units", 0)) if fill else None
                    record["status"] = "filled" if fill else "submitted"
                    record["transaction_id"] = fill.get("id") if fill else None
                except Exception as e:
                    record["status"] = "error"
                    record["error"] = str(e)
                    print(f"    ERROR: {e}")
            executions.append(record)
            append_execution_log(record)
            time.sleep(0.2)  # gentle rate limiting

    return executions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", type=Path,
                          help="Path to delta orders CSV to execute")
    parser.add_argument("--positions", action="store_true",
                          help="Print current OANDA positions only")
    parser.add_argument("--account-info", action="store_true",
                          help="Print OANDA account summary")
    parser.add_argument("--dry-run", action="store_true",
                          help="Compute orders but do not submit")
    parser.add_argument("--min-notional", type=float, default=100.0,
                          help="Skip orders below this USD delta")
    args = parser.parse_args()

    client = get_client()
    account_id = get_account_id()

    if args.account_info:
        summary = get_account_summary(client, account_id)
        print(json.dumps({
            "balance": summary["balance"],
            "NAV": summary["NAV"],
            "marginUsed": summary["marginUsed"],
            "marginAvailable": summary["marginAvailable"],
            "openPositionCount": summary["openPositionCount"],
            "currency": summary["currency"],
            "unrealizedPL": summary["unrealizedPL"],
        }, indent=2))
        return

    if args.positions:
        positions = get_positions_dict(client, account_id)
        if not positions:
            print("No open positions.")
            return
        for inst, units in positions.items():
            print(f"  {inst}: {units:+.0f} units")
        return

    if args.execute:
        executions = execute_delta_csv(client, account_id, args.execute,
                                         min_notional=args.min_notional,
                                         dry_run=args.dry_run)
        print(f"\nExecuted {len(executions)} orders. Log: {TRADE_LOG}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
