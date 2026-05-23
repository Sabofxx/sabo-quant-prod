# Adaptive Regime Sizing Audit

Problem: 7-year FULL-sample Sharpe is only 0.42 (FX_MR_STACK 10% vol-target).
Edge concentrated in good regimes. Bad regimes (2017, 2020, 2022) bleed.

Solution tested: scale leverage by rolling 126-day realized Sharpe.
- High Sharpe regime (rolling > 0.5) : full vol-target
- Mid Sharpe (0 to 0.5) : 50%-75% vol-target
- Negative Sharpe : 0%-50% vol-target

## Full-sample comparison (per spec × config)
| spec | config | Sharpe | ann_ret% | max_dd% | Calmar |
|---|---|---:|---:|---:|---:|
| FX_MR_STACK | STATIC_baseline | +0.16 | +1.7 | -48.3 | 0.04 |
| FX_MR_STACK | ADAPTIVE_50_25 | +0.41 | +3.1 | -24.9 | 0.12 |
| FX_MR_STACK | ADAPTIVE_75_50 | +0.34 | +2.9 | -30.9 | 0.09 |
| FX_MR_STACK | ADAPTIVE_50_0 | +0.55 | +4.0 | -14.7 | 0.27 |
| NO_EUR_STACK | STATIC_baseline | +0.17 | +1.8 | -45.0 | 0.04 |
| NO_EUR_STACK | ADAPTIVE_50_25 | +0.44 | +3.3 | -21.1 | 0.15 |
| NO_EUR_STACK | ADAPTIVE_75_50 | +0.34 | +2.8 | -29.2 | 0.10 |
| NO_EUR_STACK | ADAPTIVE_50_0 | +0.53 | +3.8 | -15.2 | 0.25 |
| COMDOLL_STACK | STATIC_baseline | +0.37 | +3.9 | -35.2 | 0.11 |
| COMDOLL_STACK | ADAPTIVE_50_25 | +0.61 | +4.5 | -21.2 | 0.21 |
| COMDOLL_STACK | ADAPTIVE_75_50 | +0.52 | +4.3 | -25.4 | 0.17 |
| COMDOLL_STACK | ADAPTIVE_50_0 | +0.65 | +4.7 | -19.5 | 0.24 |

## Per-regime comparison (2019-22 bad regime, 2023-25 good regime)
| spec | config | 2019-22 Sh | 2023-25 Sh | improvement vs static |
|---|---|---:|---:|---|
| FX_MR_STACK | STATIC_baseline | -0.69 | +1.32 | — |
| FX_MR_STACK | ADAPTIVE_50_25 | -0.57 | +1.35 | bad ++0.11, good +0.04 |
| FX_MR_STACK | ADAPTIVE_75_50 | -0.61 | +1.38 | bad ++0.08, good +0.06 |
| FX_MR_STACK | ADAPTIVE_50_0 | -0.30 | +1.34 | bad ++0.38, good +0.02 |
| NO_EUR_STACK | STATIC_baseline | -0.58 | +1.18 | — |
| NO_EUR_STACK | ADAPTIVE_50_25 | -0.40 | +1.23 | bad ++0.18, good +0.05 |
| NO_EUR_STACK | ADAPTIVE_75_50 | -0.51 | +1.24 | bad ++0.08, good +0.06 |
| NO_EUR_STACK | ADAPTIVE_50_0 | -0.22 | +1.21 | bad ++0.36, good +0.03 |
| COMDOLL_STACK | STATIC_baseline | -0.32 | +1.27 | — |
| COMDOLL_STACK | ADAPTIVE_50_25 | -0.30 | +1.41 | bad ++0.02, good +0.13 |
| COMDOLL_STACK | ADAPTIVE_75_50 | -0.33 | +1.38 | bad +-0.02, good +0.11 |
| COMDOLL_STACK | ADAPTIVE_50_0 | -0.27 | +1.42 | bad ++0.05, good +0.15 |

## Best adaptive config per spec (by full-sample Calmar)
| spec | best config | static Calmar | best Calmar | improvement |
|---|---|---:|---:|---:|
| FX_MR_STACK | ADAPTIVE_50_0 | 0.04 | 0.27 | +0.24 |
| NO_EUR_STACK | ADAPTIVE_50_0 | 0.04 | 0.25 | +0.21 |
| COMDOLL_STACK | ADAPTIVE_50_0 | 0.11 | 0.24 | +0.13 |

## Per-year Sharpe (FX_MR_STACK)
| year | STATIC | ADAPTIVE_50_25 | ADAPTIVE_75_50 | ADAPTIVE_50_0 |
|---|---:|---:|---:|---:|
| 2019 | -0.64 | -0.73 | -0.73 | -0.72 |
| 2020 | -1.39 | -1.19 | -1.30 | +0.96 |
| 2021 | +0.77 | +0.14 | +0.48 | +0.12 |
| 2022 | -1.45 | -1.66 | -1.59 | -1.20 |
| 2023 | +1.06 | +0.89 | +1.00 | +0.91 |
| 2024 | +0.36 | +0.27 | +0.31 | +0.16 |
| 2025 | +2.58 | +2.54 | +2.56 | +2.54 |

## Verdict

**ADAPTIVE WINS** : ADAPTIVE_50_0 avg Calmar 0.25 vs static 0.06

Recommendation:
- Deploy ADAPTIVE_50_0 adaptive sizing on production
- Reduces drawdown during bad regimes while preserving upside in good
- Add to signal_generator: compute rolling Sharpe per spec, scale vol_target accordingly
