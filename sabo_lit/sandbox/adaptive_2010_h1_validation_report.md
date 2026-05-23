# Adaptive Sizing Validation on 2010-2025 H1 Data

CRITICAL test : adaptive layer designed/tested on 2019-2025.
Does it still beat static on UNSEEN 2010-2018 data (9 years)?

Data : Dukascopy H1 bid/ask for 6 FX pairs, 2010-01-01 to 2025-12-31
Vol target : 10% | Adaptive window : 126d
Production tiers : [(0.3, 1.0), (0.0, 0.75), (-1000000000.0, 0.5)]

## FX_MR_STACK

| period | mode | Sharpe | ann% | DD% | Calmar | pause% |
|---|---|---:|---:|---:|---:|---:|
| FULL_2010_2025 | STATIC | +0.12 | +1.3 | -62.0 | 0.02 | 0.0% |
| FULL_2010_2025 | ADAPTIVE_50_0 | +0.23 | +1.6 | -30.3 | 0.05 | 44.4% |
| FULL_2010_2025 | ADAPTIVE_50_25 | +0.20 | +1.5 | -38.1 | 0.04 | 44.4% |
| FULL_2010_2025 | ADAPTIVE_75_50 | +0.18 | +1.5 | -43.2 | 0.03 | 44.4% |
| FULL_2010_2025 | ADAPTIVE_75_50_UNLEV | +0.17 | +1.4 | -43.3 | 0.03 | 42.0% |
| PRE_2019 | STATIC | +0.14 | +1.5 | -30.1 | 0.05 | 0.0% |
| PRE_2019 | ADAPTIVE_50_0 | -0.01 | -0.1 | -28.3 | -0.00 | 43.9% |
| PRE_2019 | ADAPTIVE_50_25 | +0.03 | +0.2 | -26.6 | 0.01 | 43.9% |
| PRE_2019 | ADAPTIVE_75_50 | +0.08 | +0.7 | -26.0 | 0.03 | 43.9% |
| PRE_2019 | ADAPTIVE_75_50_UNLEV | +0.09 | +0.8 | -25.1 | 0.03 | 40.0% |
| POST_2019 | STATIC | +0.10 | +1.1 | -56.0 | 0.02 | 0.0% |
| POST_2019 | ADAPTIVE_50_0 | +0.52 | +3.9 | -19.6 | 0.20 | 45.1% |
| POST_2019 | ADAPTIVE_50_25 | +0.40 | +3.1 | -29.5 | 0.10 | 45.1% |
| POST_2019 | ADAPTIVE_75_50 | +0.29 | +2.5 | -37.6 | 0.07 | 45.1% |
| POST_2019 | ADAPTIVE_75_50_UNLEV | +0.26 | +2.2 | -40.1 | 0.05 | 44.6% |

**Calmar improvement** : PRE_2019 0.05 → 0.03 (Δ -0.02) | POST_2019 0.02 → 0.07 (Δ +0.05)
**Unlevered trigger check** : PRE_2019 0.03 | POST_2019 0.05

## NO_EUR_STACK

| period | mode | Sharpe | ann% | DD% | Calmar | pause% |
|---|---|---:|---:|---:|---:|---:|
| FULL_2010_2025 | STATIC | +0.15 | +1.6 | -58.6 | 0.03 | 0.0% |
| FULL_2010_2025 | ADAPTIVE_50_0 | +0.23 | +1.7 | -30.3 | 0.06 | 46.1% |
| FULL_2010_2025 | ADAPTIVE_50_25 | +0.23 | +1.7 | -31.7 | 0.05 | 46.1% |
| FULL_2010_2025 | ADAPTIVE_75_50 | +0.20 | +1.6 | -40.2 | 0.04 | 46.1% |
| FULL_2010_2025 | ADAPTIVE_75_50_UNLEV | +0.19 | +1.6 | -40.1 | 0.04 | 43.4% |
| PRE_2019 | STATIC | +0.15 | +1.6 | -37.4 | 0.04 | 0.0% |
| PRE_2019 | ADAPTIVE_50_0 | +0.02 | +0.1 | -30.3 | 0.00 | 47.2% |
| PRE_2019 | ADAPTIVE_50_25 | +0.08 | +0.6 | -28.5 | 0.02 | 47.2% |
| PRE_2019 | ADAPTIVE_75_50 | +0.11 | +0.9 | -30.3 | 0.03 | 47.2% |
| PRE_2019 | ADAPTIVE_75_50_UNLEV | +0.11 | +0.9 | -30.6 | 0.03 | 42.2% |
| POST_2019 | STATIC | +0.14 | +1.5 | -48.0 | 0.03 | 0.0% |
| POST_2019 | ADAPTIVE_50_0 | +0.49 | +3.6 | -17.5 | 0.21 | 44.7% |
| POST_2019 | ADAPTIVE_50_25 | +0.41 | +3.1 | -23.4 | 0.13 | 44.7% |
| POST_2019 | ADAPTIVE_75_50 | +0.31 | +2.6 | -32.1 | 0.08 | 44.7% |
| POST_2019 | ADAPTIVE_75_50_UNLEV | +0.28 | +2.4 | -31.9 | 0.08 | 44.9% |

**Calmar improvement** : PRE_2019 0.04 → 0.03 (Δ -0.01) | POST_2019 0.03 → 0.08 (Δ +0.05)
**Unlevered trigger check** : PRE_2019 0.03 | POST_2019 0.08

## COMDOLL_STACK

| period | mode | Sharpe | ann% | DD% | Calmar | pause% |
|---|---|---:|---:|---:|---:|---:|
| FULL_2010_2025 | STATIC | +0.19 | +2.1 | -47.4 | 0.04 | 0.0% |
| FULL_2010_2025 | ADAPTIVE_50_0 | +0.24 | +1.7 | -36.7 | 0.05 | 42.5% |
| FULL_2010_2025 | ADAPTIVE_50_25 | +0.24 | +1.8 | -35.5 | 0.05 | 42.5% |
| FULL_2010_2025 | ADAPTIVE_75_50 | +0.23 | +1.9 | -35.8 | 0.05 | 42.5% |
| FULL_2010_2025 | ADAPTIVE_75_50_UNLEV | +0.19 | +1.6 | -36.9 | 0.04 | 39.7% |
| PRE_2019 | STATIC | +0.05 | +0.5 | -32.9 | 0.01 | 0.0% |
| PRE_2019 | ADAPTIVE_50_0 | -0.13 | -0.9 | -32.3 | -0.03 | 43.6% |
| PRE_2019 | ADAPTIVE_50_25 | -0.08 | -0.6 | -28.9 | -0.02 | 43.6% |
| PRE_2019 | ADAPTIVE_75_50 | -0.03 | -0.2 | -25.7 | -0.01 | 43.6% |
| PRE_2019 | ADAPTIVE_75_50_UNLEV | -0.05 | -0.4 | -25.9 | -0.01 | 37.2% |
| POST_2019 | STATIC | +0.38 | +4.1 | -37.6 | 0.11 | 0.0% |
| POST_2019 | ADAPTIVE_50_0 | +0.68 | +5.1 | -18.3 | 0.28 | 41.0% |
| POST_2019 | ADAPTIVE_50_25 | +0.62 | +4.8 | -21.0 | 0.23 | 41.0% |
| POST_2019 | ADAPTIVE_75_50 | +0.54 | +4.6 | -25.8 | 0.18 | 41.0% |
| POST_2019 | ADAPTIVE_75_50_UNLEV | +0.50 | +4.2 | -26.9 | 0.16 | 42.8% |

**Calmar improvement** : PRE_2019 0.01 → -0.01 (Δ -0.02) | POST_2019 0.11 → 0.18 (Δ +0.07)
**Unlevered trigger check** : PRE_2019 -0.01 | POST_2019 0.16

## VERDICT

**WARNING : ADAPTIVE may be overfit to 2019-2025**. On unseen 2010-2018 data, static performs better or equal. Adaptive may need recalibration.
