# CLAUDE.md — sabo-quant

Guidance for Claude Code when working in this repo. Overrides `/Users/oscarmischler/CLAUDE.md` (which describes a different project, sabo-pro / MT5 scalping bot).

## Project overview

**sabo-quant** — Automated multi-strategy trading program on Capital.com demo accounts, run entirely from GitHub Actions (no Mac needed). One strategy = one config = one pinned broker account = one isolated state dir. Zero-cost free-tier stack. Telegram reporting.

**⚠️ PIVOT (June 2026): the current production pipeline is `strategy_runner.py`**, NOT the legacy FX mean-reversion pipeline. The legacy workflows `Daily Prop Firm Signal` and `Intraday Risk Check` are **disabled manually** on GitHub. See "Legacy pipeline (DISABLED)" below before touching `automated_runner.py`.

## Current production: Strategy Runner (multi-account)

- **Workflow**: `.github/workflows/strategy_runner.yml` — cron `20 22 * * *` (22:20 UTC daily), matrix over strategies, `max-parallel: 1`.
- **Entry point**: `sabo_lit/sandbox/strategy_runner.py --config <id> [--dry-run]`
- **Configs**: `sabo_lit/sandbox/configs/*.json` (`StrategyConfig` schema in `strategy_config.py`). Signals: `tsm` | `ma` | `donchian` | `carry` (`strategy_signals.py`, momentum family — NOT the legacy mean-reversion).
- **Active matrix**: `[gold, index]`.
  - `gold` — TSM on XAUUSD, vol target 3%, account `323733202891256094` (100k USDd demo), creds suffix `_GOLD`.
  - `index` — long-biased MA trend on US500/US100/DE40 with top-20% vol-regime filter, account `323735182871179550`, creds suffix `_INDEX`. **Status: OBSERVATION — trades demo daily to accumulate a live track record, NOT a validated edge** (residual gap-day tail risk documented in its config).
- **Per-strategy state**: `sabo_lit/sandbox/live/<strategy>/` — `automated_daily_pnl.jsonl` (1 row/day), `capital_executions.jsonl`, `slippage.jsonl`, `realized_pnl.jsonl`, `account_state.json`, `prop_delta_orders_latest.csv`, `dashboard_full.html`.
- **Account safety**: `account_id` pinned in config; session asserted == config BEFORE signal gen AND inside `execute_delta_csv` (defense in depth). Empty `account_id` + non-dry run → exit 7. Credential resolution: `secret_suffix` `""` = base `CAPITAL_*` (Cas A, switch accountId), `"_X"` = `CAPITAL_*_X` (Cas B, separate login).
- **Risk controls in run_once**: FX schedule guard + per-epic broker `marketStatus` (TRADEABLE) guard, daily DD breaker vs prior-day close (`DAILY_DD_BREAKER_PCT`, default -2%), peak DD breaker (`MAX_PEAK_DD_PCT`, default -8%), GSL with ATR floor (`CAPITAL_GSL_ATR_MULT`, default 1.5), idempotent `--reset` rebalance.
- **Telegram**: `run_once` sends a per-strategy daily summary and a ⛔️ alert on breaker trips; workflow sends ❌ on job failure. All silent no-op if `TELEGRAM_*` secrets absent.
- **Elimination criteria** (pre-registered per config, reported by `strategy_status.py <id>`): `min_trades` 30, `min_live_sharpe` 0.50, tracking ≥ 0.50× backtest, median slippage ≤ 1.5 pip (pip size is per-epic: GOLD=0.1, indices=1.0 — see `PIP_SIZE` in `capital_connector.py`), 60 observation days.
- **Monitoring**: each run appends a status recap to the GitHub run summary (`strategy_status.py`). Realized win/loss comes ONLY from `--sync-realized` (Capital transaction history — includes intraday GSL stop-outs the reset close-loop never sees).

### Ops workflows (workflow_dispatch)

| Workflow | Purpose |
|----------|---------|
| `Strategy Runner (multi-account)` | inputs: `strategy` (blank = all), `dry_run` (full chain, no order) |
| `Capital Maintenance` | `action`: `positions` / `account-info` / `sync-debug` (verbose realized-PnL sync + raw transaction dump) / `close-all` (requires `account_id`); `suffix`: `base` / `_GOLD` / `_INDEX` |
| `List Capital Accounts` | list accountIds under a login; `check_account` asserts the guard |
| `PR Smoke Test` | compileall + imports + `pytest sabo_lit/sandbox/test_units.py` + offline renders |

### Known operational gotchas

- The state-commit step MUST commit before rebasing and MUST fail loudly on push failure — a swallowed push rejection silently lost `live/index/` state for 2 weeks (June 2026). Don't reintroduce `git push || echo`.
- `actions/cache` with a fixed key never re-saves: the data cache key includes `github.run_id` (restore latest via `restore-keys`, save fresh each run). With a cold cache the runner bootstraps only ~200 daily bars from the broker — enough for lookback 60–100, thin for the 252d vol-rank filter.
- The local clone lags the remote (the bot commits state daily). `git pull` before reading `live/`.
- `data/` CSVs are gitignored; `*-m5-bid-*.csv` filenames may contain DAILY bars (filename kept for loader compatibility).

## File map

| What | Where |
|------|-------|
| Production runner (entry point) | `sabo_lit/sandbox/strategy_runner.py` |
| Strategy configs + schema | `sabo_lit/sandbox/configs/*.json`, `strategy_config.py` |
| Signal generator (momentum family) | `sabo_lit/sandbox/strategy_signals.py` |
| Broker connector (shared) | `sabo_lit/sandbox/capital_connector.py` |
| Per-strategy status recap | `sabo_lit/sandbox/strategy_status.py` |
| Telegram notifier | `sabo_lit/sandbox/telegram_notifier.py` |
| Rich dashboard (per strategy: `--live-dir live/<id>`) | `sabo_lit/sandbox/local_dashboard.py` |
| Unit tests (signals, breakers, pip math, guard) | `sabo_lit/sandbox/test_units.py` |
| Per-strategy live state | `sabo_lit/sandbox/live/<strategy>/` |
| Legacy live state (frozen) | `sabo_lit/sandbox/live/*.jsonl,*.json` |
| Historical CSV (cached) | `sabo_lit/sandbox/data/` (gitignored, GH Actions cached) |
| Workflows | `.github/workflows/{strategy_runner,capital_maintenance,list_accounts,pr_smoke}.yml` |
| Legacy workflows (disabled) | `.github/workflows/{daily_propfirm,intraday_risk}.yml` |
| Setup doc | `SETUP_AUTOMATION.md` |
| Architecture scaffold layers (docstring-only `__init__.py`) | `sabo_lit/{api,backtest,execution,risk,strategy,persistence,features,models,microstructure,filters}` — **do NOT delete**: hard-referenced in `governance/dependency_rules.py` as the canonical layer set with enforced import-edge rules; governance tests scan the real tree. Empty ≠ unused. |

## Risk controls & known cost structure

- **GSL is mandatory** on these Capital accounts (orders error `guaranteed-stop-loss.required` without it). `CAPITAL_GSL_MODE=off` therefore HALTS trading (every order rejected) — it is NOT "trade without a stop".
- The bleed diagnosed 2026-06 (legacy): +EV signal but guaranteed stops at `exchange_min×2` (~12-32 pip) inside the daily range → ~75% of positions clipped intraday. Fix = ATR floor (`CAPITAL_GSL_ATR_MULT`, default 1.5; 0 disables).
- Realized win/loss lives ONLY in `live/<strategy>/realized_pnl.jsonl` via `--sync-realized` (transaction history, dedup by reference) — close-loop logging would undercount losses (GSL stop-outs vanish before the daily reset). The sync prints a fetch/skip breakdown every run; a fetched>0/new=0 pattern means the filters are wrong — use `Capital Maintenance → sync-debug`.
- Tunable repo variables: `DAILY_DD_BREAKER_PCT`, `MAX_PEAK_DD_PCT`, `CAPITAL_GSL_MODE`, `CAPITAL_GSL_DISTANCE_BUFFER`, `CAPITAL_GSL_FALLBACK_DISTANCE_PCT`, `CAPITAL_GSL_STOPLOSS_RETRY_MULTIPLIER`, `CAPITAL_GSL_ATR_MULT`, `CAPITAL_GSL_ATR_PERIOD`.

## Required GitHub secrets

| Name | Purpose |
|------|---------|
| `CAPITAL_API_KEY` / `CAPITAL_IDENTIFIER` / `CAPITAL_API_PASSWORD` | Base login (Cas A / legacy account) |
| `CAPITAL_API_KEY_GOLD` / `CAPITAL_IDENTIFIER_GOLD` / `CAPITAL_API_PASSWORD_GOLD` | Gold strategy login (Cas B) |
| `CAPITAL_API_KEY_INDEX` / `CAPITAL_IDENTIFIER_INDEX` / `CAPITAL_API_PASSWORD_INDEX` | Index strategy login (Cas B) |
| `CAPITAL_ENVIRONMENT` | `demo` or `live` (default `demo`) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Notifier |
| `TRADINGVIEW_WORKER_URL` / `TRADINGVIEW_WORKER_READ_TOKEN` | Legacy TV filter (unused by strategy_runner) |

## Local commands

```bash
# Run a strategy locally (dry-run, needs CAPITAL_* env)
python sabo_lit/sandbox/strategy_runner.py --config gold --dry-run

# Status recap from committed state (no broker calls)
python sabo_lit/sandbox/strategy_status.py gold

# Unit tests
python -m pytest sabo_lit/sandbox/test_units.py -q

# Trigger the daily run / a dry-run
gh workflow run "Strategy Runner (multi-account)" -f dry_run=true
gh run watch

# Ops: probe positions / debug realized sync / close-all on a pinned account
gh workflow run "Capital Maintenance" -f action=positions -f suffix=base
gh workflow run "Capital Maintenance" -f action=sync-debug -f suffix=_GOLD
```

## Safety: live trading gate

`CAPITAL_ENVIRONMENT=live` is NOT enough. Live execution also requires `--allow-live` (or `ALLOW_LIVE=1`) AND each `delta_notional_usd` ≤ `--live-max-notional` (default $250k). Failsafe against a misconfigured demo-sized rebalance hitting a real account.

## Legacy pipeline (DISABLED — do not resurrect casually)

The original prod was a 6-pair FX mean-reversion pipeline (`ADAPTIVE_75_50` regime sizing) driven by `automated_runner.py` on cron 22:05 UTC (`daily_propfirm.yml`) with a 4h intraday risk check (`intraday_risk.yml`). Both workflows are **disabled manually** on GitHub since ~2026-06-16. Its state files live at the ROOT of `sabo_lit/sandbox/live/` (frozen). Its account is the base-credential login, account `321896722115941534` — it had 39 open positions when disabled; check/clean via `Capital Maintenance` (`positions` / `close-all`). The TradingView confirmation layer (`tradingview_filter.py`, `infra/cloudflare-worker/`) belongs to this pipeline. `automated_runner.py` still exports `market_execution_allowed()` used by strategy_runner.

## History / context for AI agents

- `sabo_lit/` was originally a strict framework scaffold (typed contracts per `sabo-conventions` skill). Plan never executed; those dirs hold docstring-only `__init__.py`.
- Real production lives in `sabo_lit/sandbox/`. "sandbox" is misleading — it IS prod.
- Initial broker was OANDA; pivoted to Capital.com after Cloudflare blocked the OANDA API from this region.
- `live_news_calendar.py` falls back to a hardcoded NFP/CPI/FOMC calendar when `TRADING_ECONOMICS_KEY` is not set. The fallback is the documented default.

## Code conventions (this repo)

- Single source of pipeline truth: `strategy_runner.py`. Other entry points (`strategy_status.py`, `local_dashboard.py`) are read-only consumers of `live/<strategy>/` state.
- All state files are JSONL append-only OR JSON snapshots. JSONL `*_pnl.jsonl` are date-keyed and capped 1/day in the writer.
- Sandbox-only edits: do NOT touch `sabo_lit/lit/`, `sabo_lit/core/`, `sabo_lit/governance/` without invoking the `sabo-conventions` skill — those carry typed-contract guarantees enforced by tests.

## Backtest scripts

`sabo_lit/sandbox/strategy_*.py` (except `strategy_runner/config/signals/status`) are standalone walk-forward / OOS scripts. Run directly with Python 3; they read `sabo_lit/sandbox/data/` CSVs and print to stdout.

## What NOT to do

- Do not commit `sabo_lit/sandbox/data/*.csv` (huge, GH Actions cached).
- Do not bypass the market guards — Capital rejects 100% of FX orders during the 20:55-21:10 UTC maintenance window; non-FX epics are gated by broker `marketStatus`.
- Do not remove the `--reset` flag from the executor — without it, hedging-mode accounts accumulate stacked positions.
- Do not weaken the state-commit step back to `git push || echo` (see Known operational gotchas).
- Do not paste any `CAPITAL_*` or `TELEGRAM_*` value on a shell command line (`gh secret set NAME` then paste on stdin, Ctrl+D).
