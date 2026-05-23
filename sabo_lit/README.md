# sabo_lit

Research and deployment sandbox for a retail prop-firm FX trading system.

The original LIT/SMC architecture was abandoned after validation. The current
deployable system is a systematic FX daily mean-reversion stack built for
multiple CFD prop-firm accounts.

## Current Production Stack

- `FX_MR_STACK`: six FX majors, equal-weight daily mean reversion.
- `NO_EUR_STACK`: same stack without EURUSD.
- `COMDOLL_STACK`: AUDUSD, NZDUSD, USDCAD.
- Sizing: `ADAPTIVE_75_50`
  - rolling 126-day realized Sharpe > `0.3` → 100% base vol target
  - rolling Sharpe between `0` and `0.3` → 75%
  - rolling Sharpe < `0` → 50%
- Base vol target: `10%`
- Max leverage cap: `10x`
- Execution cadence: daily after UTC 22:00 close.

## Quickstart

Run from repo root:

```bash
# 1) Audit firm-rule compatibility
/Users/oscarmischler/sabo-quant/.venv/bin/python sabo_lit/sandbox/firm_rule_audit.py

# 2) Generate daily signals
/Users/oscarmischler/sabo-quant/.venv/bin/python sabo_lit/sandbox/propfirm_signal_generator.py \
  --adaptive \
  --broker default_mt5 \
  --positions sabo_lit/sandbox/propfirm_positions.example.json

# 3) Or run full daily dry-run workflow
/Users/oscarmischler/sabo-quant/.venv/bin/python sabo_lit/sandbox/daily_ops_runner.py \
  --broker default_mt5

# 4) Dry-run MT5 delta execution
/Users/oscarmischler/sabo-quant/.venv/bin/python sabo_lit/sandbox/mt5_connector.py \
  --delta-csv sabo_lit/sandbox/live/prop_delta_orders_latest.csv

# 5) Run offline 30-day demo harness
/Users/oscarmischler/sabo-quant/.venv/bin/python sabo_lit/sandbox/demo_30day_harness.py
```

Read the live procedure first:

```text
sabo_lit/sandbox/PRODUCTION_RUNBOOK.md
```

## File Index

### Live Operation

- `sandbox/propfirm_signal_generator.py`: daily target and delta order generator.
- `sandbox/live_news_calendar.py`: Trading Economics calendar API with fallback cache.
- `sandbox/propfirm_news_calendar.py`: static fallback FOMC/NFP/ECB/BoE/BoC calendar.
- `sandbox/live_tracker.py`: daily P&L, slippage, and breach monitor.
- `sandbox/daily_ops_runner.py`: one-command daily dry-run workflow and GO/NO-GO report.
- `sandbox/telegram_notifier.py`: Telegram Bot API daily summaries and failure alerts.
- `sandbox/dashboard_generator.py`: static HTML dashboard at `sandbox/live/dashboard.html`.
- `sandbox/mt5_connector.py`: minimal MT5 dry-run/live delta-order connector.
- `sandbox/firm_rule_audit.py`: firm-rule compatibility simulator.
- `sandbox/demo_30day_harness.py`: offline 30-day paper-trade rehearsal.
- `sandbox/propfirm_hybrid_8x200_accounts.json`: current 8-account production template.
- `sandbox/broker_symbol_map.json`: broker symbol templates.
- `sandbox/broker_symbol_map.md`: broker calibration procedure.
- `sandbox/PRODUCTION_RUNBOOK.md`: daily/weekly/monthly live runbook.

### Strategy and Validation

- `sandbox/strategy_propfirm_cashmax.py`: FX strategy universe and prop-firm cash optimizer.
- `sandbox/historical_propfirm_simulation_adaptive.py`: 7-year adaptive prop-firm simulation.
- `sandbox/adaptive_2010_h1_validation.py`: 2010-2025 H1 adaptive robustness check.
- `sandbox/adaptive_grid_walkforward.py`: adaptive parameter grid walk-forward.
- `sandbox/validate_block_bootstrap.py`: autocorrelation/block-bootstrap validation.
- `sandbox/validate_fx_baseline.py`: baseline FX strategy validation.
- `sandbox/multi_asset_adaptive.py`: FX + crypto adaptive research check.
- `sandbox/state.md`: full research log and audit history.

## Validation Summary

Nine audit passes built the current system:

- SMC/LIT discretionary concepts dropped after failing validation.
- FX daily mean-reversion stack selected as the only robust core.
- EURUSD single-pair strategy dropped after held-out Q4 2025 failure.
- Three production specs retained: `FX_MR_STACK`, `NO_EUR_STACK`, `COMDOLL_STACK`.
- Bootstrap, null-distribution, block-bootstrap, walk-forward, and H1 2010+ checks run.
- `ADAPTIVE_75_50` selected as the least aggressive adaptive sizing layer.
- Operational scripts added for firm rules, news, broker mapping, MT5 dry-run, and demo harness.

## Honest Limitations

- The edge is regime-dependent; 2023-2025 was much stronger than 2010-2022.
- Full 2010 H1 validation does not prove adaptive sizing is a universal alpha enhancer.
- Prop-firm returns are path-dependent; one rule breach can erase a good strategy.
- Broker symbol maps are templates until exported from the exact account platform.
- News rules, payout gates, consistency rules, and inactivity rules can change.
- Crypto is not production-approved; recent OOS failed the add-to-production gate.

## Deployment Stance

This is not a “set and forget” trading bot. It is a controlled prop-firm
execution pipeline. Start with demo and one challenge only. Scale after real
fills, real slippage, and first payout validate the model.
