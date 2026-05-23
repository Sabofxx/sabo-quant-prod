"""
Minimal MetaTrader 5 connector for prop-firm delta-order execution.

Default mode is dry-run. Use --live only after a demo account has been tested.
The Python MetaTrader5 package is Windows-oriented; on macOS this module will
still import, but live connection usually requires a Windows VPS/terminal.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import live_tracker


try:
    import MetaTrader5 as mt5  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - depends on local OS/platform
    mt5 = None


HERE = Path(__file__).parent
LIVE_DIR = HERE / "live"
DEFAULT_DELTA_CSV = LIVE_DIR / "prop_delta_orders_latest.csv"
FILLS_LOG = LIVE_DIR / "mt5_fills.jsonl"
DEFAULT_MAGIC = 2026052200

SUCCESS_RETCODES = {10008, 10009}


@dataclass(frozen=True)
class FillRecord:
    ts: str
    account_id: str
    symbol: str
    side: str
    requested_lots: float
    filled_lots: float
    fill_price: float | None
    retcode: int | None
    comment: str
    dry_run: bool
    magic: int


def require_mt5() -> Any:
    if mt5 is None:
        raise RuntimeError("MetaTrader5 package is not installed/available. Use --dry-run or run on MT5-capable host.")
    return mt5


def connect(login: int | None = None, password: str | None = None, server: str | None = None) -> None:
    terminal = require_mt5()
    kwargs: dict[str, Any] = {}
    if login is not None:
        kwargs["login"] = int(login)
    if password:
        kwargs["password"] = password
    if server:
        kwargs["server"] = server
    if not terminal.initialize(**kwargs):
        raise RuntimeError(f"MT5 initialize failed: {terminal.last_error()}")


def disconnect() -> None:
    if mt5 is not None:
        mt5.shutdown()


def symbol_contract_size(symbol: str) -> float:
    terminal = require_mt5()
    info = terminal.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"symbol not found: {symbol}")
    return float(info.trade_contract_size or 100_000)


def get_positions(magic: int) -> dict[str, float]:
    """Return approximate symbol -> notional USD for positions matching magic."""
    terminal = require_mt5()
    positions = terminal.positions_get()
    if positions is None:
        raise RuntimeError(f"positions_get failed: {terminal.last_error()}")
    out: dict[str, float] = {}
    for position in positions:
        if int(position.magic) != int(magic):
            continue
        symbol = str(position.symbol)
        contract_size = symbol_contract_size(symbol)
        price = float(position.price_current or position.price_open)
        sign = 1.0 if int(position.type) == terminal.POSITION_TYPE_BUY else -1.0
        out[symbol] = out.get(symbol, 0.0) + sign * float(position.volume) * contract_size * price
    return out


def ensure_symbol(symbol: str) -> None:
    terminal = require_mt5()
    info = terminal.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"symbol not found in MT5 Market Watch: {symbol}")
    if not info.visible and not terminal.symbol_select(symbol, True):
        raise RuntimeError(f"symbol exists but cannot be selected: {symbol}")


def place_order(symbol: str, side: str, lots: float, magic: int = DEFAULT_MAGIC, deviation: int = 20) -> float:
    """Place a market order and return fill price. Raises on broker rejection."""
    terminal = require_mt5()
    ensure_symbol(symbol)
    tick = terminal.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"no tick available for {symbol}")
    side_upper = side.upper()
    if side_upper == "BUY":
        order_type = terminal.ORDER_TYPE_BUY
        price = float(tick.ask)
    elif side_upper == "SELL":
        order_type = terminal.ORDER_TYPE_SELL
        price = float(tick.bid)
    else:
        raise ValueError(f"side must be BUY or SELL, got {side!r}")
    request = {
        "action": terminal.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(lots),
        "type": order_type,
        "price": price,
        "deviation": deviation,
        "magic": int(magic),
        "comment": "sabo_prop_delta",
        "type_time": terminal.ORDER_TIME_GTC,
        "type_filling": terminal.ORDER_FILLING_IOC,
    }
    result = terminal.order_send(request)
    if result is None:
        raise RuntimeError(f"order_send returned None: {terminal.last_error()}")
    if int(result.retcode) not in SUCCESS_RETCODES:
        raise RuntimeError(f"order rejected retcode={result.retcode} comment={result.comment}")
    return float(result.price)


def read_delta_orders(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"delta CSV not found: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def lots_from_row(row: dict[str, str]) -> float:
    value = row.get("estimated_standard_lots")
    if value in (None, "", "None"):
        return 0.0
    return abs(float(value))


def side_from_row(row: dict[str, str]) -> str:
    side = (row.get("delta_side") or "").upper()
    if side in {"BUY", "SELL"}:
        return side
    return "NONE"


def log_fill(record: FillRecord) -> None:
    LIVE_DIR.mkdir(exist_ok=True)
    with FILLS_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(record)) + "\n")
    if record.fill_price is not None and record.filled_lots > 0:
        live_tracker.jsonl_append(
            live_tracker.TRADES_LOG,
            {
                "ts": record.ts,
                "account": record.account_id,
                "instrument": record.symbol,
                "side": record.side,
                "target_price": record.fill_price,
                "fill_price": record.fill_price,
                "notional_usd": record.filled_lots * 100_000,
                "slippage_bps": 0.0,
                "source": "mt5_connector",
                "dry_run": record.dry_run,
            },
        )


def execute_delta_orders(
    rows: list[dict[str, str]],
    magic: int,
    dry_run: bool,
    max_lots_per_order: float,
) -> list[FillRecord]:
    records = []
    for row in rows:
        side = side_from_row(row)
        lots = lots_from_row(row)
        if side == "NONE" or lots <= 0:
            continue
        if lots > max_lots_per_order:
            raise RuntimeError(f"order exceeds max_lots_per_order={max_lots_per_order}: {row}")
        symbol = row.get("broker_symbol") or row.get("instrument") or ""
        account_id = row.get("account_id", "")
        ts = datetime.now(UTC).isoformat(timespec="seconds")
        if dry_run:
            record = FillRecord(
                ts=ts,
                account_id=account_id,
                symbol=symbol,
                side=side,
                requested_lots=lots,
                filled_lots=0.0,
                fill_price=None,
                retcode=None,
                comment="dry_run",
                dry_run=True,
                magic=magic,
            )
        else:
            try:
                fill_price = place_order(symbol, side, lots, magic=magic)
                record = FillRecord(
                    ts=ts,
                    account_id=account_id,
                    symbol=symbol,
                    side=side,
                    requested_lots=lots,
                    filled_lots=lots,
                    fill_price=fill_price,
                    retcode=10009,
                    comment="filled_or_accepted",
                    dry_run=False,
                    magic=magic,
                )
            except Exception as exc:
                record = FillRecord(
                    ts=ts,
                    account_id=account_id,
                    symbol=symbol,
                    side=side,
                    requested_lots=lots,
                    filled_lots=0.0,
                    fill_price=None,
                    retcode=None,
                    comment=f"rejected: {exc}",
                    dry_run=False,
                    magic=magic,
                )
        log_fill(record)
        records.append(record)
    return records


def dump_symbols(symbols: list[str]) -> None:
    terminal = require_mt5()
    for symbol in symbols:
        info = terminal.symbol_info(symbol)
        if info is None:
            print(f"{symbol}: NOT_FOUND")
            continue
        payload = {
            "symbol": symbol,
            "visible": bool(info.visible),
            "trade_contract_size": info.trade_contract_size,
            "volume_min": info.volume_min,
            "volume_max": info.volume_max,
            "volume_step": info.volume_step,
            "point": info.point,
            "trade_tick_value": info.trade_tick_value,
            "swap_long": info.swap_long,
            "swap_short": info.swap_short,
        }
        print(json.dumps(payload, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute prop-firm MT5 delta orders.")
    parser.add_argument("--delta-csv", default=str(DEFAULT_DELTA_CSV))
    parser.add_argument("--login", type=int, default=int(os.environ["MT5_LOGIN"]) if "MT5_LOGIN" in os.environ else None)
    parser.add_argument("--password", default=os.environ.get("MT5_PASSWORD"))
    parser.add_argument("--server", default=os.environ.get("MT5_SERVER"))
    parser.add_argument("--magic", type=int, default=DEFAULT_MAGIC)
    parser.add_argument("--max-lots-per-order", type=float, default=5.0)
    parser.add_argument("--live", action="store_true", help="Actually send orders. Default is dry-run.")
    parser.add_argument("--dump-symbols", nargs="*", help="Connect and print MT5 symbol contract specs.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dry_run = not args.live
    if not dry_run or args.dump_symbols is not None:
        connect(args.login, args.password, args.server)
    try:
        if args.dump_symbols is not None:
            dump_symbols(args.dump_symbols)
            return
        rows = read_delta_orders(Path(args.delta_csv))
        records = execute_delta_orders(rows, args.magic, dry_run=dry_run, max_lots_per_order=args.max_lots_per_order)
        filled = sum(record.filled_lots for record in records)
        rejected = sum(1 for record in records if record.comment.startswith("rejected"))
        print(f"Orders processed: {len(records)} dry_run={dry_run} filled_lots={filled:.2f} rejected={rejected}")
        print(f"Fill log: {FILLS_LOG}")
    finally:
        if not dry_run or args.dump_symbols is not None:
            disconnect()


if __name__ == "__main__":
    main()
