# Accuracy Boost — push win rate above 50%

Baseline FX_MR_STACK win rate ~43%. Strategies tested:
- V1 STRICT_CONSENSUS : all 3 lookbacks {MR3, MR5, MR10} must agree → reduce N, raise wr
- V2 SOFT_CONSENSUS 2/3 : 2 of 3 lookbacks agree
- V3 WEIGHTED_ENSEMBLE : continuous position = mean of 3 signals
- V4 VOL_GATE : trade only when short-vol < 1.5× long-vol (low-vol = chop = MR-friendly)
- V5 STRICT + VOL_GATE : compound V1 + V4
- V6 MULTI_PAIR_CONFIRM : require USD-basket signal in same direction

## EURUSD SOLO — accuracy frontier (OOS 2024-2025)
| spec | n_traded | trade% | wr% | Sharpe | ann_ret% | maxDD% | exp/trade(bps) | CI95 wr% |
|---|---|---|---|---|---|---|---|---|
| baseline_MR5 | 635 | 86.9 | 49.8 | +1.16 | +6.9 | -5.0 | +3.1 | [45.8, 53.6] |
| V1_strict_consensus | 487 | 66.6 | 37.2 | +0.46 | +2.1 | -3.6 | +1.2 | [33.0, 41.7] |
| V2_soft_consensus_2of3 | 640 | 87.6 | 49.7 | +0.82 | +4.9 | -4.6 | +2.2 | [45.6, 53.7] |
| V3_weighted_ensemble | 659 | 90.2 | 47.6 | +0.64 | +3.0 | -3.6 | +1.3 | [43.7, 51.6] |
| V4_vol_gate | 597 | 81.7 | 49.7 | +1.21 | +6.3 | -5.0 | +3.1 | [45.6, 53.7] |
| V5_strict_plus_vol_gate | 453 | 62.0 | 36.9 | +0.64 | +2.5 | -3.5 | +1.6 | [32.5, 41.5] |
| V6_multi_pair_confirm | 619 | 84.7 | 41.8 | +0.83 | +4.3 | -5.5 | +2.0 | [37.9, 45.8] |

  → BEST (wr max with Sharpe > 0.5): **baseline_MR5**
    n_traded=635  trade_rate=86.9%  wr=49.8% (CI95 [45.8, 53.6])
    Sharpe=+1.16  ann_ret=+6.9%  maxDD=-5.0%

## FX 6-PAIR STACK — accuracy frontier (OOS 2024-2025)
| spec | n_traded | trade% | wr% | Sharpe | ann_ret% | maxDD% | exp/trade(bps) | CI95 wr% |
|---|---|---|---|---|---|---|---|---|
| baseline_best_per_pair | 690 | 94.4 | 48.6 | +1.51 | +5.8 | -3.8 | +2.4 | [45.0, 52.3] |
| V1_strict_consensus_per_pair | 707 | 96.7 | 43.8 | +0.77 | +2.7 | -5.2 | +1.1 | [40.3, 47.5] |
| V2_soft_consensus_2of3 | 686 | 93.8 | 46.6 | +0.42 | +1.8 | -7.4 | +0.8 | [42.9, 50.3] |
| V3_weighted_ensemble | 718 | 98.2 | 44.4 | +0.65 | +2.4 | -5.9 | +1.0 | [40.7, 48.2] |
| V4_vol_gate_on_best | 686 | 93.8 | 48.5 | +1.46 | +5.0 | -3.8 | +2.1 | [44.8, 52.3] |
| V5_strict_plus_vol_gate | 703 | 96.2 | 44.5 | +0.76 | +2.4 | -4.8 | +1.0 | [41.0, 48.2] |
| V6_multi_pair_confirm | 698 | 95.5 | 47.3 | +0.96 | +3.7 | -3.2 | +1.5 | [43.8, 51.0] |

  → BEST (wr max with Sharpe > 0.5): **baseline_best_per_pair**
    n_traded=690  trade_rate=94.4%  wr=48.6% (CI95 [45.0, 52.3])
    Sharpe=+1.51  ann_ret=+5.8%  maxDD=-3.8%

## TRADE-OFF SUMMARY
### EURUSD baseline vs best
  baseline_MR5     : wr=49.8%  Sh=+1.16  n_trades=635  trade%=87
  baseline_MR5   : wr=49.8%  Sh=+1.16  n_trades=635  trade%=87
  Δ wr: +0.0 percentage points
  Δ Sh: +0.00
  Trade reduction: 0%

### FX Stack baseline vs best
  baseline_best_per_pair        : wr=48.6%  Sh=+1.51  n=690  trade%=94
  baseline_best_per_pair         : wr=48.6%  Sh=+1.51  n=690  trade%=94
  Δ wr: +0.0 pp
  Δ Sh: +0.00

## RECOMMENDATION
Pick based on goal:
  - Maximize **Sharpe** (best risk-adjusted return) → use FX_MR_STACK best_per_pair (baseline)
  - Maximize **win rate** (psychological comfort, prop firm consistency) → use **baseline_best_per_pair** on stack OR **baseline_MR5** on EURUSD

Trade-off: high wr methods reduce N trades and may reduce raw Sharpe.
For prop firm eval: high wr = fewer DD breaches, more consistent equity curve.
For maximum compounding: high Sharpe wins long-term.
