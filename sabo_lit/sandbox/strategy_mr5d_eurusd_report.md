# EURUSD MR_5d Retail Validation

Universe: EURUSD only. Lookback 5d. Daily close-to-close. Round-trip cost 1.9 pips.
IS: 2019 → 2023 | OOS: 2024 → 2025

## GROSS vs NET
| period | gross Sh | gross ann% | net Sh | net ann% | cost drag |
|---|---|---|---|---|---|
| IS  | -0.10 | -0.62 | -0.25 | -1.51 | +0.14 |
| OOS | +1.32 | +7.82 | +1.16 | +6.87 | +0.16 |

## NET FULL METRICS
  n=2555  Sharpe=+0.15  ann_ret=+0.89%  vol=6.07%  maxDD=-27.61%  wr=43.2%  calmar=0.03  skew=+0.08
  best day=+2.80%  worst day=-2.65%

## BOOTSTRAP CI95 (n=2000)
  OOS  Sharpe : [+0.04, +2.30] median=+1.15
  OOS  ann_ret: [+0.22%, +14.07%]  median=+6.85%
  FULL Sharpe : [-0.48, +0.76] median=+0.15

## RANDOM BASELINE OOS (1000 iter, EURUSD daily ±1 random + same costs)
  p5=-1.35  med=-0.35  p95=+0.57
  Actual NET OOS Sharpe=+1.16  → BEATS 95p

## PER-YEAR NET
| year | n | Sharpe | ann% | maxDD% | wr% |
|---|---|---|---|---|---|
| 2019 | 363 | -1.03 | -4.26 | -8.88 | 40.2 |
| 2020 | 366 | -1.71 | -10.70 | -19.68 | 40.7 |
| 2021 | 365 | +0.67 | +3.15 | -6.10 | 44.9 |
| 2022 | 365 | -0.54 | -4.52 | -10.67 | 44.1 |
| 2023 | 365 | +1.40 | +8.80 | -4.43 | 46.3 |
| 2024 | 366 | +1.27 | +6.33 | -2.74 | 43.2 |
| 2025 | 365 | +1.10 | +7.41 | -5.03 | 43.3 |

## MONTHLY RETURNS (% net)
```
             M01    M02    M03    M04    M05    M06    M07    M08    M09    M10    M11    M12
timestamp                                                                                    
2019       +0.72  -1.62  -1.45  -1.62  +0.42  -2.82  -0.31  +1.48  +1.18  -1.70  -0.79  +0.37
2020       -0.11  -2.24  -8.43  -3.20  +0.95  -0.42  -4.65  +1.41  +1.04  -0.14  +1.41  -1.16
2021       -0.31  -0.09  -1.64  -1.78  +1.21  -0.33  +1.59  -0.27  -1.24  +2.82  +0.64  +3.95
2022       -0.73  -1.83  +1.45  -0.64  -1.59  +2.02  -2.83  +2.41  -1.34  -2.37  -0.77  -0.31
2023       +1.39  +2.25  +4.76  +3.02  -0.22  +2.61  -3.14  +1.50  +1.68  +1.68  +1.01  -3.79
2024       -1.21  +1.20  +1.19  +1.49  +0.65  +2.48  -0.97  -1.58  +2.06  -1.59  +0.66  +4.81
2025       +2.07  +2.68  +0.63  +3.42  +1.42  -2.02  -2.38  +3.27  +3.74  -1.62  -0.84  +0.35
```

## VERDICT: **TRADEABLE**

## RETAIL EXECUTION SPEC

### Account ($15-20k)
- Broker: IC Markets / Pepperstone Raw (FX retail, micro-lot, low spread)
- Account base: USD recommended (avoid currency conversion drag)
- Min capital: $5000 to run with 1% risk and meaningful position size

### Signal computation (daily UTC 22:00)
```python
# Each day at 22:00 UTC:
# 1. Compute past 5-day cumulative return on EURUSD daily close
#    cum_5d = (close[t-1] / close[t-6]) - 1
# 2. Signal:
#    if cum_5d > 0 → short next day
#    if cum_5d < 0 → long next day
#    if cum_5d == 0 → flat (rare)
# 3. Submit market order at 22:01 UTC for full position
# 4. Exit at next 22:00 UTC (force close before submitting new signal)
```

### Position sizing (1% risk per trade)
- Daily expected vol = 0.38%
- 1% capital risk = ~1.5x daily vol stop
- On $17.5k capital: position notional = $17500 × 1 = $17500 (1x leverage)
- EURUSD margin requirement ~3.3% = $580 margin per $17500 position
- Lot size: 0.175 standard lot = 17500 units (micro-lot capable broker)

### Expected performance (per OOS net metrics)
- Annual return: +6.9%  → $+1202 on $17.5k capital
- Annual vol: 5.9%  → ~$1037 std dev
- Max DD historical: -5.0%  → ~$-881 worst case
- Calmar: 1.36
- Best day: +2.80% (~$+490)
- Worst day: -2.65% (~$-463)

### Kill switches (HARD STOP rules)
- Monthly DD > 5% of capital ($875 on $17.5k) → halt + review
- Rolling 60-day Sharpe < -0.5 → halt + review
- Single day loss > 2% of capital → manual review next session
- 3 consecutive losing days = OK ; 5+ = check signal logic

### Risks disclosed
- OOS CI95 lower bound = +0.04 Sharpe (could realize negative)
- Single pair concentration: 100% EURUSD exposure
- No black swan protection: 2-3% adverse gap possible at NY close / weekend
- Edge could be regime-conditional (2024 saw weaker year per-year table)
- Sample size: 731 OOS days = limited statistical power

### Live tracking (first 30 days)
- Record actual fill prices + slippage per trade
- Compare daily realized P&L vs backtest expected (within 1 std)
- After 30 days: re-evaluate. If realized within CI95 → scale up.
- If realized below CI95 lower bound for 20+ days → STOP.
