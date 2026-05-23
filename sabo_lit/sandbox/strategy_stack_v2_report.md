# Stack V2 — Vol-targeted, Sharpe-weighted toward high-return

Pairs (per-pair best lookback): {'EURUSD': 5, 'GBPUSD': 3, 'USDJPY': 10, 'AUDUSD': 21, 'NZDUSD': 10, 'USDCAD': 3}
Weighting: **sharpe-weighted** (chosen by IS Sharpe)
Vol lookback: 60 days. Max leverage cap: 8.0×. Kill switch: peak-to-trough DD > 25% halts trading.
IS: 2019 → 2023 | OOS: 2024 → 2025

## STACK WEIGHTS (chosen)
  EURUSD: 0.000
  GBPUSD: 0.000
  USDJPY: 0.027
  AUDUSD: 0.000
  NZDUSD: 0.517
  USDCAD: 0.455

## VOL-TARGET FRONTIER (OOS, kill switch active)
| target_vol | OOS Sharpe | OOS ann_ret% | OOS maxDD% | avg_lev | max_lev | kill_date | killed_ann_ret% | CI95 Sharpe |
|---|---|---|---|---|---|---|---|---|
| 10% | +1.13 | +11.9 | -18.8 | 2.1× | 3.6× | — | +11.9 | [-0.04, +2.28] |
| 15% | +1.13 | +17.8 | -28.3 | 3.2× | 5.5× | 2024-10-21 | +3.0 | [-0.04, +2.28] |
| 20% | +1.13 | +23.7 | -37.7 | 4.3× | 7.3× | 2024-08-23 | +6.4 | [-0.04, +2.28] |
| 25% | +1.15 | +30.0 | -46.0 | 5.3× | 8.0× | 2024-08-19 | +11.2 | [-0.02, +2.30] |
| 30% | +1.18 | +36.2 | -51.8 | 6.3× | 8.0× | 2024-08-19 | +14.1 | [+0.02, +2.34] |

## OPTIMAL TARGET VOL (max Calmar, no-kill subset): **10%**
  OOS Sharpe=+1.13  ann_ret=+11.9%  maxDD=-18.8%  Calmar=0.63
  Average leverage applied OOS: 2.1× (max 3.6×, cap 8.0×)
  Bootstrap OOS Sharpe CI95: [-0.04, +2.28] median=+1.15

## ANSWER TO 80% TARGET REQUEST
  80% NOT achievable within kill-switch boundaries.
  Best safe target_vol: 10% → ann_ret=+11.9% maxDD=-18.8%

  To realistically achieve 80%:
    (a) Stack 2-3 more uncorrelated edges (crypto, options) → combined
        Sharpe 3+, then 30% target_vol could yield 80%+ with safe DD
    (b) Accept higher kill probability: target_vol 35-40%, expect
        25-40% chance of kill within 3 years
    (c) Pivot to crypto where MR Sharpe historically 1.5-2.5 and
        vol naturally higher → 80% achievable at 2-3× leverage

## PER-YEAR — target_vol=10% (kill switch active)
| year | n | Sharpe | ann_ret% | maxDD% | wr% |
|---|---|---|---|---|---|
| 2019 | 363 | +0.23 | +2.1 | -9.5 | 43.5 |
| 2020 | 366 | -0.09 | -1.0 | -13.4 | 43.2 |
| 2021 | 365 | +0.67 | +7.0 | -10.3 | 43.6 |
| 2022 | 365 | -0.80 | -8.4 | -20.5 | 41.4 |
| 2023 | 365 | +1.01 | +10.5 | -6.6 | 45.5 |
| 2024 | 366 | +0.58 | +6.4 | -18.8 | 45.1 |
| 2025 | 365 | +1.75 | +17.4 | -4.0 | 47.4 |

## VERDICT (best target_vol=10%): **RESEARCH_ONLY**

## RETAIL EXECUTION SPEC
  Stack: 6 FX pairs with frozen lookbacks {'EURUSD': 5, 'GBPUSD': 3, 'USDJPY': 10, 'AUDUSD': 21, 'NZDUSD': 10, 'USDCAD': 3}
  Weights: sharpe-weighted
  Target vol: 10% annualized
  Leverage: dynamic = 10% / realized_60d_vol, capped 8.0×
  Kill switch: halt if peak-to-trough DD > 25%
  On $17.5k capital with avg lev 2.1× :
    expected annual return: $+2076
    expected max DD: $-3297
    Calmar: 0.63
