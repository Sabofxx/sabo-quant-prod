# Prop Firm Live Workflow — Hybrid 8×200k

Goal: generate target exposures for the current best portfolio (`hybrid_blend_8x200`) and execute manually or through a broker bridge.

## 1. Generate daily signals
Run after the daily close / data update:

```bash
/Users/oscarmischler/sabo-quant/.venv/bin/python sabo_lit/sandbox/propfirm_signal_generator.py
```

Outputs:

```text
sabo_lit/sandbox/live/prop_signals_latest.md
sabo_lit/sandbox/live/prop_signals_latest.json
sabo_lit/sandbox/live/prop_orders_latest.csv
```

## 2. Account state and lockouts
Optional state file:

```bash
/Users/oscarmischler/sabo-quant/.venv/bin/python sabo_lit/sandbox/propfirm_signal_generator.py \
  --state sabo_lit/sandbox/propfirm_account_state.example.json
```

If `manual_lock=true`, daily loss <= `-4%`, or overall loss <= `-8%`, the account is forced flat in the output.

## 3. Execution rule
The CSV contains target exposure, not delta orders.

Execution steps:

1. Read current broker positions.
2. Compute `delta = target_position - current_position`.
3. Send only the delta order.
4. Save fill price, slippage, spread and execution time.
5. Update the account state before the next run.

## 4. Sizing caveats
FX estimates include approximate units and standard lots.

CFD index symbols (`USTEC`, `USA30`) require the exact prop-firm contract multiplier before converting notional to lots/contracts.

Do not execute real money until:

- exact firm symbols are configured,
- CFD multipliers are verified,
- daily-loss calculation is understood for each firm,
- demo fills match expected target notionals.

