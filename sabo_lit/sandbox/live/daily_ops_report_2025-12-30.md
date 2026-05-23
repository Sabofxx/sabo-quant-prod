# Daily Ops Runner Report

Generated: 2026-05-22T15:36:16+00:00
As of: 2025-12-30
Broker map: `default_mt5`
Decision: **DEMO_OK_LIVE_NO_GO**

## Step Status

| step | status | detail |
|---|---|---|
| broker_selection | OK | broker forced by CLI: default_mt5 |
| broker_map | WARN | broker map default_mt5 is template-only; live not approved |
| news | OK | 2025-12-30 no blackout source=cache |
| positions | OK | 8 current positions loaded from /Users/oscarmischler/sabo-quant/sabo_lit/sandbox/propfirm_positions.example.json |
| signal_generation | OK | command completed |
| delta_csv | OK | 39 rows, $5,990,925 gross delta, max lots 3.56 |
| mt5_dry_run | OK | command completed |
| live_tracker | OK | command completed |

## Delta Summary

- Rows: `39`
- Gross delta: `$5,990,925`
- Max estimated lots/order: `3.56`
- Accounts: `A1, A2, A3, A4, A5, A6, A7, A8`
- Symbols: `AUDUSD, EURUSD, GBPUSD, NZDUSD, USDCAD, USDJPY`

## Commands

### signal_generation

- Return code: `0`
- Command: `/Users/oscarmischler/sabo-quant/.venv/bin/python /Users/oscarmischler/sabo-quant/sabo_lit/sandbox/propfirm_signal_generator.py --adaptive --as-of 2025-12-30 --accounts /Users/oscarmischler/sabo-quant/sabo_lit/sandbox/propfirm_hybrid_8x200_accounts.json --broker default_mt5 --broker-map /Users/oscarmischler/sabo-quant/sabo_lit/sandbox/broker_symbol_map.json --out-dir /Users/oscarmischler/sabo-quant/sabo_lit/sandbox/live --positions /Users/oscarmischler/sabo-quant/sabo_lit/sandbox/propfirm_positions.example.json`

```text
WARNING: broker map 'default_mt5' is not verified. Confirm symbols in platform before live.
As of: 2025-12-30
Accounts: 8
Orders (target): 39
Delta orders (execute): 39
Total target gross: $6500925
Total delta gross : $5990925
Wrote: /Users/oscarmischler/sabo-quant/sabo_lit/sandbox/live/prop_signals_2025-12-30.json
Wrote: /Users/oscarmischler/sabo-quant/sabo_lit/sandbox/live/prop_orders_2025-12-30.csv
Wrote: /Users/oscarmischler/sabo-quant/sabo_lit/sandbox/live/prop_signals_2025-12-30.md
Wrote: /Users/oscarmischler/sabo-quant/sabo_lit/sandbox/live/prop_delta_orders_2025-12-30.csv
```

### mt5_dry_run

- Return code: `0`
- Command: `/Users/oscarmischler/sabo-quant/.venv/bin/python /Users/oscarmischler/sabo-quant/sabo_lit/sandbox/mt5_connector.py --delta-csv /Users/oscarmischler/sabo-quant/sabo_lit/sandbox/live/prop_delta_orders_latest.csv --max-lots-per-order 5.0`

```text
Orders processed: 39 dry_run=True filled_lots=0.00 rejected=0
Fill log: /Users/oscarmischler/sabo-quant/sabo_lit/sandbox/live/mt5_fills.jsonl
```

### live_tracker

- Return code: `0`
- Command: `/Users/oscarmischler/sabo-quant/.venv/bin/python /Users/oscarmischler/sabo-quant/sabo_lit/sandbox/live_tracker.py --account-config /Users/oscarmischler/sabo-quant/sabo_lit/sandbox/propfirm_hybrid_8x200_accounts.json`

```text
Trades logged so far: 1
Daily P&L logged so far: 2
Active alerts: 0
Dashboard: /Users/oscarmischler/sabo-quant/sabo_lit/sandbox/live/tracker_dashboard.md
Alerts JSON: /Users/oscarmischler/sabo-quant/sabo_lit/sandbox/live/tracker_alerts.json
```

## Interpretation

- `DEMO_OK`: safe to use for demo/manual rehearsal.
- `DEMO_OK_LIVE_NO_GO`: files generated, but at least one live blocker/warning remains.
- `LIVE_NO_GO`: live was requested as strict, but warnings remain.
- `NO_GO`: fix failed step before execution.

This runner never sends live orders. Run `mt5_connector.py --live` separately only after demo validation.