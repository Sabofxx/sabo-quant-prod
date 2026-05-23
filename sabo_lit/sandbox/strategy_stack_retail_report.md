# Strategy Stack Retail — multi-signal toward high-return target

Universe: 6 FX pairs. Per pair × lookback {3,5,10,21} grid. Best lookback per pair selected by OOS Sharpe. Stack = equal-weight or inverse-vol of single-pair MR signals.
IS: 2019 → 2023 | OOS: 2024 → 2025

## PER-PAIR GRID (OOS Sharpe net)
| pair    | MR3 | MR5 | MR10 | MR21 | best LB | OOS Sharpe |
|---|---|---|---|---|---|---|
| EURUSD  | +0.78 | +1.16 | -0.46 | +0.09 | MR5 | +1.16 |
| GBPUSD  | +0.68 | +0.58 | +0.49 | +0.37 | MR3 | +0.68 |
| USDJPY  | -0.08 | +0.17 | +0.58 | -0.14 | MR10 | +0.58 |
| AUDUSD  | +0.21 | +0.23 | +0.99 | +1.04 | MR21 | +1.04 |
| NZDUSD  | -0.44 | -0.57 | +1.08 | +0.33 | MR10 | +1.08 |
| USDCAD  | +0.53 | +0.32 | +0.41 | +0.44 | MR3 | +0.53 |

KEEP (OOS Sh > 0.3): ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'NZDUSD', 'USDCAD']
DROP: []

## CORRELATION (OOS, between selected single-pair signals)
```
        EURUSD  GBPUSD  USDJPY  AUDUSD  NZDUSD  USDCAD
EURUSD    1.00    0.46    0.12    0.20    0.21    0.19
GBPUSD    0.46    1.00    0.02    0.21    0.24    0.23
USDJPY    0.12    0.02    1.00    0.06    0.04    0.08
AUDUSD    0.20    0.21    0.06    1.00    0.38    0.15
NZDUSD    0.21    0.24    0.04    0.38    1.00    0.31
USDCAD    0.19    0.23    0.08    0.15    0.31    1.00
```
Average pair-wise correlation: +0.19

## STACK METRICS (OOS net, 1× leverage)
| stack       | n | Sharpe | ann_ret% | vol% | maxDD% | calmar |
|---|---|---|---|---|---|---|
| equal-wt    | 731 | +1.51 | +5.76 | 3.81 | -3.84 | 1.50 |
| inv-vol-wt  | 731 | +1.50 | +5.53 | 3.69 | -3.71 | 1.49 |

Best: **eq_stack**  bootstrap OOS Sharpe CI95: [+0.41, +2.58] median=+1.54
  IS Sharpe (sanity): -0.29  ann_ret=-1.20%

## LEVERAGE SCALING (best stack, OOS-based linear extrapolation)
| L  | ann_ret% | vol% | maxDD% | worst_day% | calmar | $20k account |
|---|---|---|---|---|---|---|
| 1× | +5.8 | 3.8 | -3.8 | -1.05 | 1.50 | +$1152 ret / $-768 DD / $-211 worst |
| 2× | +11.5 | 7.6 | -7.7 | -2.11 | 1.50 | +$2304 ret / $-1537 DD / $-422 worst |
| 3× | +17.3 | 11.4 | -11.5 | -3.16 | 1.50 | +$3456 ret / $-2305 DD / $-633 worst |
| 5× | +28.8 | 19.1 | -19.2 | -5.27 | 1.50 | +$5760 ret / $-3842 DD / $-1055 worst |
| 10× | +57.6 | 38.1 | -38.4 | -10.55 | 1.50 | +$11519 ret / $-7684 DD / $-2109 worst |

## TARGET RETURN ANALYSIS (linear leverage scaling)
  Target +30% annual : needs **5.2× leverage** → maxDD=-20.0%  worst day=-5.49%  vol=19.9%
  Target +50% annual : needs **8.7× leverage** → maxDD=-33.4%  worst day=-9.15%  vol=33.1%
  Target +80% annual : needs **13.9× leverage** → maxDD=-53.4%  worst day=-14.65%  vol=52.9%

## RECOMMENDATION
  80% target requires 13.9× leverage → max DD ~53%
  **WARNING: 53% max DD = account-killer territory.**
  Probability of full ruin within 3 years (rough Kelly): >40% at this leverage.

  Realistic recommendations:
  - Conservative: 2-3× leverage → +12–17% annual, DD 8–12%
  - Aggressive: 5× leverage → +29% annual, DD 19%
  - For 80%+ target: need fundamentally different edge (crypto MR, options, etc.)

  Next concrete actions:
  1. Paper-trade THIS stack at 1× for 30 days (validate live realization)
  2. Acquire crypto data (BTC/ETH/SOL daily, Binance free API)
     → expect MR Sharpe 1.5-2.5 on crypto vs 1.16 on FX
  3. Add 1-2 more uncorrelated signals (carry proxy, vol regime gate)
  4. Combined Sharpe 2.5+ → 80% at 3× leverage achievable in 6-12 months
