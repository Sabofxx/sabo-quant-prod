# FX MR 2010-2025 H1 Bid/Ask Backtest

Data: Dukascopy H1 bid/ask, 6 FX pairs. Returns use mid close-to-close
with bid/ask spread charged on turnover only. Intraday DD approximated from H1 lows/highs.

## Strategy comparison
| spec | full Sh | full ann% | full DD% | pre-2019 Sh | 2022-2025 Sh | 2023-2025 Sh | worst H1 intraday% | random p95 Sh | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| FX_MR_STACK | +0.19 | +0.74 | -22.22 | +0.26 | +0.49 | +1.25 | -3.43 | -0.72 | ROBUST_WEAK |
| NO_EUR_STACK | +0.15 | +0.63 | -23.97 | +0.20 | +0.35 | +1.04 | -3.73 | -0.67 | ROBUST_WEAK |
| COMDOLL_STACK | +0.29 | +1.56 | -19.12 | +0.24 | +0.71 | +1.17 | -4.00 | -0.58 | ROBUST_WEAK |
| FAST_STACK | -0.10 | -0.50 | -36.05 | +0.11 | +0.10 | +0.40 | -3.76 | -0.31 | REGIME/WEAK |
| UNIFORM_MR10 | +0.26 | +1.19 | -14.09 | +0.03 | +0.40 | +0.92 | -3.43 | -0.72 | ROBUST_WEAK |

## FX_MR_STACK by year
| year | Sharpe | ann% | maxDD% | win% |
|---|---:|---:|---:|---:|
| 2010 | +0.32 | +1.64 | -5.65 | 41.1 |
| 2011 | +0.79 | +3.38 | -5.99 | 51.0 |
| 2012 | +0.60 | +1.90 | -3.26 | 39.9 |
| 2013 | -0.23 | -0.84 | -5.14 | 43.0 |
| 2014 | +0.40 | +1.16 | -4.17 | 44.1 |
| 2015 | +0.42 | +1.72 | -3.96 | 47.4 |
| 2016 | +0.31 | +1.35 | -2.68 | 43.2 |
| 2017 | -0.71 | -2.39 | -5.31 | 41.1 |
| 2018 | +0.31 | +0.97 | -2.97 | 44.9 |
| 2019 | -0.20 | -0.50 | -3.70 | 43.0 |
| 2020 | -1.35 | -6.55 | -11.02 | 38.5 |
| 2021 | +0.47 | +1.69 | -2.39 | 45.5 |
| 2022 | -1.06 | -5.72 | -11.31 | 37.8 |
| 2023 | +1.20 | +4.38 | -3.81 | 49.0 |
| 2024 | +0.29 | +1.02 | -3.76 | 42.9 |
| 2025 | +2.10 | +8.67 | -2.20 | 47.1 |

## 10% vol-target comparison
| spec | full Sh | ann% | maxDD% | pre-2019 Sh | post-2023 Sh |
|---|---:|---:|---:|---:|---:|
| FX_MR_STACK | +0.09 | +0.97 | -65.70 | +0.12 | +1.19 |
| NO_EUR_STACK | +0.11 | +1.14 | -64.21 | +0.13 | +1.02 |
| COMDOLL_STACK | +0.17 | +1.78 | -50.42 | +0.03 | +1.16 |
| FAST_STACK | -0.12 | -1.24 | -80.52 | +0.12 | +0.39 |
| UNIFORM_MR10 | +0.23 | +2.43 | -40.21 | -0.01 | +0.98 |

## Interpretation
- If pre-2019 Sharpe is negative, the edge is not a 2010-stable law; it is regime-dependent.
- If full Sharpe beats random p95 but only because 2023-2025 is strong, deploy with regime/runway controls.
- H1 underestimates exact intraday DD versus M5, but is sufficient for long-horizon edge validation.
