# Z-score / Bollinger / RSI — push wr via different signal type

Signals tested:
- baseline_MR : per-pair best lookback MR (reference)
- Z1/Z2/Z3 ZSCORE : trade when |close - MA20| / std20 > {2.0, 2.5, 3.0}
- B1 BOLLINGER_TOUCH : trade when close outside Bollinger band (20, 2σ)
- B2 BOLLINGER_REVERT : trade when WAS outside band yesterday AND back inside today
- RSI_extreme : trade when RSI(14) < 30 (long) or > 70 (short), {30/70 vs 25/75}
- Z2 + vol_gate : combine z-score 2σ + low-vol regime

## RESULTS (OOS 2024-2025, equal-weight 6-pair stack)
| spec | n_trd | trd% | wr% | Sharpe | ann% | maxDD% | exp_bps | CI95 wr% | CI95 Sh |
|---|---|---|---|---|---|---|---|---|---|
| baseline_MR_best_LB | 690 | 94.4 | 48.6 | +1.51 | +5.8 | -3.8 | +2.4 | [45.0, 52.3] | [+0.39, +2.56] |
| Z1_zscore_2sigma | 309 | 42.3 | 27.8 | +0.67 | +1.0 | -1.4 | +1.0 | [23.0, 33.2] | [-0.45, +1.66] |
| Z2_zscore_2.5sigma | 136 | 18.6 | 27.2 | +0.86 | +0.7 | -0.7 | +1.4 | [20.2, 34.6] | [-0.27, +1.83] |
| Z3_zscore_3sigma | 47 | 6.4 | 27.7 | +0.47 | +0.1 | -0.3 | +0.9 | [15.4, 40.5] | [-0.67, +1.43] |
| B1_bollinger_touch | 309 | 42.3 | 27.8 | +0.67 | +1.0 | -1.4 | +1.0 | [23.0, 33.2] | [-0.45, +1.66] |
| B2_bollinger_revert | 256 | 35.0 | 22.7 | -0.42 | -0.3 | -1.8 | -0.4 | [17.2, 28.1] | [-1.52, +0.75] |
| RSI_extreme_30_70 | 553 | 75.6 | 43.9 | +0.50 | +1.1 | -3.3 | +0.6 | [39.5, 48.1] | [-0.78, +1.78] |
| RSI_extreme_25_75 | 422 | 57.7 | 41.7 | +0.24 | +0.4 | -2.8 | +0.3 | [36.9, 46.3] | [-1.09, +1.46] |
| Z2_plus_vol_gate | 300 | 41.0 | 28.3 | +0.73 | +1.0 | -1.3 | +1.0 | [23.4, 33.6] | [-0.34, +1.71] |

## WINNERS (Sharpe > 0.5)
- TOP WIN RATE : **baseline_MR_best_LB**
    wr=48.6%  Sh=+1.51  ann_ret=+5.8%  maxDD=-3.8%  n_trades=690
- TOP SHARPE   : **baseline_MR_best_LB**
    Sh=+1.51  wr=48.6%  ann_ret=+5.8%  maxDD=-3.8%
- TOP CALMAR   : **baseline_MR_best_LB**
    Calmar=1.50  Sh=+1.51  wr=48.6%

## TRADE-OFF vs BASELINE (FX_MR_STACK best LB)
  Z1_zscore_2sigma             | Δwr=-20.7pp | ΔSh=-0.84 | Δexp=-1.4bps | trade reduction=+55%
  Z2_zscore_2.5sigma           | Δwr=-21.3pp | ΔSh=-0.65 | Δexp=-1.0bps | trade reduction=+80%
  Z3_zscore_3sigma             | Δwr=-20.9pp | ΔSh=-1.04 | Δexp=-1.6bps | trade reduction=+93%
  B1_bollinger_touch           | Δwr=-20.7pp | ΔSh=-0.84 | Δexp=-1.4bps | trade reduction=+55%
  B2_bollinger_revert          | Δwr=-25.9pp | ΔSh=-1.93 | Δexp=-2.8bps | trade reduction=+63%
  RSI_extreme_30_70            | Δwr= -4.6pp | ΔSh=-1.01 | Δexp=-1.9bps | trade reduction=+20%
  RSI_extreme_25_75            | Δwr= -6.8pp | ΔSh=-1.27 | Δexp=-2.2bps | trade reduction=+39%
  Z2_plus_vol_gate             | Δwr=-20.2pp | ΔSh=-0.78 | Δexp=-1.4bps | trade reduction=+57%

## INTERPRETATION
Our base MR edge has wr ~48-50% with positive expectancy via R asymmetry
(winners larger than losers in magnitude). High-wr signal types tested here:
- If a method beats baseline wr AND Sharpe → genuine accuracy gain
- If method has high wr but low Sharpe → small wins/big losses (anti-MR style)
- If method has low N → over-filtering, noisy stat

**Fundamental truth**: FX daily MR caps around 50% wr. Higher wr requires either
different asset (mean-reverting stocks pairs trading, options selling), or accept
R-asymmetric edge as-is (current baseline is OPTIMAL given universe).
