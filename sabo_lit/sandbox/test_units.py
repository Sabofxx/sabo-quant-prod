"""
Unit tests for the money-touching sandbox logic (pytest).

Covers: signal directions (no look-ahead assumptions), pip conversion used by
the slippage elimination criterion, PnL string parsing from Capital transaction
history, the FX schedule guard, and the drawdown breakers of strategy_runner.

Run: python -m pytest sabo_lit/sandbox/test_units.py -q
"""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

import capital_connector as cc
from automated_runner import market_execution_allowed
from strategy_runner import daily_drawdown_check, peak_drawdown_check
from strategy_signals import momentum_signal


def _series(values: list[float]) -> pd.Series:
    idx = pd.date_range("2025-01-01", periods=len(values), freq="1D", tz="UTC")
    return pd.Series(values, index=idx)


# --- signals -----------------------------------------------------------------

def test_tsm_long_on_uptrend():
    up = _series([100 + i for i in range(80)])
    assert momentum_signal(up, lookback=60, kind="tsm") == 1.0


def test_tsm_short_on_downtrend():
    down = _series([200 - i for i in range(80)])
    assert momentum_signal(down, lookback=60, kind="tsm") == -1.0


def test_long_only_clamps_short_to_flat():
    down = _series([200 - i for i in range(80)])
    assert momentum_signal(down, lookback=60, kind="tsm", long_only=True) == 0.0


def test_donchian_breakout_long():
    flat_then_break = _series([100.0] * 70 + [105.0])
    assert momentum_signal(flat_then_break, lookback=55, kind="donchian") == 1.0


def test_signal_flat_when_history_too_short():
    short = _series([100.0, 101.0, 102.0])
    assert momentum_signal(short, lookback=60, kind="tsm") == 0.0


# --- pip conversion (slippage criterion) --------------------------------------

def test_pip_size_fx_jpy_and_nonfx():
    assert cc.pip_size("EURUSD") == 0.0001
    assert cc.pip_size("USDJPY") == 0.01
    assert cc.pip_size("GOLD") == 0.1
    assert cc.pip_size("US500") == 1.0


def test_gold_slippage_is_pips_not_price_x10000():
    # 0.42 price units on gold = 4.2 pips (was reported as 4200 before the fix)
    slip = abs(4034.90 - 4034.48) / cc.pip_size("GOLD")
    assert round(slip, 1) == 4.2


# --- Capital PnL string parsing ------------------------------------------------

def test_parse_pnl_currency_prefixed():
    assert cc._parse_pnl("EUR-12.34") == -12.34
    assert cc._parse_pnl("USDd37.5") == 37.5
    assert cc._parse_pnl(5) == 5.0
    assert cc._parse_pnl(None) is None
    assert cc._parse_pnl("") is None


# --- Capital transaction -> realized record (exact row shapes observed live) -----

GOLD_TRADE_ROW = {
    "date": "2026-07-02T01:46:31.281", "dateUtc": "2026-07-01T23:46:31.281",
    "instrumentName": "GOLD", "transactionType": "TRADE", "note": "Trade closed",
    "reference": "132126428272116", "size": "-80.43", "currency": "USDd",
    "status": "PROCESSED", "dealId": "00601567-0055-311e-0000-000084d60a14",
}
GOLD_SWAP_ROW = {
    "date": "2026-07-01T23:05:26.293", "dateUtc": "2026-07-01T21:05:26.293",
    "instrumentName": "GOLD", "transactionType": "SWAP", "note": "Overnight fee",
    "reference": "132116293832863", "size": "4.08", "currency": "USDd",
    "status": "PROCESSED",
}


def test_txn_trade_row_maps_to_realized_loss():
    rec, why = cc.txn_to_realized(GOLD_TRADE_ROW)
    assert why == "ok"
    assert rec["profit"] == -80.43 and rec["win"] is False
    assert rec["epic"] == "GOLD" and rec["reference"] == "132126428272116"
    assert rec["ts"] == "2026-07-01T23:46:31.281"


def test_txn_swap_row_is_not_a_trade():
    rec, why = cc.txn_to_realized(GOLD_SWAP_ROW)
    assert rec is None and why == "non_trade"


def test_txn_prefers_profit_and_loss_field_when_present():
    row = dict(GOLD_TRADE_ROW, profitAndLoss="USDd12.5")
    rec, _ = cc.txn_to_realized(row)
    assert rec["profit"] == 12.5 and rec["win"] is True


def test_txn_without_reference_skipped():
    row = dict(GOLD_TRADE_ROW)
    del row["reference"], row["dealId"]
    rec, why = cc.txn_to_realized(row)
    assert rec is None and why == "no_ref"


# --- FX schedule guard ----------------------------------------------------------

def test_market_guard_saturday_closed():
    sat = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)
    allowed, _ = market_execution_allowed(sat)
    assert not allowed


def test_market_guard_midweek_open():
    wed = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    allowed, _ = market_execution_allowed(wed)
    assert allowed


def test_market_guard_maintenance_window_closed():
    wed_2100 = datetime(2026, 7, 1, 21, 0, tzinfo=UTC)
    allowed, _ = market_execution_allowed(wed_2100)
    assert not allowed


# --- drawdown breakers ------------------------------------------------------------

def _rows(*day_balance: tuple[str, float]) -> list[dict]:
    return [{"date": d, "balance": b} for d, b in day_balance]


def test_daily_breaker_trips_on_big_loss():
    rows = _rows(("2026-06-30", 100_000.0), ("2026-07-01", 100_000.0))
    ok, msg = daily_drawdown_check(rows, 97_000.0, -2.0, today="2026-07-02")
    assert not ok and "DAILY DD BREAKER" in msg


def test_daily_breaker_allows_small_loss():
    rows = _rows(("2026-07-01", 100_000.0))
    ok, _ = daily_drawdown_check(rows, 99_500.0, -2.0, today="2026-07-02")
    assert ok


def test_daily_breaker_ignores_today_snapshot():
    # today's pre-run row must not be used as the reference (it IS the current bal)
    rows = _rows(("2026-07-01", 100_000.0), ("2026-07-02", 97_000.0))
    ok, _ = daily_drawdown_check(rows, 97_000.0, -2.0, today="2026-07-02")
    assert not ok


def test_peak_breaker_trips_below_peak_budget():
    rows = _rows(("2026-06-01", 100_000.0), ("2026-07-01", 93_000.0))
    ok, msg, stats = peak_drawdown_check(rows, 91_000.0, -8.0)
    assert not ok and stats["peak"] == 100_000.0


def test_peak_breaker_ok_within_budget():
    rows = _rows(("2026-06-01", 100_000.0))
    ok, _, stats = peak_drawdown_check(rows, 99_000.0, -8.0)
    assert ok and stats["dd_pct"] == -1.0


def test_breakers_permissive_without_history():
    ok_d, _ = daily_drawdown_check([], 100.0, -2.0)
    ok_p, _, _ = peak_drawdown_check([], 0.0, -8.0)
    assert ok_d and ok_p
