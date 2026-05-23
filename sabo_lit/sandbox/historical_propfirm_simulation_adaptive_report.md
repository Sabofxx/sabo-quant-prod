# Historical Prop Firm Simulation 2019-2026 — ADAPTIVE SIZING

Same setup as `historical_propfirm_simulation.py` but with adaptive sizing
ADAPTIVE_50_0 : leverage scales by rolling 126-day Sharpe per spec.
Pauses trading entirely (vol-target=0) when rolling Sharpe < 0.

## Pause rates per spec/vol
- FX_MR_STACK vt=8% : **0.0% of days paused**
- FX_MR_STACK vt=10% : **0.0% of days paused**
- NO_EUR_STACK vt=8% : **0.0% of days paused**
- NO_EUR_STACK vt=10% : **0.0% of days paused**
- COMDOLL_STACK vt=8% : **0.0% of days paused**
- COMDOLL_STACK vt=10% : **0.0% of days paused**

## Per-account historical results ADAPTIVE
| account | spec | vol | fees | payouts net | NET CASH | blowups | funded | days_funded |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A1 | FX_MR_STACK | 10% | $7,000 | $43,975 | **$+36,975** | 6 | 2 | 655 |
| A2 | FX_MR_STACK | 10% | $7,000 | $43,975 | **$+36,975** | 6 | 2 | 655 |
| A3 | NO_EUR_STACK | 10% | $7,000 | $43,836 | **$+36,836** | 6 | 2 | 553 |
| A4 | NO_EUR_STACK | 10% | $7,000 | $43,836 | **$+36,836** | 6 | 2 | 553 |
| A5 | COMDOLL_STACK | 10% | $7,000 | $55,771 | **$+48,771** | 6 | 2 | 694 |
| A6 | COMDOLL_STACK | 10% | $7,000 | $55,771 | **$+48,771** | 6 | 2 | 694 |
| A7 | FX_MR_STACK | 8% | $5,000 | $23,628 | **$+18,628** | 4 | 2 | 399 |
| A8 | NO_EUR_STACK | 8% | $5,000 | $18,630 | **$+13,630** | 4 | 2 | 201 |

## PORTFOLIO TOTALS over 7.0 years (ADAPTIVE)

- Total fees                : **$52,000**
- Total payouts net         : $329,424
- **NET CASH ACCUMULATED   : $277,424**
- Annualized                : **$39,659 / year**
- Total blow-ups            : 44
- Total funded periods      : 16

## Per-year breakdown ADAPTIVE
| year | fees | payouts net | net cash year | blowups | phase passes |
|---|---:|---:|---:|---:|---:|
| 2019 | $15,000 | $0 | $-15,000 | 12 | 2 |
| 2020 | $15,000 | $0 | $-15,000 | 10 | 0 |
| 2021 | $2,000 | $0 | $-2,000 | 2 | 4 |
| 2022 | $10,000 | $0 | $-10,000 | 10 | 0 |
| 2023 | $2,000 | $37,722 | $+35,722 | 2 | 14 |
| 2024 | $8,000 | $100,968 | $+92,968 | 8 | 2 |
| 2025 | $0 | $190,734 | $+190,734 | 0 | 16 |

## STATIC vs ADAPTIVE comparison
| metric | STATIC | ADAPTIVE | Δ |
|---|---:|---:|---:|
| Total fees | $81,000 | $52,000 | $-29,000 |
| Total payouts net | $252,447 | $329,424 | $+76,978 |
| NET CASH | $171,447 | $277,424 | **$+105,978** |
| Blow-ups | 73 | 44 | -29 |
| Funded periods | 26 | 16 | -10 |
| Annualized | $24,509 | $39,659 | $+15,150 |

**Net cash improvement : +62% ($+105,978)**
**Blow-up reduction : +29 fewer (+40%)**
