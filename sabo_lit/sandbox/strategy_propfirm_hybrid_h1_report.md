# Prop Firm Hybrid H1 Report

MC paths: 3000. Horizon: 504 trading days.

## OOS strategy metrics

| spec | Sh | ann_ret% | vol% | maxDD% | win% | worst day% | worst intraday% |
|---|---:|---:|---:|---:|---:|---:|---:|
| HYBRID_FX_H1 | +1.65 | +5.8 | 3.5 | -3.2 | 46.8 | -1.11 | -1.72 |
| FX_MR_STACK | +1.51 | +5.8 | 3.8 | -3.8 | 45.8 | -1.05 | -1.14 |
| NO_EUR_STACK | +1.39 | +5.5 | 4.0 | -4.3 | 46.9 | -1.13 | -1.34 |
| EURUSD_MR5 | +1.16 | +6.9 | 5.9 | -5.0 | 43.2 | -1.63 | -2.05 |
| H1_MULTI | +0.97 | +5.8 | 6.0 | -4.4 | 47.7 | -2.94 | -2.94 |
| H1_NASDAQ_TREND | +0.77 | +10.2 | 13.2 | -12.2 | 44.0 | -3.85 | -4.08 |
| H1_DOW_TSM | +0.51 | +6.3 | 12.5 | -13.1 | 44.7 | -8.81 | -8.81 |
| H1_CHFJPY_BREAK | +0.46 | +0.9 | 1.9 | -1.9 | 4.7 | -1.05 | -1.05 |

## Correlation at 10% vol

```
                 FX_MR_STACK  EURUSD_MR5  NO_EUR_STACK  H1_MULTI  H1_NASDAQ_TREND  H1_CHFJPY_BREAK  H1_DOW_TSM
FX_MR_STACK             1.00        0.59          0.97      0.04             0.11            -0.02       -0.03
EURUSD_MR5              0.59        1.00          0.39      0.01             0.02            -0.05        0.07
NO_EUR_STACK            0.97        0.39          1.00      0.05             0.12            -0.01       -0.05
H1_MULTI                0.04        0.01          0.05      1.00             0.53             0.14        0.58
H1_NASDAQ_TREND         0.11        0.02          0.12      0.53             1.00             0.03       -0.06
H1_CHFJPY_BREAK        -0.02       -0.05         -0.01      0.14             0.03             1.00        0.01
H1_DOW_TSM             -0.03        0.07         -0.05      0.58            -0.06             0.01        1.00
```

## Single 200k low-target account

| spec_tv | P funded | P dead | E payout | E net | median days funded |
|---|---:|---:|---:|---:|---:|
| HYBRID_FX_H1_10 | 89% | 31% | $37993 | $36793 | 130 |
| HYBRID_FX_H1_12 | 73% | 72% | $31304 | $30104 | 97 |
| HYBRID_FX_H1_8 | 93% | 14% | $30005 | $28805 | 163 |
| FX_MR_STACK_10 | 85% | 33% | $29489 | $28289 | 142 |
| EURUSD_MR5_12 | 74% | 52% | $23889 | $22689 | 138 |
| FX_MR_STACK_8 | 89% | 14% | $23590 | $22390 | 178 |
| H1_NASDAQ_TREND_12 | 64% | 76% | $22392 | $21192 | 108 |
| H1_NASDAQ_TREND_10 | 71% | 59% | $22339 | $21139 | 146 |
| FX_MR_STACK_12 | 65% | 75% | $22044 | $20844 | 113 |
| EURUSD_MR5_10 | 79% | 34% | $22012 | $20812 | 166 |
| H1_MULTI_12 | 68% | 70% | $21703 | $20503 | 116 |
| H1_MULTI_10 | 72% | 50% | $19870 | $18670 | 147 |
| H1_NASDAQ_TREND_8 | 71% | 38% | $19012 | $17812 | 196 |
| EURUSD_MR5_8 | 82% | 17% | $16688 | $15488 | 203 |
| H1_MULTI_8 | 77% | 30% | $15187 | $13987 | 193 |
| H1_DOW_TSM_10 | 57% | 65% | $10916 | $9716 | 166 |
| H1_DOW_TSM_12 | 46% | 87% | $9784 | $8584 | 117 |
| H1_DOW_TSM_8 | 56% | 43% | $8636 | $7436 | 210 |
| H1_CHFJPY_BREAK_8 | 24% | 78% | $3410 | $2210 | 221 |
| H1_CHFJPY_BREAK_10 | 11% | 95% | $1432 | $232 | 194 |
| H1_CHFJPY_BREAK_12 | 9% | 98% | $1286 | $86 | 156 |

## Portfolio candidates

| portfolio | fees | E net | median net | P net+ | P >=1 funded | avg funded | p95 net |
|---|---:|---:|---:|---:|---:|---:|---:|
| hybrid_blend_8x200 | $9600 | $186195 | $175676 | 98% | 99% | 6.2 | $395121 |
| hybrid_blend_2x500_4x200 | $9600 | $176234 | $162864 | 97% | 99% | 4.7 | $378996 |
| baseline_full_budget_mixed_2x500_4x200 | $9600 | $162703 | $150016 | 92% | 97% | 4.6 | $382673 |
| baseline_8x200_diversified | $9600 | $150909 | $130763 | 92% | 96% | 5.3 | $382494 |
| hybrid_blend_6x200_cashmax | $7200 | $150172 | $139233 | 97% | 98% | 4.8 | $330462 |
| hybrid_8x200_fx_h1 | $9600 | $140639 | $131393 | 98% | 100% | 5.3 | $298388 |
| hybrid_2x500_4x200 | $9600 | $138555 | $129003 | 98% | 99% | 4.4 | $292126 |
| hybrid_h1_heavy_6x200 | $7200 | $95449 | $89781 | 98% | 99% | 3.8 | $202772 |

## Best allocation

Best by expected net cash-out: **hybrid_blend_8x200**.
- A1: LOW_TARGET_200K $200000, HYBRID_FX_H1, vol 10%
- A2: LOW_TARGET_200K $200000, FX_MR_STACK, vol 10%
- A3: LOW_TARGET_200K $200000, EURUSD_MR5, vol 10%
- A4: LOW_TARGET_200K $200000, NO_EUR_STACK, vol 10%
- A5: LOW_TARGET_200K $200000, COMDOLL_STACK, vol 10%
- A6: LOW_TARGET_200K $200000, FAST_STACK, vol 12%
- A7: LOW_TARGET_200K $200000, H1_MULTI, vol 10%
- A8: LOW_TARGET_200K $200000, H1_NASDAQ_TREND, vol 8%

## Interpretation

- H1 adds diversification mainly through index trend and one CHFJPY breakout signal.
- Oil candidates were useful in the H1 miner, but their intraday path is too rough for prop daily-loss rules at high size.
- The comparison keeps the previous FX-only best portfolios as baselines.