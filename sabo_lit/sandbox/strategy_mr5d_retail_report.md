# MR_5d Retail Validation — 6 FX pairs, costs included

Universe: EURUSD, GBPUSD, USDJPY, AUDUSD, NZDUSD, USDCAD. Lookback 5d. Equal weight 1/6.
IS: 2019 → 2023 | OOS: 2024 → 2025. USD-basket beta IS-fit = -0.053
Round-trip cost (pips): EURUSD=1.9, GBPUSD=2.3, USDJPY=2.1, AUDUSD=2.3, NZDUSD=3.1, USDCAD=2.7

## GROSS vs NET METRICS (portfolio)
| period | gross Sh | gross ann% | net Sh | net ann% | cost drag (Sh) |
|---|---|---|---|---|---|
| IS  | +0.28 | +1.37 | +0.01 | +0.06 | +0.27 |
| OOS | +0.72 | +3.15 | +0.41 | +1.78 | +0.32 |

## NET FULL METRICS
  n=2555  Sharpe=+0.12  ann_ret=+0.55%  ann_vol=4.76%  maxDD=-18.46%  wr=43.2%  calmar=0.03  skew=+0.17
  best day=+2.04%  worst day=-2.31%

## BOOTSTRAP CI95 (n_resample=2000)
  OOS  Sharpe CI: [-0.85, +1.50] median=+0.42
  OOS  ann_ret CI: [-3.52%, +6.74%]  median=+1.83%
  FULL Sharpe CI: [-0.53, +0.73] median=+0.12

## RANDOM BASELINE OOS (n=500)
  Sharpe p5=-0.88  med=-0.03  p95=+0.83
  Actual NET OOS Sharpe=+0.41  → FAILS 95p

## PER-PAIR NET METRICS (lookback 5d, individual contribution)
| pair    | gross_full Sh | net_full Sh | gross_oos Sh | net_oos Sh | OOS verdict |
|---|---|---|---|---|---|
| EURUSD  | +0.30 | +0.15 | +1.32 | +1.16 | KEEP |
| GBPUSD  | -0.06 | -0.18 | +0.76 | +0.58 | KEEP |
| USDJPY  | +0.42 | +0.31 | +0.25 | +0.17 | MARGINAL |
| AUDUSD  | +0.36 | +0.15 | +0.49 | +0.23 | KEEP |
| NZDUSD  | +0.23 | -0.06 | -0.23 | -0.57 | DROP |
| USDCAD  | +0.27 | +0.07 | +0.53 | +0.32 | KEEP |

## SIGNAL COMBINATION — MR_5d + MR_1d (equal weight, hedged, net)
  OOS Sharpe (ensemble): -0.01  ann_ret=-0.04%  maxDD=-4.88%
  OOS bootstrap CI: [-1.24, +1.12] median=+0.02

## PER-YEAR NET HEDGED PORTFOLIO
| year | n | Sharpe | ann% | maxDD% | wr% |
|---|---|---|---|---|---|
| 2019 | 363 | -1.09 | -3.15 | -4.49 | 41.0 |
| 2020 | 366 | -1.38 | -7.95 | -14.78 | 36.9 |
| 2021 | 365 | +1.13 | +4.49 | -2.61 | 47.1 |
| 2022 | 365 | +0.56 | +3.56 | -5.53 | 42.2 |
| 2023 | 365 | +0.71 | +3.36 | -4.64 | 47.7 |
| 2024 | 366 | +0.00 | +0.00 | -5.80 | 44.0 |
| 2025 | 365 | +0.75 | +3.56 | -3.47 | 43.6 |

## RETAIL-ADJUSTED VERDICT
  Thresholds: net OOS Sharpe > 0.3 AND CI low > -0.2 AND > random p95
  Actual: net OOS Sharpe=+0.41 CI low=-0.85 rand_p95=+0.83
  VERDICT: **RESEARCH_ONLY**

## RETAIL RECOMMENDATION
  RESEARCH_ONLY: positive net OOS Sharpe but fails retail thresholds.
  Recommend: paper-trade 60 days at micro size to gather more OOS data
  before committing real capital. Track daily realized vs expected.
