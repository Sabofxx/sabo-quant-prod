# Historical Prop-Firm Plan Optimizer

Deterministic replay on real 2019-2025 FX returns. Replaces blown accounts after
7 trading days and withdraws funded profits every 21 trading days.

## Portfolio comparison
| portfolio | fees | payouts | net cash | annualized | blow-ups | min cumulative | first positive year |
|---|---:|---:|---:|---:|---:|---:|---:|
| improved_lowvol_2x500_4x200 | $100,800 | $464,055 | **$+363,255** | $51,929/y | 64 | $-52,800 | 2023 |
| full_budget_8x200_diversified | $94,800 | $427,294 | **$+332,494** | $47,532/y | 71 | $-52,800 | 2023 |
| improved_lowvol_6x200 | $98,400 | $399,277 | **$+300,877** | $43,012/y | 76 | $-50,400 | 2023 |
| phased_fast_eval_safe_funded | $87,600 | $388,361 | **$+300,761** | $42,995/y | 47 | $-55,386 | 2023 |
| full_budget_mixed_2x500_4x200 | $79,200 | $379,061 | **$+299,861** | $42,867/y | 46 | $-49,448 | 2024 |
| phased_eval10_funded8_2x500_4x200 | $73,200 | $359,413 | **$+286,213** | $40,916/y | 41 | $-48,000 | 2024 |
| upper_bound_4x500_trail | $67,200 | $351,246 | **$+284,046** | $40,606/y | 24 | $-45,600 | 2024 |
| cashmax_5x_200k_aggressive | $73,200 | $352,257 | **$+279,057** | $39,893/y | 56 | $-44,400 | 2023 |
| mixed_large_diversified | $51,600 | $310,276 | **$+258,676** | $36,979/y | 25 | $-33,422 | 2023 |
| classic_5x_200k_diversified | $56,160 | $288,611 | **$+232,451** | $33,230/y | 47 | $-39,919 | 2023 |
| cashmax_5x_200k_diversified | $49,200 | $264,730 | **$+215,530** | $30,811/y | 36 | $-28,800 | 2023 |
| one_step_fast | $54,000 | $194,908 | **$+140,908** | $20,144/y | 45 | $-35,633 | 2024 |
| single_best_low_target | $9,600 | $52,044 | **$+42,444** | $6,068/y | 7 | $-6,000 | 2023 |

## Best historical portfolio: improved_lowvol_2x500_4x200

| account | firm | spec | eval vol | funded vol | fees | payouts | net | blow-ups | funded periods |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| A1 | LOW_TARGET_500K_TRAIL | FX_MR_STACK | 8% | 8% | $16,800 | $77,327 | **$+60,527** | 6 | 2 |
| A2 | LOW_TARGET_500K_TRAIL | EURUSD_MR5 | 8% | 8% | $16,800 | $114,075 | **$+97,275** | 6 | 2 |
| A3 | LOW_TARGET_200K | ANTIPODEAN_LOW_VOL | 12% | 12% | $18,000 | $90,119 | **$+72,119** | 14 | 4 |
| A4 | LOW_TARGET_200K | COMDOLL_LOW_VOL | 12% | 12% | $16,800 | $80,935 | **$+64,135** | 13 | 4 |
| A5 | LOW_TARGET_200K | NO_EUR_LOW_VOL | 12% | 12% | $22,800 | $49,555 | **$+26,755** | 18 | 4 |
| A6 | LOW_TARGET_200K | FX_MR_STACK | 10% | 10% | $9,600 | $52,044 | **$+42,444** | 7 | 3 |

## Best portfolio per-year cash
| year | fees | payouts | net | blow-ups | phase passes |
|---|---:|---:|---:|---:|---:|
| 2019 | $26,400 | $0 | $-26,400 | 12 | 5 |
| 2020 | $26,400 | $0 | $-26,400 | 17 | 4 |
| 2021 | $7,200 | $33,174 | $+25,974 | 5 | 9 |
| 2022 | $12,000 | $18,294 | $+6,294 | 7 | 1 |
| 2023 | $9,600 | $101,811 | $+92,211 | 8 | 13 |
| 2024 | $14,400 | $137,472 | $+123,072 | 11 | 7 |
| 2025 | $4,800 | $173,305 | $+168,505 | 4 | 10 |

## Interpretation
- The fastest path is low-target 5%/5% style plans when the firm offers static drawdown and fee refund.
- Large 500k trailing accounts can maximize gross cash but add rule fragility; size them lower than 200k static accounts.
- The historical optimum is path-dependent and should not be treated as a guaranteed forward allocation.
- Use this as a plan-selection filter; live firm rules still need exact calibration before buying challenges.
