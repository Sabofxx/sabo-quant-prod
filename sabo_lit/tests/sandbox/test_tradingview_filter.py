from __future__ import annotations

import csv
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


_PROJECT_PARENT = Path(__file__).resolve().parents[3]
if str(_PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_PARENT))

from sabo_lit.sandbox import tradingview_filter as tvf  # noqa: E402


def write_live_payload(live_dir: Path, now: datetime) -> None:
    payload = {
        "summary": {"as_of": now.date().isoformat()},
        "accounts": [{"account_id": "A1", "gross_notional_usd": 2000, "delta_gross_notional_usd": 2000}],
        "orders": [
            {
                "account_id": "A1",
                "strategy": "TEST",
                "instrument": "EURUSD",
                "broker_symbol": "EURUSD",
                "source": "test",
                "side": "LONG",
                "signal": 1.0,
                "target_notional_usd": 1000.0,
                "target_pct_account": 0.01,
                "estimated_units": 1000.0,
                "estimated_standard_lots": 0.01,
                "delta_notional_usd": 1000.0,
                "delta_side": "BUY",
            },
            {
                "account_id": "A1",
                "strategy": "TEST",
                "instrument": "GBPUSD",
                "broker_symbol": "GBPUSD",
                "source": "test",
                "side": "LONG",
                "signal": 1.0,
                "target_notional_usd": 1000.0,
                "target_pct_account": 0.01,
                "estimated_units": 1000.0,
                "estimated_standard_lots": 0.01,
                "delta_notional_usd": 1000.0,
                "delta_side": "BUY",
            },
        ],
    }
    (live_dir / "prop_signals_latest.json").write_text(json.dumps(payload), encoding="utf-8")


def test_normalizes_tradingview_symbol_and_side() -> None:
    assert tvf.normalize_symbol("OANDA:EURUSD") == "EURUSD"
    assert tvf.normalize_symbol("fx:gbp/usd") == "GBPUSD"
    assert tvf.normalize_side("long") == "BUY"
    assert tvf.normalize_side("SHORT") == "SELL"


def test_valid_confirmations_rejects_old_signals() -> None:
    now = datetime(2026, 5, 27, 22, 5, tzinfo=UTC)
    fresh = {
        "symbol": "EURUSD",
        "side": "BUY",
        "timeframe": "60",
        "time": now.isoformat(),
    }
    old = {
        "symbol": "GBPUSD",
        "side": "BUY",
        "timeframe": "60",
        "time": (now - timedelta(hours=30)).isoformat(),
    }
    signals = [tvf.normalize_signal(fresh), tvf.normalize_signal(old)]
    valid = tvf.valid_confirmations(
        [signal for signal in signals if signal],
        now,
        max_age_hours=24,
        require_today=True,
        allowed_timeframes=None,
        allowed_symbols=tvf.DEFAULT_SYMBOLS,
    )

    assert [row["symbol"] for row in valid] == ["EURUSD"]


def test_require_confirm_filters_delta_csv(tmp_path: Path) -> None:
    now = datetime(2026, 5, 27, 22, 5, tzinfo=UTC)
    write_live_payload(tmp_path, now)
    signal = {
        "source": "tradingview",
        "symbol": "EURUSD",
        "side": "BUY",
        "timeframe": "60",
        "time": now.isoformat(),
        "received_at": now.isoformat(),
        "idempotency_key": "one",
    }
    (tmp_path / "tradingview_signals.jsonl").write_text(json.dumps(signal) + "\n", encoding="utf-8")

    state = tvf.apply_live_filter(
        live_dir=tmp_path,
        mode="require_confirm",
        now=now,
        max_age_hours=24,
        require_today=True,
    )

    assert state["accepted_orders"] == 1
    assert state["rejected_orders"] == 1
    with (tmp_path / "prop_delta_orders_latest.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["broker_symbol"] == "EURUSD"

    filtered = json.loads((tmp_path / "prop_signals_latest.json").read_text(encoding="utf-8"))
    gbp = [row for row in filtered["orders"] if row["broker_symbol"] == "GBPUSD"][0]
    assert gbp["side"] == "FLAT"
    assert gbp["tv_filter_reason"] == "missing_tradingview_confirmation"
