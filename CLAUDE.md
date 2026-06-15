# CLAUDE.md — sabo-quant

Guidance for Claude Code when working in this repo. Overrides `/Users/oscarmischler/CLAUDE.md` (which describes a different project, sabo-pro / MT5 scalping bot).

## Project overview

**sabo-quant** — Automated FX prop-firm signal pipeline. Runs entirely on GitHub Actions (no Mac needed). Trades Capital.com demo (or live, with explicit gate) via REST API. Reports daily summaries to Telegram. Zero-cost free-tier stack.

- **Strategy**: mean-reversion + regime-aware sizing (`ADAPTIVE_75_50` tiers `[(0.3, 1.0), (0.0, 0.75), (-1e9, 0.5)]`)
- **Active instruments**: EURUSD, GBPUSD, USDJPY, AUDUSD, NZDUSD, USDCAD (daily bars from Capital.com)
- **Cadence**: cron `5 22 * * *` (22:05 UTC every day). Mon-Fri trade, Sat-Sun status-only.
- **Account model**: 8 prop-firm sub-accounts, deltas summed, single broker execution per epic.

## File map

| What | Where |
|------|-------|
| Production pipeline | `sabo_lit/sandbox/` (yes — sandbox is prod, see History below) |
| Broker connector | `sabo_lit/sandbox/capital_connector.py` |
| Daily runner (entry point) | `sabo_lit/sandbox/automated_runner.py` |
| Signal generator | `sabo_lit/sandbox/propfirm_signal_generator.py` |
| Telegram notifier | `sabo_lit/sandbox/telegram_notifier.py` |
| Static dashboard generator (pipeline, TradingView widgets) | `sabo_lit/sandbox/dashboard_generator.py` → `live/dashboard.html` |
| Rich local dashboard (equity/DD/realized/slippage/GSL stop-out) | `sabo_lit/sandbox/local_dashboard.py` → `live/dashboard_full.html` (`--serve` to open locally) |
| TradingView filter | `sabo_lit/sandbox/tradingview_filter.py` |
| TradingView webhook Worker | `infra/cloudflare-worker/` |
| Live state files (JSONL/JSON) | `sabo_lit/sandbox/live/` |
| Historical CSV (cached) | `sabo_lit/sandbox/data/` (gitignored, GH Actions cached) |
| GitHub Actions workflow | `.github/workflows/daily_propfirm.yml` |
| Intraday Telegram/risk workflow | `.github/workflows/intraday_risk.yml` |
| Setup doc | `SETUP_AUTOMATION.md` |
| Architecture scaffold layers (docstring-only `__init__.py`) | `sabo_lit/{api,backtest,execution,risk,strategy,persistence,features,models,microstructure,filters}` — **do NOT delete**: hard-referenced in `governance/dependency_rules.py` as the canonical layer set with enforced import-edge rules; governance tests scan the real tree. Empty ≠ unused. |

## Pipeline flow (`automated_runner.py`)

1. `check_env()` — verify CAPITAL_* secrets present, exit 2 if not
2. `CapitalClient.login()` — OAuth-style session, store CST + X-SECURITY-TOKEN
3. `log_account_snapshot()` — append to `live/automated_daily_pnl.jsonl` (capped 1/day)
4. Loop 6 pairs → `get_daily_candles()` → `update_historical_csv()` (bootstrap if missing else append)
5. `propfirm_signal_generator.py --adaptive` → writes `live/prop_signals_latest.{json,md,csv}` + `prop_delta_orders_latest.csv`
6. `tradingview_filter.py` — optional TradingView confirmation layer (`TV_FILTER_MODE=disabled` by default)
7. `market_execution_allowed()` — guard FX schedule (Fri 20:59 → Sun 21:00 closed, daily 20:55-21:10 maintenance)
7b. `circuit_breaker_check()` (same-day DD vs `DAILY_DD_BREAKER_PCT`) AND `peak_drawdown_check()` (DD from all-time equity peak vs `MAX_PEAK_DD_PCT`, default -8%) — either trips → skip execution
8. If allowed: `capital_connector.py --execute --reset --min-notional 500` (close all then open fresh — idempotent rebalance). On each close, realized PnL is appended to `live/realized_pnl.jsonl`. Guaranteed stops are mandatory on this account; distance floored at `max(exchange_min×buffer, ATR×CAPITAL_GSL_ATR_MULT)` so intraday noise can't clip a 24h hold.
9. `live_tracker.py` refresh
10. `log_account_snapshot()` again (post-execution balance)
11. `write_account_state()` — rich `live/account_state.json` (balance, positions, market, env)
12. `dashboard_generator.py` → `live/dashboard.html` (TradingView widgets); `local_dashboard.py` → `live/dashboard_full.html` (equity, drawdown, realized PnL, slippage, GSL stop-out)
13. `telegram_notifier.py --run-summary` (rich message: realized vs latent PnL, DD from peak, per-pair edge)

GitHub Actions then `git commit -m "Daily run YYYY-MM-DD"` on `live/` deltas + `git push`.

## Risk controls & known cost structure

- **GSL is mandatory** on this Capital account (every order errors `guaranteed-stop-loss.required` without it). `CAPITAL_GSL_MODE=off` therefore HALTS trading (every order rejected, execution aborts) — it is NOT "trade without a stop".
- The bleed diagnosed 2026-06: directional signal was +EV but guaranteed stops sat at `exchange_min×2` (~12-32 pip), inside the daily range → ~75% of positions clipped intraday for a guaranteed loss. Fix = ATR floor (`CAPITAL_GSL_ATR_MULT` default 1.5). Set `CAPITAL_GSL_ATR_MULT=0` to disable the floor.
- Telegram `PL ouvert` is **latent** (mark-to-market right after entry, includes entry spread) — not a trade result. Realized win/loss lives only in `live/realized_pnl.jsonl` (Capital's close response carries no PnL).
- Tunable repo variables: `MAX_PEAK_DD_PCT`, `CAPITAL_GSL_ATR_MULT`, `CAPITAL_GSL_ATR_PERIOD` (plus the existing `EXEC_MIN_NOTIONAL_USD`, `DAILY_DD_BREAKER_PCT`, `CAPITAL_GSL_*`).

## Telegram cadence

- Daily rebalance sends `telegram_notifier.py --run-summary` after the 22:05 UTC run.
- Intraday risk workflow runs every 4h on weekdays and now defaults to `RISK_STATUS_MODE=always`, so it sends a non-trading status heartbeat even when there is no breach.
- Set repo variable `RISK_STATUS_MODE=alert_only` to return to silent intraday checks unless a threshold is breached.
- Intraday status writes `sabo_lit/sandbox/live/intraday_risk_state.json`; breach events still append to `intraday_risk_alerts.jsonl`.

## Required GitHub secrets

| Name | Source | Purpose |
|------|--------|---------|
| `CAPITAL_API_KEY` | Capital.com Settings → API integration | Auth header `X-CAP-API-KEY` |
| `CAPITAL_IDENTIFIER` | Capital.com login email | Session login identifier |
| `CAPITAL_API_PASSWORD` | Custom API password (NOT login pwd) | Session login password |
| `CAPITAL_ENVIRONMENT` | `demo` or `live` (default `demo`) | Selects base URL |
| `TELEGRAM_BOT_TOKEN` | @BotFather `/newbot` | Notifier auth |
| `TELEGRAM_CHAT_ID` | @userinfobot | Notifier target chat |
| `TRADINGVIEW_WORKER_URL` | Cloudflare Worker URL | Optional pull of fresh TV confirmations |
| `TRADINGVIEW_WORKER_READ_TOKEN` | Cloudflare Worker secret | Optional auth for `/signals/today` |

## TradingView confirmation layer

Recommended architecture:

`TradingView alert -> Cloudflare Worker -> KV + live/tradingview_signals.jsonl -> daily SABO cron filter`

The Worker lives in `infra/cloudflare-worker/`. It accepts `POST /tradingview/webhook`, validates the shared secret or optional `X-Sabo-Sig` HMAC, deduplicates by `sha256(symbol|timeframe|side|time)`, stores the signal in KV for 36h, and can append accepted signals to `sabo_lit/sandbox/live/tradingview_signals.jsonl` through the GitHub Contents API.

The bot consumes confirmations through `tradingview_filter.py`. Modes:

- `TV_FILTER_MODE=disabled` — current default, no trading behavior change.
- `TV_FILTER_MODE=veto_only` — only blocks a SABO delta when TradingView confirms the opposite side.
- `TV_FILTER_MODE=require_confirm` — SABO delta executes only when TradingView confirms the same symbol and side.

Start in `disabled` for observation, then `veto_only`, then `require_confirm` after enough overlap checks. Because the Capital execution uses `--reset`, TradingView should remain a confirmation layer for the daily rebalance, not a separate intraday execution source.

## Local commands

```bash
# Test Capital login
CAPITAL_API_KEY=... CAPITAL_IDENTIFIER=... CAPITAL_API_PASSWORD=... \
  python sabo_lit/sandbox/capital_connector.py --account-info

# Test Telegram
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... \
  python sabo_lit/sandbox/telegram_notifier.py --test

# Preview Telegram daily message (no send)
python sabo_lit/sandbox/telegram_notifier.py --run-summary --print-only

# Dry-run rebalance
python sabo_lit/sandbox/capital_connector.py \
  --execute sabo_lit/sandbox/live/prop_delta_orders_latest.csv --dry-run

# Trigger GH workflow
gh workflow run "Daily Prop Firm Signal"
gh run watch
```

## Safety: live trading gate

`CAPITAL_ENVIRONMENT=live` is NOT enough. Live execution requires also:
- `--allow-live` flag (or `ALLOW_LIVE=1` env), AND
- Each individual `delta_notional_usd` ≤ `--live-max-notional` (default $250k)

Failsafe to prevent a misconfigured $3M demo rebalance from blowing a real account. To go live edit `automated_runner.py` execute step to pass `--allow-live` AND set GH secret `CAPITAL_ENVIRONMENT=live`.

## History / context for AI agents

- `sabo_lit/` was originally a strict framework scaffold (typed contracts per `sabo-conventions` skill — see `sabo_lit/api`, `risk`, `execution`, etc.). Plan never executed; those dirs hold docstring-only `__init__.py`.
- Real production lives in `sabo_lit/sandbox/`. "sandbox" is misleading — it IS prod.
- Initial broker was OANDA; pivoted to Capital.com after Cloudflare blocked the OANDA API from this region.
- M5 CSV files named `*-m5-bid-2019-01-01-2026-01-01.csv` may contain DAILY bars after Capital pivot (filename retained for compatibility with strategy modules that read them).
- `live_news_calendar.py` falls back to a hardcoded NFP/CPI/FOMC calendar when `TRADING_ECONOMICS_KEY` is not set. The fallback is the documented default.

## Code conventions (this repo)

- Single source of pipeline truth: `automated_runner.py`. Other entry points (`live_tracker.py`, `dashboard_generator.py`) are read-only consumers of `live/` state.
- All state files are JSONL append-only OR JSON snapshots. JSONL `*_pnl.jsonl` are date-keyed and capped 1/day in the writer.
- Sandbox-only edits: do NOT touch `sabo_lit/lit/`, `sabo_lit/core/`, `sabo_lit/governance/` without invoking the `sabo-conventions` skill — those carry typed-contract guarantees enforced by tests.

## Backtest scripts

`sabo_lit/sandbox/strategy_*.py` are standalone walk-forward / OOS scripts. Run directly with Python 3. They read CSV files from `sabo_lit/sandbox/data/`. No test framework — results print to stdout.

## What NOT to do

- Do not commit `sabo_lit/sandbox/data/*.csv` (huge, GH Actions cached).
- Do not bypass `market_execution_allowed()` — Capital rejects 100% of FX orders during the 20:55-21:10 UTC maintenance window.
- Do not remove the `--reset` flag from the executor — without it, hedging-mode accounts accumulate stacked positions.
- Do not paste any `CAPITAL_*` or `TELEGRAM_*` value on a shell command line (`gh secret set NAME` then paste on stdin, Ctrl+D).
