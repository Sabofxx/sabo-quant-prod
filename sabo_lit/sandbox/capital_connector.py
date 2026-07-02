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
import math
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests


HERE = Path(__file__).parent
LIVE_DIR = HERE / "live"
TRADE_LOG = LIVE_DIR / "capital_executions.jsonl"
SLIPPAGE_LOG = LIVE_DIR / "slippage.jsonl"
REALIZED_LOG = LIVE_DIR / "realized_pnl.jsonl"  # per-trade realized PnL booked at close
GSL_REQUIRED_ERROR_CODE = "error.vallidation.guaranteed-stop-loss.required"
GSL_MODE_DEFAULT = "auto"  # off | auto | on
GSL_DISTANCE_BUFFER_DEFAULT = 2.0
GSL_FALLBACK_DISTANCE_PCT_DEFAULT = 5.0
GSL_STOPLOSS_RETRY_MULTIPLIER_DEFAULT = 2.0
# Guaranteed stops are mandatory on this account, but Capital's minimum distance
# (×buffer) sits well inside the daily range, so normal intraday noise clips the
# stop and books a guaranteed loss on positions meant to be held ~24h. Floor the
# stop at a multiple of daily ATR so it only triggers on genuine adverse moves.
GSL_ATR_PERIOD_DEFAULT = 14
GSL_ATR_MULT_DEFAULT = 1.5  # 0 disables the ATR floor (falls back to exchange min)

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
    # Non-FX instruments. Internal symbol -> Capital.com epic. This is the generic
    # "add an instrument" path. Epic strings are best-guess and MUST be confirmed
    # by the dry-run (strategy_runner logs the resolved epic and exits loud if the
    # epic is not found on the account — see EPIC CONFIRM).
    "XAUUSD": "GOLD",
    "US500": "US500",
    "US100": "US100",
    "DE40": "DE40",
    "OILWTI": "OIL_CRUDE",
    "OILBRENT": "OIL_BRENT",
    "US10Y": "US10YT",
    "COPPER": "COPPER",
}


# Price value of "1 pip" per epic — slippage in FX-style pips is meaningless for
# metals/indices/commodities, so each non-FX epic declares its own pip unit.
# FX default: 0.0001 (0.01 for JPY crosses).
PIP_SIZE = {
    "GOLD": 0.1,        # XAUUSD quoted ~4000.0 -> pip = 10 cents
    "SILVER": 0.01,
    "US500": 1.0,       # index points
    "US100": 1.0,
    "DE40": 1.0,
    "OIL_CRUDE": 0.01,
    "OIL_BRENT": 0.01,
    "US10YT": 0.01,
    "COPPER": 0.001,
}


def pip_size(epic: str) -> float:
    if epic in PIP_SIZE:
        return PIP_SIZE[epic]
    return 0.01 if "JPY" in epic else 0.0001


def set_live_dir(path) -> None:
    """Redirect all connector log files to an isolated per-strategy live/ dir.

    The multi-account runner calls this before executing so each strategy's
    executions/slippage/realized PnL stay fully separate. No shared state.
    """
    global LIVE_DIR, TRADE_LOG, SLIPPAGE_LOG, REALIZED_LOG
    from pathlib import Path as _P
    LIVE_DIR = _P(path)
    TRADE_LOG = LIVE_DIR / "capital_executions.jsonl"
    SLIPPAGE_LOG = LIVE_DIR / "slippage.jsonl"
    REALIZED_LOG = LIVE_DIR / "realized_pnl.jsonl"


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

    def list_accounts(self) -> list[dict]:
        r = requests.get(f"{self.base}/accounts", headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json().get("accounts", [])

    def account_preferences(self) -> dict:
        r = requests.get(f"{self.base}/accounts/preferences", headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json()

    def switch_account(self, account_id: str) -> None:
        """Switch the active account on this session (Cas A: one login, many accounts)."""
        r = requests.put(f"{self.base}/session", json={"accountId": account_id},
                         headers=self._headers(), timeout=15)
        r.raise_for_status()
        self.account_id = account_id
        # Refresh CST/token if the broker rotated them on switch
        if r.headers.get("CST"):
            self.cst = r.headers["CST"]
        if r.headers.get("X-SECURITY-TOKEN"):
            self.security_token = r.headers["X-SECURITY-TOKEN"]

    def active_account_id(self) -> str | None:
        """Authoritative active accountId from the live session (not cached)."""
        r = requests.get(f"{self.base}/session", headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json().get("accountId") or r.json().get("currentAccountId")

    def assert_active_account(self, expected: str) -> None:
        """Hard guard: refuse to proceed unless the LIVE session account == expected.

        Prevents placing an order on the wrong account after a switch. Verifies
        against the broker (active_account_id), not just the cached value.
        """
        if not expected:
            return  # no account pinned in config (Cas B single-account) -> skip
        live = self.active_account_id()
        if live != expected:
            raise RuntimeError(
                f"ACCOUNT MISMATCH: session active={live!r} but config expects={expected!r}. "
                f"Refusing to trade.")

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
        return market_mid_price(m)

    def get_daily_candles(self, epic: str, max_bars: int = 30) -> list[dict]:
        """Returns list of {snapshotTime, openPrice{bid,ask}, closePrice{}, ...}."""
        url = f"{self.base}/prices/{epic}"
        params = {"resolution": "DAY", "max": max_bars}
        r = requests.get(url, headers=self._headers(), params=params, timeout=15)
        r.raise_for_status()
        return r.json().get("prices", [])

    def get_transactions(self, from_iso: str, to_iso: str | None = None) -> list[dict]:
        """Capital transaction history (/history/transactions).

        Authoritative record of every closed deal — including positions taken
        out by a guaranteed stop intraday, which never pass through our reset
        close loop. Each row carries profitAndLoss, instrumentName, size,
        open/close level and a unique reference.
        """
        url = f"{self.base}/history/transactions"
        params: dict[str, str] = {"from": from_iso}
        if to_iso:
            params["to"] = to_iso
        r = requests.get(url, headers=self._headers(), params=params, timeout=20)
        r.raise_for_status()
        txns = r.json().get("transactions", [])
        if txns:
            return txns
        # Some Capital backends ignore from/to and only honor lastPeriod (seconds,
        # capped at 86400 = 24h). Fall back so a daily sync never silently misses
        # the last session's closes.
        r = requests.get(url, headers=self._headers(),
                         params={"lastPeriod": "86400"}, timeout=20)
        r.raise_for_status()
        return r.json().get("transactions", [])

    def get_deal_confirm_price(self, deal_reference: str | None) -> float | None:
        """Fetch executed fill price via /confirms/{dealReference}. Returns None on miss."""
        if not deal_reference:
            return None
        url = f"{self.base}/confirms/{deal_reference}"
        r = requests.get(url, headers=self._headers(), timeout=10)
        if not r.ok:
            return None
        body = r.json()
        lvl = body.get("level") or body.get("affectedDeals", [{}])[0].get("level")
        return float(lvl) if lvl else None

    def open_position(self, epic: str, direction: str, size: float,
                      guaranteed_stop: bool = False,
                      stop_distance: float | None = None) -> dict:
        url = f"{self.base}/positions"
        payload = {
            "epic": epic,
            "direction": direction,  # BUY | SELL
            "size": size,
            "guaranteedStop": guaranteed_stop,
        }
        if guaranteed_stop:
            if stop_distance is None or stop_distance <= 0:
                raise ValueError("guaranteed_stop requires positive stop_distance")
            payload["stopDistance"] = stop_distance
        r = requests.post(url, json=payload, headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json()

    def close_position(self, deal_id: str) -> dict:
        url = f"{self.base}/positions/{deal_id}"
        r = requests.delete(url, headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json()

    def close_all_positions(self) -> list[dict]:
        """Close every open position. Returns list of {deal_id, status, ...}.

        Realized PnL is NOT logged here — positions stopped out by a guaranteed
        stop intraday never reach this loop, so it would undercount losses.
        sync_realized_from_transactions() pulls the authoritative PnL for every
        close (ours + stop-outs) from Capital's transaction history instead.
        """
        results = []
        for p in self.get_positions():
            pos = p.get("position", {})
            market = p.get("market", {})
            deal_id = pos.get("dealId")
            epic = market.get("epic", "?")
            if not deal_id:
                continue
            try:
                profit = float(pos.get("profit", 0) or 0)
                resp = self.close_position(deal_id)
                results.append({
                    "deal_id": deal_id,
                    "epic": epic,
                    "status": "closed",
                    "reference": resp.get("dealReference"),
                })
                print(f"  CLOSED {epic} {deal_id} pnl={profit:+.2f}")
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


def market_mid_price(market: dict) -> float:
    snap = market["snapshot"]
    bid = float(snap["bid"])
    ofr = float(snap["offer"])
    return (bid + ofr) / 2.0


def _rule_value_and_unit(rule: object) -> tuple[float | None, str]:
    if isinstance(rule, dict):
        raw_value = rule.get("value")
        unit = str(rule.get("unit") or "POINTS").upper()
    else:
        raw_value = rule
        unit = "POINTS"
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None, unit
    if value <= 0:
        return None, unit
    return value, unit


def _distance_from_rule_value(value: float, unit: str, price: float, epic: str) -> float:
    if unit in {"PERCENTAGE", "PERCENT"}:
        return price * value / 100.0
    if unit in {"PIPS", "PIP"}:
        return value * (0.01 if "JPY" in epic else 0.0001)
    return value


def ceil_price_distance(distance: float, epic: str) -> float:
    decimals = 3 if "JPY" in epic else 5
    factor = 10 ** decimals
    return math.ceil(distance * factor) / factor


def _candle_mid(node: object) -> float | None:
    """Mid of a candle price node {bid, ask} (or scalar). None if unusable."""
    if isinstance(node, dict):
        bid = node.get("bid")
        ask = node.get("ask", node.get("offer"))
        vals = [float(v) for v in (bid, ask) if v is not None]
        return sum(vals) / len(vals) if vals else None
    if isinstance(node, (int, float)):
        return float(node)
    return None


def compute_atr(candles: list[dict], period: int = GSL_ATR_PERIOD_DEFAULT) -> float | None:
    """ATR in price units from DAY candles. None if not enough data."""
    rows = []
    for c in candles:
        hi = _candle_mid(c.get("highPrice"))
        lo = _candle_mid(c.get("lowPrice"))
        cl = _candle_mid(c.get("closePrice"))
        if hi is None or lo is None or cl is None:
            continue
        rows.append((hi, lo, cl))
    if len(rows) < 2:
        return None
    trs = []
    for i in range(1, len(rows)):
        hi, lo, _ = rows[i]
        prev_close = rows[i - 1][2]
        trs.append(max(hi - lo, abs(hi - prev_close), abs(lo - prev_close)))
    if not trs:
        return None
    window = trs[-period:] if len(trs) >= period else trs
    return sum(window) / len(window)


def atr_stop_floor(client: CapitalClient, epic: str, period: int, mult: float,
                   cache: dict[str, float | None]) -> float | None:
    """ATR-based minimum guaranteed-stop distance for an epic (price units).

    Cached per epic so the daily-candle fetch happens once even though many
    accounts submit the same epic. Returns None on data/API failure (caller
    then falls back to the exchange-minimum distance).
    """
    if mult <= 0:
        return None
    if epic in cache:
        atr = cache[epic]
    else:
        try:
            atr = compute_atr(client.get_daily_candles(epic, max_bars=period + 5), period)
        except Exception as exc:
            print(f"    WARN: ATR fetch failed for {epic} ({exc}); using exchange min")
            atr = None
        cache[epic] = atr
    if not atr or atr <= 0:
        return None
    return atr * mult


def guaranteed_stop_distance(epic: str, market: dict, price: float,
                             buffer: float = GSL_DISTANCE_BUFFER_DEFAULT,
                             fallback_pct: float = GSL_FALLBACK_DISTANCE_PCT_DEFAULT,
                             atr_floor: float | None = None) -> float:
    rules = market.get("dealingRules") if isinstance(market, dict) else {}
    if not isinstance(rules, dict):
        rules = {}
    floor = atr_floor if (atr_floor and atr_floor > 0) else 0.0
    for key in (
        "minGuaranteedStopDistance",
        "minGuaranteedStopOrLimitDistance",
        "minStopOrLimitDistance",
        "minNormalStopOrLimitDistance",
    ):
        value, unit = _rule_value_and_unit(rules.get(key))
        if value is None:
            continue
        distance = _distance_from_rule_value(value, unit, price, epic) * max(buffer, 1.0)
        return ceil_price_distance(max(distance, floor), epic)
    fallback_distance = price * max(fallback_pct, 0.1) / 100.0
    return ceil_price_distance(max(fallback_distance, floor), epic)


def configured_gsl_mode() -> str:
    mode = os.environ.get("CAPITAL_GSL_MODE", GSL_MODE_DEFAULT).strip().lower()
    if mode not in {"off", "auto", "on"}:
        print(f"WARN: invalid CAPITAL_GSL_MODE={mode!r}; using {GSL_MODE_DEFAULT}", file=sys.stderr)
        return GSL_MODE_DEFAULT
    return mode


def configured_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"WARN: invalid {name}={raw!r}; using {default}", file=sys.stderr)
        return default


def is_guaranteed_stop_required_error(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    text = getattr(response, "text", "") or str(exc)
    if GSL_REQUIRED_ERROR_CODE in text:
        return True
    try:
        body = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return False
    return body.get("errorCode") == GSL_REQUIRED_ERROR_CODE


def is_stoploss_distance_error(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    text = getattr(response, "text", "") or str(exc)
    return "error.invalid.stoploss." in text


def append_log(record: dict) -> None:
    LIVE_DIR.mkdir(exist_ok=True)
    with TRADE_LOG.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _parse_pnl(raw: object) -> float | None:
    """Parse Capital profitAndLoss strings like 'EUR-12.34', '£5', '-3.2'."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    neg = "-" in s
    digits = "".join(ch for ch in s if ch.isdigit() or ch == ".")
    if not digits or digits == ".":
        return None
    try:
        val = float(digits)
    except ValueError:
        return None
    return -val if neg else val


def _logged_references() -> set[str]:
    refs: set[str] = set()
    if not REALIZED_LOG.exists():
        return refs
    for line in REALIZED_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ref = json.loads(line).get("reference")
            if ref:
                refs.add(str(ref))
        except json.JSONDecodeError:
            continue
    return refs


NON_TRADE_TYPES = {"DEPOSIT", "WITHDRAWAL", "INACTIVITY_FEE", "TRANSFER"}


def sync_realized_from_transactions(client: CapitalClient,
                                    lookback_days: int = 7,
                                    debug: bool = False) -> int:
    """Append any new closed-trade PnL from Capital's transaction history.

    Authoritative source covering BOTH our reset closes and positions taken out
    by a guaranteed stop intraday (which never reach close_all_positions).
    Deduplicated by transaction reference. Returns count of new rows.

    Always prints a fetch/skip breakdown: a silent 0 here previously hid a
    never-populated realized_pnl.jsonl for weeks (no win/loss stats at all).
    """
    now = datetime.now(UTC)
    from_iso = (now - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%S")
    to_iso = now.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        txns = client.get_transactions(from_iso, to_iso)
    except Exception as exc:
        print(f"WARN: realized PnL sync failed: {exc}")
        return 0

    seen = _logged_references()
    LIVE_DIR.mkdir(exist_ok=True)
    new = 0
    skipped = {"no_ref": 0, "dup": 0, "no_pnl": 0, "zero_pnl": 0, "non_trade": 0}
    type_counts: dict[str, int] = {}
    with REALIZED_LOG.open("a") as f:
        for t in txns:
            ttype = str(t.get("transactionType") or t.get("type") or "?")
            type_counts[ttype] = type_counts.get(ttype, 0) + 1
            ref = str(t.get("reference") or t.get("dealId") or "")
            if not ref:
                skipped["no_ref"] += 1
                continue
            if ref in seen:
                skipped["dup"] += 1
                continue
            if ttype.upper() in NON_TRADE_TYPES:
                skipped["non_trade"] += 1
                continue
            pnl = _parse_pnl(t.get("profitAndLoss"))
            if pnl is None:
                skipped["no_pnl"] += 1
                continue
            if pnl == 0.0:
                skipped["zero_pnl"] += 1
                continue
            rec = {
                "ts": t.get("dateUtc") or t.get("date") or now.isoformat(timespec="seconds"),
                "reference": ref,
                "epic": t.get("instrumentName") or t.get("epic"),
                "type": ttype,
                "size": _parse_pnl(t.get("size")),
                "open_level": t.get("openLevel"),
                "close_level": t.get("closeLevel"),
                "profit": round(pnl, 2),
                "win": pnl > 0,
                "currency": t.get("currency"),
            }
            f.write(json.dumps(rec, default=str) + "\n")
            seen.add(ref)
            new += 1
    print(f"Realized PnL sync: fetched={len(txns)} new={new} "
          f"skipped={ {k: v for k, v in skipped.items() if v} or '{}'} "
          f"types={type_counts or '{}'}")
    if debug and txns:
        print("  DEBUG first rows (raw keys/values, truncated):")
        for t in txns[:5]:
            print(f"    {json.dumps(t, default=str)[:400]}")
    return new


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
                       live_max_notional: float = LIVE_MAX_NOTIONAL_USD_DEFAULT,
                       expected_account_id: str = "") -> list[dict]:
    if not csv_path.exists():
        print(f"ERROR: delta CSV not found: {csv_path}", file=sys.stderr)
        sys.exit(3)

    # Account guard (MANDATORY, structural): no execution may act on the implicit
    # "active account". Every execution MUST declare an account_id and the live
    # session MUST match it. This neutralizes any base-credential caller that does
    # not pin an account (legacy runner, manual CLI, a resurrected cron).
    if not dry_run:
        if not expected_account_id:
            print("FATAL: refusing to execute without an explicit account_id "
                  "(no implicit active-account trading). Pass --account-id / set "
                  "account_id in the strategy config.", file=sys.stderr)
            sys.exit(7)
        try:
            client.assert_active_account(expected_account_id)
        except RuntimeError as exc:
            print(f"FATAL: {exc}", file=sys.stderr)
            sys.exit(7)

    # Live safety preflight: scan max single-order notional before any submit
    if client.base == LIVE_BASE and not dry_run:
        max_delta = 0.0
        with csv_path.open() as preview:
            for row in csv.DictReader(preview):
                max_delta = max(max_delta, abs(float(row.get("delta_notional_usd", 0) or 0)))
        assert_live_safety(client, max_delta, live_max_notional, allow_live)

    gsl_mode = configured_gsl_mode()
    gsl_buffer = configured_float_env("CAPITAL_GSL_DISTANCE_BUFFER", GSL_DISTANCE_BUFFER_DEFAULT)
    gsl_fallback_pct = configured_float_env(
        "CAPITAL_GSL_FALLBACK_DISTANCE_PCT",
        GSL_FALLBACK_DISTANCE_PCT_DEFAULT,
    )
    gsl_stoploss_retry_multiplier = configured_float_env(
        "CAPITAL_GSL_STOPLOSS_RETRY_MULTIPLIER",
        GSL_STOPLOSS_RETRY_MULTIPLIER_DEFAULT,
    )
    gsl_atr_mult = configured_float_env("CAPITAL_GSL_ATR_MULT", GSL_ATR_MULT_DEFAULT)
    gsl_atr_period = int(configured_float_env("CAPITAL_GSL_ATR_PERIOD", GSL_ATR_PERIOD_DEFAULT))
    atr_cache: dict[str, float | None] = {}
    hedging_mode: bool | None = None
    if not dry_run and gsl_mode in {"auto", "on"}:
        try:
            prefs = client.account_preferences()
            if isinstance(prefs.get("hedgingMode"), bool):
                hedging_mode = prefs["hedgingMode"]
            print(f"Capital account preferences: hedgingMode={hedging_mode}")
        except Exception as e:
            print(f"WARN: could not fetch Capital account preferences: {e}")

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
                market = client.get_market(epic)
                price = market_mid_price(market)
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
                    requested_gsl = gsl_mode == "on"
                    requested_stop_distance = None
                    if requested_gsl:
                        requested_stop_distance = guaranteed_stop_distance(
                            epic, market, price, gsl_buffer, gsl_fallback_pct,
                            atr_floor=atr_stop_floor(client, epic, gsl_atr_period,
                                                     gsl_atr_mult, atr_cache),
                        )
                        record["guaranteed_stop"] = True
                        record["stop_distance"] = requested_stop_distance
                    try:
                        resp = client.open_position(
                            epic,
                            direction,
                            size,
                            guaranteed_stop=requested_gsl,
                            stop_distance=requested_stop_distance,
                        )
                    except requests.HTTPError as first_error:
                        if (
                            gsl_mode == "auto"
                            and not requested_gsl
                            and is_guaranteed_stop_required_error(first_error)
                            and hedging_mode is not True
                        ):
                            requested_stop_distance = guaranteed_stop_distance(
                                epic, market, price, gsl_buffer, gsl_fallback_pct,
                                atr_floor=atr_stop_floor(client, epic, gsl_atr_period,
                                                         gsl_atr_mult, atr_cache),
                            )
                            print(
                                f"    RETRY {epic} with guaranteed stop "
                                f"distance={requested_stop_distance}"
                            )
                            try:
                                resp = client.open_position(
                                    epic,
                                    direction,
                                    size,
                                    guaranteed_stop=True,
                                    stop_distance=requested_stop_distance,
                                )
                            except requests.HTTPError as gsl_error:
                                if not is_stoploss_distance_error(gsl_error):
                                    raise
                                requested_stop_distance = ceil_price_distance(
                                    requested_stop_distance * max(gsl_stoploss_retry_multiplier, 1.1),
                                    epic,
                                )
                                print(
                                    f"    RETRY {epic} with wider guaranteed stop "
                                    f"distance={requested_stop_distance}"
                                )
                                resp = client.open_position(
                                    epic,
                                    direction,
                                    size,
                                    guaranteed_stop=True,
                                    stop_distance=requested_stop_distance,
                                )
                            record["gsl_retry"] = True
                            record["guaranteed_stop"] = True
                            record["stop_distance"] = requested_stop_distance
                        elif requested_gsl and is_stoploss_distance_error(first_error):
                            requested_stop_distance = ceil_price_distance(
                                (requested_stop_distance or 0) * max(gsl_stoploss_retry_multiplier, 1.1),
                                epic,
                            )
                            print(
                                f"    RETRY {epic} with wider guaranteed stop "
                                f"distance={requested_stop_distance}"
                            )
                            resp = client.open_position(
                                epic,
                                direction,
                                size,
                                guaranteed_stop=True,
                                stop_distance=requested_stop_distance,
                            )
                            record["gsl_retry"] = True
                            record["guaranteed_stop"] = True
                            record["stop_distance"] = requested_stop_distance
                        else:
                            raise
                    record["status"] = "submitted"
                    record["deal_reference"] = resp.get("dealReference")
                    # Slippage measurement : fetch executed price from /confirms
                    try:
                        time.sleep(0.5)  # broker propagation
                        fill_price = client.get_deal_confirm_price(resp.get("dealReference"))
                        if fill_price:
                            slip_pips = abs(fill_price - price) / pip_size(epic)
                            slip_record = {
                                "ts": record["ts"], "epic": epic, "direction": direction,
                                "expected_price": price, "fill_price": fill_price,
                                "slippage_pips": round(slip_pips, 2), "size": size,
                            }
                            with SLIPPAGE_LOG.open("a") as sf:
                                sf.write(json.dumps(slip_record) + "\n")
                            record["fill_price"] = fill_price
                            record["slippage_pips"] = round(slip_pips, 2)
                    except Exception as slip_exc:
                        record["slippage_error"] = str(slip_exc)[:100]
                except requests.HTTPError as e:
                    record["status"] = "error"
                    record["error"] = f"{e.response.status_code}: {e.response.text[:300]}"
                    if is_guaranteed_stop_required_error(e):
                        if gsl_mode == "off":
                            record["error"] += " | CAPITAL_GSL_MODE=off blocks required guaranteed stop"
                            record["abort_execution"] = True
                        elif hedging_mode is True:
                            record["error"] += " | hedgingMode=true blocks guaranteedStop per Capital API"
                            record["abort_execution"] = True
                    print(f"    ERROR: {record['error']}")
                except Exception as e:
                    record["status"] = "error"
                    record["error"] = str(e)
                    print(f"    ERROR: {e}")
            executions.append(record)
            append_log(record)
            if record.get("abort_execution"):
                print("  ABORT: broker policy error applies to every remaining order")
                break
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
    parser.add_argument("--account-id", default="",
                        help="Pinned accountId. REQUIRED for --execute and --reset: "
                             "the live session must match it or the action is refused.")
    parser.add_argument("--list-accounts", action="store_true",
                        help="List all accountId / name / balance under this login")
    parser.add_argument("--check-account", metavar="EXPECTED_ACCOUNT_ID",
                        help="Login then assert active session account == EXPECTED. "
                             "exit 7 + 'ACCOUNT MISMATCH' on mismatch (proves the guard live).")
    parser.add_argument("--switch-account", metavar="ACCOUNT_ID",
                        help="Switch active account before --check-account (test the switch endpoint)")
    parser.add_argument("--sync-realized", action="store_true",
                        help="Append new closed-trade PnL from Capital transaction history")
    parser.add_argument("--realized-lookback-days", type=int, default=7)
    parser.add_argument("--debug-transactions", action="store_true",
                        help="With --sync-realized (or alone): dump raw transaction rows "
                             "to diagnose an empty realized_pnl.jsonl")
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

    if args.list_accounts:
        for a in client.list_accounts():
            bal = a.get("balance", {})
            print(f"  accountId={a.get('accountId')}  name={a.get('accountName')!r}  "
                  f"type={a.get('accountType')}  balance={bal.get('balance')} {a.get('currency')}"
                  f"{'  <- ACTIVE' if a.get('accountId') == client.account_id else ''}")
        return

    if args.switch_account:
        print(f"Switching active account -> {args.switch_account}")
        client.switch_account(args.switch_account)
        print(f"  session account now = {client.active_account_id()}")

    if args.check_account:
        try:
            client.assert_active_account(args.check_account)
            print(f"ACCOUNT OK: live session == expected ({args.check_account})")
            return
        except RuntimeError as exc:
            print(f"FATAL: {exc}", file=sys.stderr)
            sys.exit(7)

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

    if args.sync_realized or args.debug_transactions:
        n = sync_realized_from_transactions(client, args.realized_lookback_days,
                                            debug=args.debug_transactions)
        print(f"Synced {n} new realized trades → {REALIZED_LOG}")
        return

    if args.reset and not args.execute:
        # Standalone close-all is destructive -> require an explicit pinned account.
        if not args.account_id:
            print("FATAL: --reset (close-all) requires --account-id (no implicit "
                  "active-account action).", file=sys.stderr)
            sys.exit(7)
        try:
            client.assert_active_account(args.account_id)
        except RuntimeError as exc:
            print(f"FATAL: {exc}", file=sys.stderr)
            sys.exit(7)
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
                                        live_max_notional=args.live_max_notional,
                                        expected_account_id=args.account_id or "")
        n_errors = sum(1 for item in executions if item.get("status") == "error")
        print(f"\nExecuted {len(executions)} orders ({n_errors} errors). Log: {TRADE_LOG}")
        if n_errors and not args.dry_run:
            sys.exit(4)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
