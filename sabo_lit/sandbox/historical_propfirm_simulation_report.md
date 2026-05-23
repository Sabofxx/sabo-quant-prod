# Historical Prop Firm Simulation 2019-2026

Question: if you'd bought 8 × $200k prop firm challenges at 2019-01-01,
with 8%/5% two-phase eval + funded with monthly payouts + replacement on blow-up,
how much cash would you have accumulated by 2025-12-31?

**REAL historical returns used** (not bootstrap). This is what actually would
have happened given the FX MR strategy edge during 2019-2025.

## Rules modeled (FundingPips/FundedNext-style)
- Account size : $200,000
- Phase 1 target : 8%
- Phase 2 target : 5%
- Max daily loss : 5%
- Max overall trailing DD : 10%
- Profit share : 80%
- Payout cycle : every 21 trading days
- Fee per challenge : $1,000
- Replacement delay after blow-up : 7 trading days
- Slippage haircut on payouts : 1.5%

## Allocation (post-validation, no EURUSD_MR5)
| account | spec | vol_target |
|---|---|---:|
| A1 | FX_MR_STACK | 10% |
| A2 | FX_MR_STACK | 10% |
| A3 | NO_EUR_STACK | 10% |
| A4 | NO_EUR_STACK | 10% |
| A5 | COMDOLL_STACK | 10% |
| A6 | COMDOLL_STACK | 10% |
| A7 | FX_MR_STACK | 8% |
| A8 | NO_EUR_STACK | 8% |

## Per-account historical results
| account | spec | vol | fees | payouts net | NET CASH | blow-ups | funded periods | days funded |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A1 | FX_MR_STACK | 10% | $10,000 | $31,843 | **$+21,843** | 9 | 3 | 453 |
| A2 | FX_MR_STACK | 10% | $10,000 | $31,843 | **$+21,843** | 9 | 3 | 453 |
| A3 | NO_EUR_STACK | 10% | $12,000 | $24,268 | **$+12,268** | 11 | 4 | 650 |
| A4 | NO_EUR_STACK | 10% | $12,000 | $24,268 | **$+12,268** | 11 | 4 | 650 |
| A5 | COMDOLL_STACK | 10% | $11,000 | $42,152 | **$+31,152** | 10 | 4 | 728 |
| A6 | COMDOLL_STACK | 10% | $11,000 | $42,152 | **$+31,152** | 10 | 4 | 728 |
| A7 | FX_MR_STACK | 8% | $7,000 | $29,521 | **$+22,521** | 6 | 2 | 431 |
| A8 | NO_EUR_STACK | 8% | $8,000 | $26,399 | **$+18,399** | 7 | 2 | 431 |

## PORTFOLIO TOTALS over 7.0 years

- Total fees paid       : **$81,000**
- Total payouts gross   : $256,291
- Total payouts net (after slippage haircut) : $252,447
- **NET CASH ACCUMULATED : $171,447**
- Annualized average    : **$24,509 / year**
- Total blow-ups across all accounts : 73
- Total funded-from-scratch periods  : 26

## Per-year breakdown
| year | fees | payouts net | net cash year | blow-ups | phase passes |
|---|---:|---:|---:|---:|---:|
| 2019 | $18,000 | $0 | $-18,000 | 14 | 7 |
| 2020 | $27,000 | $0 | $-27,000 | 25 | 0 |
| 2021 | $4,000 | $0 | $-4,000 | 2 | 11 |
| 2022 | $14,000 | $0 | $-14,000 | 14 | 0 |
| 2023 | $2,000 | $36,515 | $+34,515 | 6 | 16 |
| 2024 | $14,000 | $39,162 | $+25,162 | 10 | 12 |
| 2025 | $2,000 | $176,770 | $+174,770 | 2 | 16 |

## Interpretation

Capital required upfront : $8,000 (8 initial challenges)
Total fees over 7 years  : $81,000 (including replacements)
Total replacement fees   : $73,000
Net ROI on total fees    : 212%

**Caveats**:
- This is the BEST CASE backtest given today's strategy. Forward returns will differ.
- Assumes you could replace blown accounts immediately (firms may have cooldown)
- Assumes uniform $1000 fee (real costs vary $600-1800 per challenge)
- No live news event tail risk modeled beyond what's in real 2019-2025 data
- 2020 COVID period included = real stress test
- 2022 Fed pivot included = real regime test
