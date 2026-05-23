# Strategy TSM Cross-Pair Daily, USD-basket Beta Hedged

Pairs: EURUSD, GBPUSD, USDJPY, AUDUSD, NZDUSD, USDCAD
Lookback: 21 days (~1 month). Position: equal-weight ±1/6 per pair. Rebalance daily.
Data: 2019-01-02 → 2025-12-31 (2556 aligned trading days × 6 pairs).
IS: 2019-01-01 → 2023-12-31 | OOS: 2024-01-01 → 2025-12-31
Hedge beta (fitted on IS): **+0.029**

## METRICS — IS (2019-2023)
  Unhedged: n=1824  Sharpe=+0.19  ann_ret=+0.94%  ann_vol=4.93%  maxDD=-7.95%  wr=42.8%  calmar=0.12  skew=+0.28  kurt=+6.97
  Hedged  : n=1824  Sharpe=+0.19  ann_ret=+0.92%  ann_vol=4.93%  maxDD=-7.89%  wr=43.1%  calmar=0.12  skew=+0.28  kurt=+6.94

## METRICS — OOS (2024-2025)
  Unhedged: n=731  Sharpe=-0.69  ann_ret=-2.95%  ann_vol=4.27%  maxDD=-13.46%  wr=40.2%  calmar=-0.22  skew=-0.40  kurt=+5.44
  Hedged  : n=731  Sharpe=-0.70  ann_ret=-2.97%  ann_vol=4.28%  maxDD=-13.24%  wr=40.6%  calmar=-0.22  skew=-0.44  kurt=+5.78

## METRICS — Full sample
  Unhedged   : n=2555  Sharpe=-0.04  ann_ret=-0.18%  ann_vol=4.75%  maxDD=-14.83%  wr=42.0%  calmar=-0.01  skew=+0.15  kurt=+6.86
  Hedged     : n=2555  Sharpe=-0.04  ann_ret=-0.19%  ann_vol=4.75%  maxDD=-14.78%  wr=42.4%  calmar=-0.01  skew=+0.14  kurt=+6.89
  USD basket : n=2555  Sharpe=+0.10  ann_ret=+0.61%  ann_vol=5.89%  maxDD=-19.17%  wr=42.5%  calmar=0.03  skew=+0.02  kurt=+4.53  (passive long-USD)

## RANDOM BASELINE (1000 iterations)
  random Sharpe distribution (full sample, equal-weight ±1 random per pair per day):
    p5=-0.52  median=+0.01  p95=+0.53
  Actual hedged FULL Sharpe=-0.04  → FAILS 95p random baseline
  Actual hedged OOS Sharpe=-0.70  → FAILS 95p random baseline (same dist)

## BOOTSTRAP CI95 — Hedged Sharpe
  OOS  : [-1.82, +0.46] median=-0.66
  FULL : [-0.66, +0.56] median=-0.03

## PER-YEAR HEDGED
| year | n_days | Sharpe | ann_ret% | maxDD% | wr% |
|---|---|---|---|---|---|
| 2019 | 363 | -0.98 | -2.71 | -5.41 | 42.1 |
| 2020 | 366 | +0.43 | +2.34 | -5.82 | 45.4 |
| 2021 | 365 | +0.35 | +1.44 | -3.77 | 42.2 |
| 2022 | 365 | +0.72 | +4.83 | -5.08 | 42.5 |
| 2023 | 365 | -0.29 | -1.33 | -7.89 | 43.3 |
| 2024 | 366 | +0.30 | +1.26 | -3.39 | 41.3 |
| 2025 | 365 | -1.65 | -7.21 | -13.24 | 40.0 |

## PER-YEAR UNHEDGED (for comparison)
| year | n_days | Sharpe | ann_ret% | maxDD% | wr% |
|---|---|---|---|---|---|
| 2019 | 363 | -1.00 | -2.75 | -5.31 | 40.2 |
| 2020 | 366 | +0.41 | +2.22 | -5.95 | 44.8 |
| 2021 | 365 | +0.37 | +1.54 | -3.78 | 42.7 |
| 2022 | 365 | +0.74 | +5.00 | -5.14 | 42.7 |
| 2023 | 365 | -0.29 | -1.35 | -7.95 | 43.3 |
| 2024 | 366 | +0.34 | +1.42 | -3.35 | 40.7 |
| 2025 | 365 | -1.69 | -7.34 | -13.46 | 39.7 |

## VERDICT
  OOS hedged Sharpe=-0.70  (> 0.5 ? NO)
  OOS hedged Sharpe > random 95p (+0.53) ? NO
  OOS bootstrap CI low (-1.82) > 0 ? NO

  FINAL VERDICT: **DEAD**

  next_step: TSM dead on 6 FX pairs. Pivot to (a) cross-sectional momentum (rank pairs, long top/short bottom), (b) carry proxy via interest-rate differential, (c) accept FX day-trading not viable, switch asset class (crypto / equities cross-section).
