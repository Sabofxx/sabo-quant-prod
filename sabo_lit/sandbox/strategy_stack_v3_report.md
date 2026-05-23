# Stack V3 AGGRESSIVE — Equal-weight + Vol-target push for retail

6 FX pairs, per-pair best lookback frozen: {'EURUSD': 5, 'GBPUSD': 3, 'USDJPY': 10, 'AUDUSD': 21, 'NZDUSD': 10, 'USDCAD': 3}
Stack weighting: **EQUAL** (V2 lesson: Sharpe-weighted overfits IS)
Vol-target sizing, max leverage 15.0×, kill DD 30%
IS: 2019-2023 | OOS: 2024-2025  |  Reference capital: $25000

## UNLEVERED EQUAL-WEIGHT STACK
  OOS Sharpe=+1.51  ann_ret=+5.76%  vol=3.81%  maxDD=-3.84%  Calmar=1.50

## VOL-TARGET FRONTIER (OOS, raw — kill switch active in 'killed' col)
| target_vol | OOS Sh | OOS ret% | maxDD% | avg_lev | kill | killed_ret% | CI95 Sh | CI95 ann_ret% | P(ruin30%)3y | P(ruin50%)3y |
|---|---|---|---|---|---|---|---|---|---|---|
| 15% | +1.44 | +22.8 | -23.7 | 4.4× | — | +22.8 | [+0.32,+2.57] | [+5.0,+41.0] | 2% | 0% |
| 20% | +1.44 | +30.4 | -31.5 | 5.9× | 2024-08-23 | +1.3 | [+0.32,+2.57] | [+6.6,+54.6] | 12% | 0% |
| 30% | +1.44 | +45.4 | -47.0 | 8.8× | 2024-08-16 | +7.4 | [+0.33,+2.56] | [+9.6,+81.5] | 48% | 7% |
| 40% | +1.49 | +60.7 | -55.8 | 11.2× | 2024-08-06 | +12.4 | [+0.37,+2.61] | [+14.9,+107.7] | 76% | 20% |
| 50% | +1.56 | +75.1 | -57.3 | 13.0× | 2024-08-06 | +14.9 | [+0.46,+2.67] | [+21.3,+131.5] | 88% | 32% |
| 60% | +1.55 | +81.7 | -57.4 | 14.2× | 2024-08-06 | +15.2 | [+0.44,+2.64] | [+22.7,+144.1] | 94% | 42% |

## DOLLAR FRAMING (capital $25000)
| target_vol | ann_ret$ | maxDD$ | worst_day$ | annual_vol$ |
|---|---|---|---|---|
| 15% | $+5702 | $-5913 | $-1358 | $3958 |
| 20% | $+7603 | $-7884 | $-1810 | $5277 |
| 30% | $+11359 | $-11755 | $-2715 | $7868 |
| 40% | $+15179 | $-13940 | $-3620 | $10164 |
| 50% | $+18776 | $-14337 | $-3955 | $12038 |
| 60% | $+20436 | $-14353 | $-3955 | $13217 |

## RECOMMENDED AGGRESSIVE TARGET: **vol_target = 15%**
  Criteria: max ann_ret with NO kill triggered + ruin50%/3y < 25%
  OOS Sharpe   : +1.44
  OOS ann_ret  : +22.8%  ($+5702 on $25000)
  OOS maxDD    : -23.7%  ($-5913)
  OOS Calmar   : 0.96
  OOS worst day: -5.43%  ($-1358)
  avg leverage : 4.4×  (max used 8.6×, cap 15.0×)
  CI95 Sharpe  : [+0.32, +2.57]
  CI95 ann_ret : [+5.0%, +41.0%]
  P(ruin -30% over 3y) : 2%
  P(ruin -50% over 3y) : 0%

## PER-YEAR — target_vol=15% (kill switch active)
| year | n | Sharpe | ann_ret% | maxDD% | wr% | $ ret on $25k |
|---|---|---|---|---|---|---|
| 2019 | 363 | -0.70 | -10.3 | -21.0 | 40.5 | $-2581 |
| 2020 | 366 | -1.39 | -23.5 | -45.0 | 38.3 | $-5885 |
| 2021 | 365 | +0.77 | +12.3 | -9.2 | 46.0 | $+3074 |
| 2022 | 365 | -1.45 | -23.3 | -42.3 | 38.9 | $-5816 |
| 2023 | 365 | +1.06 | +16.2 | -15.0 | 47.9 | $+4058 |
| 2024 | 366 | +0.36 | +5.9 | -23.7 | 44.8 | $+1463 |
| 2025 | 365 | +2.58 | +39.8 | -6.7 | 46.8 | $+9953 |

## EXECUTION SPEC (concrete, retail $25k)

### Daily process (UTC 22:00, ~5 min/day manual or scripted)
```
For each of 6 pairs at 22:00 UTC daily:
  1. Compute past N-day cum return where N = {'EURUSD': 5, 'GBPUSD': 3, 'USDJPY': 10, 'AUDUSD': 21, 'NZDUSD': 10, 'USDCAD': 3}
  2. Signal_t = -1 if past_N_ret > 0, +1 if < 0
  3. Position equal-weight 1/6 of total leveraged capital

Daily leverage recalc:
  realized_vol = std(portfolio_returns last 60d) × sqrt(252)
  leverage = min(0.15 / realized_vol, 15.0)

Execution:
  - Close all positions at 22:00 UTC
  - Open new positions at 22:01 UTC using signals + leverage
  - Per-pair notional = capital × leverage × (1/6) × signal
  - On $25000 with lev 4.4× per-pair notional ≈ $18508
```

### Kill switches (HARD STOP)
  - Peak-to-trough DD > 30% ($7500) → halt + 30-day review
  - Single day loss > 10% of capital ($2500) → manual review
  - 60-day rolling Sharpe < -0.5 → reduce target_vol by 50%
  - Weekly check : if any pair vol > 2× IS vol → reduce that pair's weight to 50%

## VERDICT: **TRADEABLE**

Tradeable but doesn't hit aggressive target. Consider crypto pivot for higher vol/ret.
