"""
TradingView confirmation layer for the SABO daily rebalance.

Default mode is disabled. In disabled mode this module only records a state file
for dashboard/Telegram visibility and leaves generated signals untouched.

Modes:
  - disabled: no filtering
  - veto_only: opposite TradingView signal blocks a SABO delta
  - require_confirm: SABO delta needs same-symbol same-side TradingView signal
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


HERE = Path(__file__).parent
LIVE_DIR = HERE / "live"
SIGNALS_JSONL = "tradingview_signals.jsonl"
STATE_JSON = "tradingview_filter_state.json"
VALID_MODES = {"disabled", "veto_only", "require_confirm"}
DEFAULT_SYMBOLS = {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD"}


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def normalize_symbol(value: object) -> str:
    raw = str(value or "").strip().upper()
    if ":" in raw:
        raw = raw.split(":")[-1]
    return "".join(ch for ch in raw if ch.isalnum())


def normalize_side(value: object) -> str:
    raw = str(value or "").strip().upper()
    if raw in {"BUY", "LONG", "BULL", "1"}:
        return "BUY"
    if raw in {"SELL", "SHORT", "BEAR", "-1"}:
        return "SELL"
    return ""


def normalize_timeframe(value: object) -> str:
    raw = str(value or "").strip().upper()
    if raw == "1D":
        return "D"
    if raw == "1H":
        return "60"
    if raw == "4H":
        return "240"
    return raw


def parse_timestamp(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000 if float(value) > 10_000_000_000 else float(value)
        return datetime.fromtimestamp(seconds, tz=UTC).replace(microsecond=0)
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=0)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def signal_id(row: dict[str, Any], symbol: str, side: str, timeframe: str, ts: datetime) -> str:
    existing = str(row.get("idempotency_key") or "").strip()
    if existing:
        return existing
    raw = f"{symbol}|{timeframe}|{side}|{ts.isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_signal(row: dict[str, Any]) -> dict[str, Any] | None:
    symbol = normalize_symbol(row.get("symbol") or row.get("ticker") or row.get("broker_symbol"))
    side = normalize_side(row.get("side") or row.get("direction"))
    timeframe = normalize_timeframe(row.get("timeframe") or row.get("interval"))
    ts = (
        parse_timestamp(row.get("time"))
        or parse_timestamp(row.get("event_time"))
        or parse_timestamp(row.get("ts"))
        or parse_timestamp(row.get("received_at"))
    )
    received_at = parse_timestamp(row.get("received_at")) or parse_timestamp(row.get("ts")) or ts
    if not symbol or not side or not timeframe or ts is None:
        return None
    return {
        "source": "tradingview",
        "strategy": str(row.get("strategy") or "tradingview_alert"),
        "symbol": symbol,
        "timeframe": timeframe,
        "side": side,
        "price": row.get("price"),
        "time": ts.isoformat(),
        "received_at": (received_at or ts).isoformat(),
        "idempotency_key": signal_id(row, symbol, side, timeframe, ts),
    }


def load_worker_signals(worker_url: str, read_token: str, now: datetime) -> list[dict[str, Any]]:
    if not worker_url:
        return []
    url = worker_url.rstrip("/") + f"/signals/today?date={now.date().isoformat()}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    if read_token:
        request.add_header("Authorization", f"Bearer {read_token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return []
    if isinstance(data, dict) and isinstance(data.get("signals"), list):
        return [row for row in data["signals"] if isinstance(row, dict)]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def load_confirmations(
    live_dir: Path,
    now: datetime,
    worker_url: str = "",
    read_token: str = "",
) -> list[dict[str, Any]]:
    rows = load_jsonl(live_dir / SIGNALS_JSONL)
    rows.extend(load_worker_signals(worker_url, read_token, now))
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        signal = normalize_signal(row)
        if not signal:
            continue
        key = str(signal["idempotency_key"])
        if key in seen:
            continue
        seen.add(key)
        normalized.append(signal)
    return normalized


def valid_confirmations(
    signals: list[dict[str, Any]],
    now: datetime,
    max_age_hours: float,
    require_today: bool,
    allowed_timeframes: set[str] | None,
    allowed_symbols: set[str],
) -> list[dict[str, Any]]:
    cutoff = now - timedelta(hours=max_age_hours)
    out: list[dict[str, Any]] = []
    for signal in signals:
        symbol = normalize_symbol(signal.get("symbol"))
        if symbol not in allowed_symbols:
            continue
        timeframe = normalize_timeframe(signal.get("timeframe"))
        if allowed_timeframes and timeframe not in allowed_timeframes:
            continue
        ts = parse_timestamp(signal.get("time"))
        if ts is None or ts < cutoff:
            continue
        if require_today and ts.date() != now.date():
            continue
        out.append(signal)
    return out


def confirmation_map(signals: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for signal in sorted(signals, key=lambda row: str(row.get("time", ""))):
        key = (normalize_symbol(signal.get("symbol")), normalize_side(signal.get("side")))
        out[key] = signal
    return out


def opposite_side(side: str) -> str:
    return "SELL" if side == "BUY" else "BUY" if side == "SELL" else ""


def order_symbol(row: dict[str, Any]) -> str:
    return normalize_symbol(row.get("broker_symbol") or row.get("instrument") or row.get("symbol"))


def order_delta_side(row: dict[str, Any]) -> str:
    return normalize_side(row.get("delta_side") or row.get("side"))


def should_accept_order(
    row: dict[str, Any],
    mode: str,
    confirms: dict[tuple[str, str], dict[str, Any]],
) -> tuple[bool, str]:
    symbol = order_symbol(row)
    side = order_delta_side(row)
    if not symbol or not side:
        return True, "no_delta"
    if mode == "disabled":
        return True, "disabled"
    if mode == "require_confirm":
        if (symbol, side) in confirms:
            return True, "confirmed"
        return False, "missing_tradingview_confirmation"
    if mode == "veto_only":
        if (symbol, opposite_side(side)) in confirms and (symbol, side) not in confirms:
            return False, "opposite_tradingview_confirmation"
        if (symbol, side) in confirms:
            return True, "confirmed"
        return True, "no_tradingview_veto"
    raise ValueError(f"invalid TradingView filter mode: {mode}")


def zero_order_delta(row: dict[str, Any], reason: str) -> None:
    row["tv_filter_original_side"] = row.get("side", "")
    row["tv_filter_original_delta_side"] = row.get("delta_side", "")
    row["tv_filter_original_target_notional_usd"] = row.get("target_notional_usd", 0)
    row["tv_filter_original_delta_notional_usd"] = row.get("delta_notional_usd", 0)
    row["tv_filter_status"] = "rejected"
    row["tv_filter_reason"] = reason
    row["side"] = "FLAT"
    row["signal"] = 0.0
    row["target_notional_usd"] = 0.0
    row["target_pct_account"] = 0.0
    row["estimated_units"] = 0.0
    row["estimated_standard_lots"] = 0.0
    row["delta_notional_usd"] = 0.0
    row["delta_side"] = "NONE"


def apply_to_payload(
    payload: dict[str, Any],
    mode: str,
    confirms: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    orders = payload.get("orders", [])
    if not isinstance(orders, list):
        orders = []
        payload["orders"] = orders

    accepted = 0
    rejected = 0
    for order in orders:
        if not isinstance(order, dict):
            continue
        accept, reason = should_accept_order(order, mode, confirms)
        order["tv_filter_status"] = "accepted" if accept else "rejected"
        order["tv_filter_reason"] = reason
        if accept:
            accepted += 1
        else:
            rejected += 1
            zero_order_delta(order, reason)

    accounts = payload.get("accounts", [])
    if isinstance(accounts, list):
        by_account: dict[str, list[dict[str, Any]]] = {}
        for order in orders:
            if isinstance(order, dict):
                by_account.setdefault(str(order.get("account_id", "")), []).append(order)
        for account in accounts:
            if not isinstance(account, dict):
                continue
            account_orders = by_account.get(str(account.get("account_id", "")), [])
            account["gross_notional_usd"] = round(
                sum(abs(float(order.get("target_notional_usd", 0) or 0)) for order in account_orders),
                2,
            )
            account["delta_gross_notional_usd"] = round(
                sum(abs(float(order.get("delta_notional_usd", 0) or 0)) for order in account_orders),
                2,
            )

    summary = payload.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}
        payload["summary"] = summary
    account_rows = accounts if isinstance(accounts, list) else []
    summary["total_gross_notional_usd"] = round(
        sum(abs(float(account.get("gross_notional_usd", 0) or 0)) for account in account_rows if isinstance(account, dict)),
        2,
    )
    summary["total_delta_notional_usd"] = round(
        sum(abs(float(account.get("delta_gross_notional_usd", 0) or 0)) for account in account_rows if isinstance(account, dict)),
        2,
    )
    summary["tradingview_filter"] = {
        "mode": mode,
        "accepted_orders": accepted,
        "rejected_orders": rejected,
        "confirmations": len(confirms),
    }
    return payload


def order_fieldnames(rows: list[dict[str, Any]]) -> list[str]:
    preferred = [
        "account_id",
        "strategy",
        "instrument",
        "broker_symbol",
        "source",
        "side",
        "signal",
        "weight",
        "target_notional_usd",
        "target_pct_account",
        "estimated_units",
        "estimated_standard_lots",
        "sizing_note",
        "last_price",
        "magic",
        "current_notional_usd",
        "delta_notional_usd",
        "delta_side",
        "tv_filter_status",
        "tv_filter_reason",
        "tv_filter_original_side",
        "tv_filter_original_delta_side",
        "tv_filter_original_target_notional_usd",
        "tv_filter_original_delta_notional_usd",
    ]
    keys: list[str] = []
    for key in preferred:
        if any(key in row for row in rows):
            keys.append(key)
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    return keys


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_filtered_outputs(live_dir: Path, payload: dict[str, Any]) -> None:
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    as_of = str(summary.get("as_of") or utc_now().date().isoformat())
    orders = [row for row in payload.get("orders", []) if isinstance(row, dict)]
    delta_rows = [row for row in orders if abs(float(row.get("delta_notional_usd", 0) or 0)) > 1e-9]
    fields = order_fieldnames(orders)

    for path in (live_dir / "prop_signals_latest.json", live_dir / f"prop_signals_{as_of}.json"):
        write_json(path, payload)
    for path in (live_dir / "prop_orders_latest.csv", live_dir / f"prop_orders_{as_of}.csv"):
        write_csv(path, orders, fields)
    for path in (live_dir / "prop_delta_orders_latest.csv", live_dir / f"prop_delta_orders_{as_of}.csv"):
        write_csv(path, delta_rows, fields)


def env_set_csv(value: str, normalizer) -> set[str]:
    return {normalizer(part) for part in value.split(",") if normalizer(part)}


def apply_live_filter(
    live_dir: Path = LIVE_DIR,
    mode: str = "disabled",
    now: datetime | None = None,
    max_age_hours: float = 24.0,
    require_today: bool = True,
    allowed_timeframes: set[str] | None = None,
    allowed_symbols: set[str] | None = None,
    worker_url: str = "",
    read_token: str = "",
) -> dict[str, Any]:
    current = now or utc_now()
    mode = (mode or "disabled").strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(f"TV_FILTER_MODE must be one of {sorted(VALID_MODES)}, got {mode!r}")

    allowed_symbols = allowed_symbols or DEFAULT_SYMBOLS
    raw_confirmations = load_confirmations(live_dir, current, worker_url, read_token)
    valid = valid_confirmations(
        raw_confirmations,
        current,
        max_age_hours=max_age_hours,
        require_today=require_today,
        allowed_timeframes=allowed_timeframes,
        allowed_symbols=allowed_symbols,
    )
    confirms = confirmation_map(valid)

    payload_path = live_dir / "prop_signals_latest.json"
    payload = load_json(payload_path)
    orders_before = len(payload.get("orders", [])) if isinstance(payload.get("orders"), list) else 0

    state: dict[str, Any] = {
        "ts": current.isoformat(),
        "mode": mode,
        "raw_confirmations": len(raw_confirmations),
        "valid_confirmations": len(valid),
        "confirmation_keys": [f"{symbol}:{side}" for symbol, side in sorted(confirms)],
        "orders_before": orders_before,
        "orders_after": orders_before,
        "accepted_orders": orders_before,
        "rejected_orders": 0,
        "enabled": mode != "disabled",
    }

    if mode != "disabled" and payload:
        payload = apply_to_payload(payload, mode, confirms)
        write_filtered_outputs(live_dir, payload)
        orders = [row for row in payload.get("orders", []) if isinstance(row, dict)]
        rejected = sum(1 for row in orders if row.get("tv_filter_status") == "rejected")
        accepted = sum(1 for row in orders if row.get("tv_filter_status") == "accepted")
        state.update({
            "orders_after": sum(1 for row in orders if abs(float(row.get("delta_notional_usd", 0) or 0)) > 1e-9),
            "accepted_orders": accepted,
            "rejected_orders": rejected,
        })

    write_json(live_dir / STATE_JSON, state)
    return state


def apply_live_filter_from_env(live_dir: Path = LIVE_DIR) -> dict[str, Any]:
    timeframes_raw = os.environ.get("TV_FILTER_TIMEFRAMES", "")
    allowed_timeframes = env_set_csv(timeframes_raw, normalize_timeframe) if timeframes_raw else None
    symbols_raw = os.environ.get("TV_FILTER_SYMBOLS", "")
    allowed_symbols = env_set_csv(symbols_raw, normalize_symbol) if symbols_raw else DEFAULT_SYMBOLS
    require_today = os.environ.get("TV_FILTER_REQUIRE_TODAY", "1").lower() not in {"0", "false", "no"}
    return apply_live_filter(
        live_dir=live_dir,
        mode=os.environ.get("TV_FILTER_MODE", "disabled"),
        max_age_hours=float(os.environ.get("TV_FILTER_MAX_AGE_HOURS", "24")),
        require_today=require_today,
        allowed_timeframes=allowed_timeframes,
        allowed_symbols=allowed_symbols,
        worker_url=os.environ.get("TRADINGVIEW_WORKER_URL", ""),
        read_token=os.environ.get("TRADINGVIEW_WORKER_READ_TOKEN", ""),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply TradingView confirmation filter to live SABO signals.")
    parser.add_argument("--live-dir", default=str(LIVE_DIR))
    parser.add_argument("--mode", default=os.environ.get("TV_FILTER_MODE", "disabled"), choices=sorted(VALID_MODES))
    parser.add_argument("--max-age-hours", type=float, default=float(os.environ.get("TV_FILTER_MAX_AGE_HOURS", "24")))
    parser.add_argument("--allow-yesterday", action="store_true", help="Do not require signal date to equal current UTC date.")
    parser.add_argument("--worker-url", default=os.environ.get("TRADINGVIEW_WORKER_URL", ""))
    parser.add_argument("--read-token", default=os.environ.get("TRADINGVIEW_WORKER_READ_TOKEN", ""))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    state = apply_live_filter(
        live_dir=Path(args.live_dir),
        mode=args.mode,
        max_age_hours=args.max_age_hours,
        require_today=not args.allow_yesterday,
        worker_url=args.worker_url,
        read_token=args.read_token,
    )
    print(
        "TradingView filter: "
        f"mode={state['mode']} valid={state['valid_confirmations']} "
        f"accepted={state['accepted_orders']} rejected={state['rejected_orders']}"
    )


if __name__ == "__main__":
    main()
