# CRYPTO STACK — Aggressive retail target (50-80% annual)

Coins: ['BTC', 'ETH', 'BNB', 'XRP', 'ADA']  |  TRADING_DAYS=365 (24/7)
Cost round-trip: 0.20% per pair
Max leverage cap: 5.0× | Kill DD: 40% | Reference capital: $25000
Best signal per coin picked by IS Sharpe (avoid OOS overfit)

## BEST SIGNAL PER COIN
| coin | best signal | IS Sharpe | OOS Sharpe |
|---|---|---|---|
| BTC | TSM63 | +0.92 | -0.06 |
| ETH | TSM126 | +0.73 | -0.26 |
| BNB | TSM21 | +0.98 | -0.81 |
| XRP | TSM126 | +0.10 | -0.12 |
| ADA | TSM126 | +0.93 | +0.42 |

## UNLEVERED STACK
  IS  Sharpe=+1.08  ann_ret=+63.9%  vol=59.3%  maxDD=-77.6%
  OOS Sharpe=-0.15  ann_ret=-6.9%  vol=47.1%  maxDD=-60.6%  Calmar=-0.11
  Avg pair-wise correlation OOS: +0.27

## VOL-TARGET FRONTIER (OOS)
| target_vol | OOS Sh | ann_ret% | maxDD% | avg_lev | kill | killed_ret% | CI95 Sh | CI95 ret% | P(ruin30%)3y | P(ruin50%)3y |
|---|---|---|---|---|---|---|---|---|---|---|
| 30% | +0.22 | +7.6 | -44.6 | 0.7× | 2024-11-07 | -11.6 | [-1.18,+1.54] | [-39.6,+53.9] | 95% | 62% |
| 50% | +0.22 | +12.7 | -74.3 | 1.2× | 2024-07-15 | -5.2 | [-1.18,+1.54] | [-65.9,+89.9] | 100% | 95% |
| 80% | +0.22 | +20.4 | -118.9 | 2.0× | 2024-05-01 | +8.2 | [-1.18,+1.54] | [-105.5,+143.8] | 100% | 100% |
| 100% | +0.21 | +23.7 | -150.9 | 2.5× | 2024-04-12 | +14.6 | [-1.19,+1.53] | [-132.3,+176.5] | 100% | 100% |
| 150% | +0.14 | +22.9 | -216.6 | 3.5× | 2024-01-22 | -14.2 | [-1.23,+1.47] | [-197.9,+238.7] | 100% | 100% |

## DOLLAR FRAMING (capital $25000)
| target_vol | ann_ret$ | maxDD$ | worst_day$ |
|---|---|---|---|
| 30% | $+1909 | $-11144 | $-2170 |
| 50% | $+3181 | $-18574 | $-3616 |
| 80% | $+5090 | $-29718 | $-5786 |
| 100% | $+5920 | $-37724 | $-7232 |
| 150% | $+5733 | $-54144 | $-10848 |

## RECOMMENDED TARGET: **vol_target = 30%**
  Criteria: max ann_ret with NO kill + ruin50%/3y < 30% + ret >= 50%
  OOS Sharpe   : +0.22
  OOS ann_ret  : +7.6%  ($+1909 on $25000)
  OOS maxDD    : -44.6%  ($-11144)
  OOS Calmar   : 0.17
  OOS worst day: -8.68%  ($-2170)
  avg leverage : 0.7×  (max 1.8×)
  CI95 Sharpe  : [-1.18, +1.54]
  CI95 ann_ret : [-39.6%, +53.9%]
  P(ruin -30% / 3y) : 95%
  P(ruin -50% / 3y) : 62%

## PER-YEAR @ target_vol=30%
| year | n | Sharpe | ann_ret% | maxDD% | $ ret on $25k |
|---|---|---|---|---|---|
| 2019 | 363 | +2.60 | +95.3 | -15.5 | $+23819 |
| 2020 | 366 | +1.29 | +47.4 | -19.7 | $+11855 |
| 2021 | 365 | +1.96 | +58.5 | -22.6 | $+14622 |
| 2022 | 365 | +0.27 | +8.7 | -35.1 | $+2187 |
| 2023 | 365 | +0.12 | +4.5 | -29.1 | $+1135 |
| 2024 | 366 | +0.55 | +20.2 | -44.6 | $+5044 |
| 2025 | 365 | -0.11 | -3.4 | -31.2 | $-854 |

## EXECUTION SPEC (Binance spot OR perp futures)
```
For each coin ['BTC', 'ETH', 'BNB', 'XRP', 'ADA'] at 00:00 UTC daily:
  1. Compute signal per frozen spec per coin
     BTC: TSM63
     ETH: TSM126
     BNB: TSM21
     XRP: TSM126
     ADA: TSM126
  2. Signal = +1 long / -1 short / 0 flat (rare)
  3. Equal weight 1/5 per coin

Daily leverage:
  realized_vol_60d = std(portfolio_returns 60d) × sqrt(365)
  leverage = min(0.3 / realized_vol_60d, 5.0)

Execution:
  - 00:00 UTC : close all + reopen with new signal & leverage
  - Binance perp futures for short capability
  - Per-coin notional = capital × leverage × (1/5) × signal
  - $25000 × avg_lev 0.7× / 5 coins = ~$3730 per coin notional
```

### Kill switches
  - Peak-to-trough DD > 40% → halt 30 days
  - Single day loss > 15% capital → manual review
  - 30-day rolling Sharpe < -0.5 → halve target_vol
  - Coin vol > 3× IS vol → reduce that coin to 50%

## VERDICT: **RESEARCH_ONLY**
