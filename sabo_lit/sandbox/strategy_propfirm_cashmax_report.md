# Prop Firm Cash-Max Optimizer

OOS window: 2024-2025. MC horizon: 504 trading days. MC paths: 5000.
M5 data is used to estimate intraday adverse excursion for daily-loss checks.
Funded profits are withdrawn monthly, reducing the account profit buffer after each payout.

## Unlevered Strategy Quality
| spec | Sharpe | ann_ret% | ann_vol% | maxDD% | win% | worst intraday% |
|---|---:|---:|---:|---:|---:|---:|
| FX_MR_STACK | +1.51 | +5.8 | 3.8 | -3.8 | 45.8 | -1.14 |
| NO_EUR_LOW_VOL | +1.40 | +3.7 | 2.6 | -4.1 | 24.9 | -1.22 |
| NO_EUR_STACK | +1.39 | +5.5 | 4.0 | -4.3 | 46.9 | -1.34 |
| ANTIPODEAN_LOW_VOL | +1.38 | +6.2 | 4.5 | -4.5 | 27.8 | -1.45 |
| COMDOLL_STACK | +1.27 | +6.3 | 4.9 | -4.4 | 47.6 | -1.92 |
| COMDOLL_LOW_VOL | +1.16 | +3.8 | 3.2 | -3.9 | 25.7 | -1.22 |
| EURUSD_MR5 | +1.16 | +6.9 | 5.9 | -5.0 | 43.2 | -2.05 |
| FAST_STACK | +0.89 | +3.7 | 4.1 | -4.7 | 44.5 | -1.51 |
| SLOW_STACK | +0.55 | +2.3 | 4.3 | -5.2 | 44.9 | -1.34 |
| MR5_ALL | +0.40 | +1.7 | 4.3 | -5.8 | 43.4 | -1.41 |

## 10% Vol-Target Correlation
```
                    FX_MR_STACK  EURUSD_MR5  NO_EUR_STACK  COMDOLL_STACK  FAST_STACK  SLOW_STACK  MR5_ALL  ANTIPODEAN_LOW_VOL  COMDOLL_LOW_VOL  NO_EUR_LOW_VOL
FX_MR_STACK                1.00        0.59          0.97           0.79        0.68        0.63     0.73                0.50             0.56            0.68
EURUSD_MR5                 0.59        1.00          0.39           0.27        0.62        0.27     0.63                0.17             0.20            0.29
NO_EUR_STACK               0.97        0.39          1.00           0.83        0.60        0.64     0.66                0.52             0.58            0.70
COMDOLL_STACK              0.79        0.27          0.83           1.00        0.45        0.58     0.49                0.63             0.70            0.57
FAST_STACK                 0.68        0.62          0.60           0.45        1.00        0.27     0.67                0.18             0.31            0.43
SLOW_STACK                 0.63        0.27          0.64           0.58        0.27        1.00     0.35                0.42             0.40            0.46
MR5_ALL                    0.73        0.63          0.66           0.49        0.67        0.35     1.00                0.28             0.37            0.47
ANTIPODEAN_LOW_VOL         0.50        0.17          0.52           0.63        0.18        0.42     0.28                1.00             0.77            0.45
COMDOLL_LOW_VOL            0.56        0.20          0.58           0.70        0.31        0.40     0.37                0.77             1.00            0.66
NO_EUR_LOW_VOL             0.68        0.29          0.70           0.57        0.43        0.46     0.47                0.45             0.66            1.00
```

## Single 200k Low-Target Account
Plan modeled: 5% Phase 1, 5% Phase 2, 5% daily loss, 10% static max loss, 80% profit share.
| spec_tv | P(funded) | P(dead) | E[payout] | E[net after fee] | median days funded |
|---|---:|---:|---:|---:|---:|
| FX_MR_STACK_10 | 84% | 34% | $28641 | $27441 | 147 |
| NO_EUR_STACK_10 | 82% | 37% | $27080 | $25880 | 148 |
| COMDOLL_STACK_12 | 71% | 61% | $26444 | $25244 | 125 |
| COMDOLL_STACK_10 | 79% | 40% | $24674 | $23474 | 152 |
| EURUSD_MR5_12 | 73% | 54% | $23851 | $22651 | 134 |
| FX_MR_STACK_8 | 89% | 14% | $23335 | $22135 | 181 |
| NO_EUR_STACK_8 | 87% | 18% | $21788 | $20588 | 181 |
| FX_MR_STACK_12 | 64% | 76% | $21697 | $20497 | 112 |
| EURUSD_MR5_10 | 79% | 34% | $20996 | $19796 | 169 |
| COMDOLL_STACK_8 | 83% | 19% | $19832 | $18632 | 187 |
| NO_EUR_STACK_12 | 61% | 79% | $18983 | $17783 | 114 |
| FAST_STACK_12 | 60% | 69% | $16469 | $15269 | 140 |
| EURUSD_MR5_8 | 80% | 16% | $16024 | $14824 | 206 |
| FAST_STACK_10 | 67% | 47% | $15589 | $14389 | 175 |
| FAST_STACK_8 | 70% | 26% | $12130 | $10930 | 215 |
| ANTIPODEAN_LOW_VOL_10 | 41% | 96% | $9140 | $7940 | 100 |
| ANTIPODEAN_LOW_VOL_12 | 38% | 98% | $8302 | $7102 | 87 |
| ANTIPODEAN_LOW_VOL_8 | 39% | 95% | $7007 | $5807 | 116 |
| COMDOLL_LOW_VOL_10 | 33% | 97% | $6777 | $5577 | 108 |
| COMDOLL_LOW_VOL_8 | 37% | 92% | $6618 | $5418 | 146 |
| COMDOLL_LOW_VOL_12 | 29% | 99% | $6009 | $4809 | 95 |
| NO_EUR_LOW_VOL_8 | 31% | 95% | $6000 | $4800 | 136 |
| SLOW_STACK_8 | 46% | 52% | $5347 | $4147 | 226 |
| NO_EUR_LOW_VOL_10 | 26% | 98% | $5166 | $3966 | 109 |
| SLOW_STACK_12 | 36% | 95% | $5007 | $3807 | 119 |
| MR5_ALL_12 | 33% | 91% | $4790 | $3590 | 138 |
| SLOW_STACK_10 | 36% | 86% | $4668 | $3468 | 152 |
| MR5_ALL_10 | 35% | 80% | $4479 | $3279 | 171 |
| NO_EUR_LOW_VOL_12 | 20% | 100% | $3600 | $2400 | 90 |
| MR5_ALL_8 | 34% | 61% | $3332 | $2132 | 230 |

## Portfolio Candidates
| portfolio | fees | E gross | E net | median net | P(net+) | P>=1 funded | avg funded | avg dead | p95 net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| full_budget_mixed_2x500_4x200 | $9600 | $170418 | $160818 | $144986 | 92% | 96% | 4.6 | 2.7 | $392313 |
| phased_fast_eval_safe_funded | $9600 | $158774 | $149174 | $138109 | 92% | 96% | 4.5 | 2.6 | $356079 |
| full_budget_8x200_diversified | $9600 | $157143 | $147543 | $130522 | 92% | 97% | 5.3 | 4.5 | $371843 |
| phased_eval10_funded8_2x500_4x200 | $9600 | $153561 | $143961 | $131400 | 92% | 96% | 4.6 | 2.2 | $343213 |
| upper_bound_4x500_trail | $9600 | $152396 | $142796 | $129972 | 90% | 94% | 3.1 | 2.0 | $349558 |
| mixed_large_diversified | $7200 | $127223 | $120023 | $109990 | 92% | 96% | 3.2 | 1.7 | $289879 |
| improved_lowvol_2x500_4x200 | $9600 | $121424 | $111824 | $98860 | 89% | 94% | 3.3 | 4.2 | $289285 |
| cashmax_5x_200k_diversified | $6000 | $117592 | $111592 | $99982 | 93% | 97% | 3.9 | 1.9 | $270019 |
| cashmax_5x_200k_aggressive | $6000 | $108629 | $102629 | $80587 | 89% | 94% | 3.3 | 3.4 | $287708 |
| classic_5x_200k_diversified | $5400 | $102489 | $97089 | $74410 | 91% | 96% | 3.5 | 3.1 | $266065 |
| improved_lowvol_6x200 | $7200 | $85996 | $78796 | $64775 | 89% | 94% | 3.0 | 4.5 | $211195 |
| one_step_fast | $5400 | $65692 | $60292 | $49664 | 91% | 96% | 3.4 | 3.7 | $161105 |
| single_best_low_target | $1200 | $29050 | $27850 | $26972 | 81% | 84% | 0.8 | 0.3 | $67741 |

## Recommended Cash-Max Allocation
Best by expected net cash-out: **full_budget_mixed_2x500_4x200**.
Treat LOW_TARGET plans as rule templates; use separate firms only when their current rules match.
- A1: LOW_TARGET_500K_TRAIL $500000, FX_MR_STACK, vol_target=8%
- A2: LOW_TARGET_500K_TRAIL $500000, EURUSD_MR5, vol_target=8%
- A3: LOW_TARGET_200K $200000, NO_EUR_STACK, vol_target=10%
- A4: LOW_TARGET_200K $200000, COMDOLL_STACK, vol_target=10%
- A5: LOW_TARGET_200K $200000, FAST_STACK, vol_target=12%
- A6: LOW_TARGET_200K $200000, FX_MR_STACK, vol_target=10%

## Hard Constraints
- Do not run the same spec on every account; common-date MC shows correlation matters.
- Avoid crypto for CFD prop rules unless the firm has wider daily-loss bands; daily excursions are too large.
- Prefer 5%/5% static-drawdown two-step plans over 10%/5% plans when available.
- Avoid trailing drawdown 500k plans unless you size lower; expected gross can be high but path risk is worse.
- Do not assume multiple accounts are allowed inside one firm; verify max allocation and copy-trading rules first.
