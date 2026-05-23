# Walk-Forward Lookback Audit

Tests whether the FROZEN per-pair best lookback selection (chosen once on full
2019-2025 OOS) generalizes forward, vs annual refit on rolling 3-year train.

Frozen lookbacks: {'EURUSD': 5, 'GBPUSD': 3, 'USDJPY': 10, 'AUDUSD': 21, 'NZDUSD': 10, 'USDCAD': 3}

## Per-year portfolio Sharpe : walk-forward vs frozen
| year | WF Sharpe | frozen Sharpe | Δ (WF-frozen) | best LB picks (WF) |
|---|---:|---:|---:|---|
| 2022 | -0.69 | -1.13 | +0.43 | EURUSD:10, GBPUSD:10, USDJPY:10, AUDUSD:10, NZDUSD:10, USDCAD:10 |
| 2023 | +0.87 | +1.13 | -0.25 | EURUSD:10, GBPUSD:10, USDJPY:3, AUDUSD:5, NZDUSD:5, USDCAD:3 |
| 2024 | -0.59 | +0.52 | -1.11 | EURUSD:10, GBPUSD:10, USDJPY:5, AUDUSD:5, NZDUSD:5, USDCAD:3 |
| 2025 | +0.89 | +2.37 | -1.48 | EURUSD:5, GBPUSD:10, USDJPY:5, AUDUSD:3, NZDUSD:5, USDCAD:21 |

Average Δ Sharpe: **-0.60**

## Lookback drift per pair across years (WF picks)
| pair | 2022 | 2023 | 2024 | 2025 | frozen | stable? |
|---|---:|---:|---:|---:|---:|---|
| EURUSD | 10 | 10 | 10 | 5 | 5 | DRIFT |
| GBPUSD | 10 | 10 | 10 | 10 | 3 | yes |
| USDJPY | 10 | 3 | 5 | 5 | 10 | DRIFT |
| AUDUSD | 10 | 5 | 5 | 3 | 21 | DRIFT |
| NZDUSD | 10 | 5 | 5 | 5 | 10 | DRIFT |
| USDCAD | 10 | 3 | 3 | 21 | 3 | DRIFT |

## VERDICT: FROZEN WINS : frozen is more stable, do NOT refit

Operational implication:
- Keep frozen lookbacks indefinitely
- Annual retraining adds noise, hurts forward performance
- Simplifies ops dramatically
