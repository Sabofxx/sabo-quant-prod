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
| Static dashboard generator | `sabo_lit/sandbox/dashboard_generator.py` |
| Live state files (JSONL/JSON) | `sabo_lit/sandbox/live/` |
| Historical CSV (cached) | `sabo_lit/sandbox/data/` (gitignored, GH Actions cached) |
| GitHub Actions workflow | `.github/workflows/daily_propfirm.yml` |
| Setup doc | `SETUP_AUTOMATION.md` |
| Research scaffolding (mostly empty) | `sabo_lit/{api,backtest,execution,data,risk,strategy,...}` |

## Pipeline flow (`automated_runner.py`)

1. `check_env()` — verify CAPITAL_* secrets present, exit 2 if not
2. `CapitalClient.login()` — OAuth-style session, store CST + X-SECURITY-TOKEN
3. `log_account_snapshot()` — append to `live/automated_daily_pnl.jsonl` (capped 1/day)
4. Loop 6 pairs → `get_daily_candles()` → `update_historical_csv()` (bootstrap if missing else append)
5. `propfirm_signal_generator.py --adaptive` → writes `live/prop_signals_latest.{json,md,csv}` + `prop_delta_orders_latest.csv`
6. `market_execution_allowed()` — guard FX schedule (Fri 20:59 → Sun 21:00 closed, daily 20:55-21:10 maintenance)
7. If allowed: `capital_connector.py --execute --reset --min-notional 500` (close all then open fresh — idempotent rebalance)
8. `live_tracker.py` refresh
9. `log_account_snapshot()` again (post-execution balance)
10. `write_account_state()` — rich `live/account_state.json` (balance, positions, market, env)
11. `dashboard_generator.py` → `live/dashboard.html`
12. `telegram_notifier.py --run-summary` (sends rich Telegram message)

GitHub Actions then `git commit -m "Daily run YYYY-MM-DD"` on `live/` deltas + `git push`.

## Required GitHub secrets

| Name | Source | Purpose |
|------|--------|---------|
| `CAPITAL_API_KEY` | Capital.com Settings → API integration | Auth header `X-CAP-API-KEY` |
| `CAPITAL_IDENTIFIER` | Capital.com login email | Session login identifier |
| `CAPITAL_API_PASSWORD` | Custom API password (NOT login pwd) | Session login password |
| `CAPITAL_ENVIRONMENT` | `demo` or `live` (default `demo`) | Selects base URL |
| `TELEGRAM_BOT_TOKEN` | @BotFather `/newbot` | Notifier auth |
| `TELEGRAM_CHAT_ID` | @userinfobot | Notifier target chat |

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
