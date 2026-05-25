from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import requests


_PROJECT_PARENT = Path(__file__).resolve().parents[3]
if str(_PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_PARENT))

from sabo_lit.sandbox import capital_connector as cc  # noqa: E402
from sabo_lit.sandbox import telegram_notifier as tn  # noqa: E402


def http_error(text: str) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = 400
    response._content = text.encode("utf-8")
    return requests.HTTPError(response=response)


def test_detects_guaranteed_stop_required_error() -> None:
    err = http_error('{"errorCode":"error.vallidation.guaranteed-stop-loss.required"}')

    assert cc.is_guaranteed_stop_required_error(err)


def test_guaranteed_stop_distance_uses_market_rule_with_buffer() -> None:
    market = {
        "dealingRules": {
            "minGuaranteedStopDistance": {"value": 1, "unit": "PERCENTAGE"},
        },
        "snapshot": {"bid": 1.1999, "offer": 1.2001},
    }

    assert cc.guaranteed_stop_distance("EURUSD", market, 1.2, buffer=1.25) == 0.015


def test_open_position_sends_guaranteed_stop_payload(monkeypatch) -> None:
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"dealReference": "o_test"}

    def fake_post(url: str, json: dict, headers: dict, timeout: int) -> FakeResponse:
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(cc.requests, "post", fake_post)
    client = cc.CapitalClient("key", "identifier", "password", "demo")
    client.cst = "cst"
    client.security_token = "token"

    resp = client.open_position(
        "EURUSD",
        "BUY",
        1000,
        guaranteed_stop=True,
        stop_distance=0.015,
    )

    assert resp == {"dealReference": "o_test"}
    assert captured["json"] == {
        "epic": "EURUSD",
        "direction": "BUY",
        "size": 1000,
        "guaranteedStop": True,
        "stopDistance": 0.015,
    }
    assert "forceOpen" not in captured["json"]


def test_telegram_ignores_stale_saturday_market_state_after_sunday_open() -> None:
    now = datetime(2026, 5, 24, 23, 11, tzinfo=UTC)
    state = {"market_open": False, "market_reason": "Saturday FX market closed"}

    emoji, line = tn.fmt_market_status(state, now)

    assert emoji == "\U0001f7e2"
    assert "OUVERT" in line
    assert tn.is_weekend_mode(state, now) is False
