# Production config validation — pre-registered verdicts

OOS split: 2024-01-01. Costs: 1 full spread/position/day (daily --reset) + 0.3bp GSL premium.

| strategy | signal | verdict reset→skip | full Sharpe | OOS Sharpe | OOS CI95 | skip-reset full/OOS | maxDD | worst day | drag reset→skip |
|---|---|---|---:|---:|---|---|---:|---:|---|
| bonds | tsm | **DEAD → DEAD** | -0.87 | -1.96 | [-3.1, -0.82] | 0.35 / -0.63 | -35.84% | -2.85% | 6.95% → 0.31% |
| carry | carry | **DEAD → DEAD** | -0.72 | -0.81 | [-1.77, 0.12] | -0.16 / -0.33 | -38.5% | -2.51% | 3.86% → 0.53% |
| copper | donchian | **DEAD → DEAD** | -0.3 | -0.83 | [-1.63, 0.42] | -0.31 / -0.85 | -46.11% | -24.19% | 1.55% → 1.76% |
| fxtsm | tsm | **DEAD → DEAD** | -1.09 | -1.08 | [-2.16, -0.13] | -0.3 / -0.26 | -47.25% | -2.25% | 4.62% → 0.32% |
| gold | tsm | **DEAD → ROBUST** | 0.54 | 1.09 | [0.06, 2.1] | 0.79 / 1.36 | -10.2% | -1.53% | 1.12% → 0.08% |
| index | ma | **DEAD → DEAD** | 0.32 | 0.74 | [-0.42, 1.96] | 0.45 / 0.94 | -14.66% | -5.23% | 0.69% → 0.07% |
| oil | donchian | **DEAD → DEAD** | 0.55 | -0.21 | [-1.23, 1.07] | 0.54 / -0.23 | -17.69% | -5.87% | 1.1% → 1.17% |

## Portfolios (equal capital per account)

| combo | full Sharpe | OOS Sharpe | full maxDD | CI95 full |
|---|---:|---:|---:|---|
| gold_only[reset] | 0.54 | 1.09 | -10.2% | [-0.24, 1.14] |
| gold_only[skip] | 0.79 | 1.36 | -7.23% | [0.11, 1.32] |
| gold+bonds[reset] | -0.22 | -0.58 | -17.71% | [-0.86, 0.42] |
| gold+bonds[skip] | 0.71 | 0.43 | -4.83% | [0.11, 1.27] |
| gold+oil[reset] | 0.57 | 0.29 | -7.82% | [0.05, 0.94] |
| gold+oil[skip] | 0.63 | 0.39 | -6.55% | [0.13, 1.03] |
| gold+index[reset] | 0.55 | 1.19 | -8.44% | [-0.11, 1.13] |
| gold+index[skip] | 0.79 | 1.5 | -6.98% | [0.19, 1.36] |
| gold+bonds+oil[reset] | 0.35 | -0.49 | -11.58% | [-0.26, 0.76] |
| gold+bonds+oil[skip] | 0.67 | 0.11 | -6.74% | [0.2, 1.08] |
| gold+bonds+index[reset] | -0.03 | -0.21 | -11.53% | [-0.71, 0.62] |
| gold+bonds+index[skip] | 0.82 | 0.76 | -5.27% | [0.2, 1.39] |
| gold+bonds+oil+index[reset] | 0.41 | -0.28 | -6.87% | [-0.26, 0.8] |
| gold+bonds+oil+index[skip] | 0.75 | 0.35 | -5.4% | [0.34, 1.14] |
| all_seven[reset] | -0.23 | -1.08 | -16.99% | [-0.88, 0.27] |
| all_seven[skip] | 0.27 | -0.5 | -7.72% | [-0.31, 0.78] |
