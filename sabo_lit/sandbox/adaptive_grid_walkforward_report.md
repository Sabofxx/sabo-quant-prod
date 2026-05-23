# Adaptive Grid Walk-Forward

Tests if 126d / 0.5 / 0.0 / pause params are overfit by grid-searching
18 configurations across train (2019), val (2020-2022), OOS (2023-2025).

Grid : windows = [63, 126, 252], thresholds = [(0.3, 0.0), (0.5, 0.0), (0.5, 0.25)], hysteresis = [False, True]

## Best config per spec (by VAL Calmar)
| spec | best config | TRAIN Sh | VAL Sh | OOS Sh | TRAIN Calmar | VAL Calmar | OOS Calmar |
|---|---|---:|---:|---:|---:|---:|---:|
| FX_MR_STACK | W126_thr0.3_0.0_hystFalse | -0.65 | +0.10 | +1.37 | -0.48 | 0.07 | 1.13 |
| NO_EUR_STACK | W126_thr0.3_0.0_hystFalse | -0.93 | +0.10 | +1.18 | -0.43 | 0.07 | 0.95 |
| COMDOLL_STACK | STATIC | +0.43 | -0.53 | +1.27 | 0.48 | -0.17 | 1.10 |

## Full grid for FX_MR_STACK (all 18 configs + static)
| config | TRAIN Sh | VAL Sh | OOS Sh | TRAIN Calmar | VAL Calmar | OOS Calmar |
|---|---:|---:|---:|---:|---:|---:|
| STATIC | -0.64 | -0.70 | +1.32 | -0.45 | -0.18 | 0.87 |
| W126_thr0.3_0.0_hystFalse | -0.65 | +0.10 | +1.37 | -0.48 | 0.07 | 1.13 |
| W126_thr0.3_0.0_hystTrue | -0.73 | -0.08 | +1.26 | -0.49 | -0.04 | 1.12 |
| W126_thr0.5_0.0_hystFalse | -0.72 | -0.14 | +1.34 | -0.49 | -0.07 | 1.16 |
| W126_thr0.5_0.0_hystTrue | -0.71 | -0.34 | +1.22 | -0.48 | -0.14 | 1.15 |
| W126_thr0.5_0.25_hystFalse | -0.65 | -0.37 | +1.33 | -0.47 | -0.15 | 1.35 |
| W126_thr0.5_0.25_hystTrue | -0.55 | -0.50 | +1.19 | -0.44 | -0.18 | 1.28 |
| W252_thr0.3_0.0_hystFalse | -0.58 | -0.13 | +0.91 | -0.34 | -0.08 | 0.56 |
| W252_thr0.3_0.0_hystTrue | -0.58 | -0.11 | +0.91 | -0.33 | -0.07 | 0.61 |
| W252_thr0.5_0.0_hystFalse | -0.58 | -0.17 | +0.90 | -0.34 | -0.10 | 0.58 |
| W252_thr0.5_0.0_hystTrue | -0.58 | -0.18 | +0.91 | -0.33 | -0.11 | 0.62 |
| W252_thr0.5_0.25_hystFalse | -0.58 | -0.28 | +0.95 | -0.33 | -0.13 | 0.73 |
| W252_thr0.5_0.25_hystTrue | -0.58 | -0.43 | +0.98 | -0.33 | -0.15 | 0.77 |
| W63_thr0.3_0.0_hystFalse | -0.24 | -0.49 | +1.19 | -0.18 | -0.20 | 1.05 |
| W63_thr0.3_0.0_hystTrue | -0.34 | -0.51 | +0.78 | -0.32 | -0.22 | 0.50 |
| W63_thr0.5_0.0_hystFalse | -0.33 | -0.54 | +1.17 | -0.22 | -0.22 | 0.98 |
| W63_thr0.5_0.0_hystTrue | -0.46 | -0.47 | +0.76 | -0.37 | -0.22 | 0.46 |
| W63_thr0.5_0.25_hystFalse | -0.47 | -0.66 | +1.10 | -0.37 | -0.22 | 0.77 |
| W63_thr0.5_0.25_hystTrue | -0.21 | -0.57 | +0.84 | -0.21 | -0.21 | 0.51 |

## Stability check FX_MR_STACK : top-5 by VAL vs top-5 by OOS
  Top 5 VAL  : ['W126_thr0.3_0.0_hystFalse', 'W126_thr0.3_0.0_hystTrue', 'W252_thr0.3_0.0_hystTrue', 'W126_thr0.5_0.0_hystFalse', 'W252_thr0.3_0.0_hystFalse']
  Top 5 OOS  : ['W126_thr0.5_0.25_hystFalse', 'W126_thr0.5_0.25_hystTrue', 'W126_thr0.5_0.0_hystFalse', 'W126_thr0.5_0.0_hystTrue', 'W126_thr0.3_0.0_hystFalse']
  Overlap    : ['W126_thr0.3_0.0_hystFalse', 'W126_thr0.5_0.0_hystFalse'] (2/5)

  → MIXED : some param sensitivity, but ranking partially preserved
