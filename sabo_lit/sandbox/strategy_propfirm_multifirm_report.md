# Prop Firm Multi-Firm Realistic Allocation

Critique-corrected version of codex hybrid_blend_8x200:
- **Multi-firm constraint** : max 2 accounts per firm
- **Slippage haircut** : -1.5% applied to gross payouts (live friction)
- **Dropped FRAGILE H1 picks** : per `validate_codex_picks.py` verdict
- **Validated specs only** : FX_MR_STACK + EURUSD_MR5 + NO_EUR_STACK + COMDOLL_STACK

## Firm templates modeled
| firm | account | fee | targets | DD mode | profit share | max accts/trader |
|---|---:|---:|---|---|---:|---:|
| FTMO_SWING_200K | $200000 | $1080 | 10% / 5% | eod_trailing | 80% | 2 |
| MFF_LIKE_STATIC_200K | $200000 | $900 | 5% / 5% | static | 80% | 2 |
| FUNDED_NEXT_TRAIL_200K | $200000 | $1000 | 8% / 5% | eod_trailing | 85% | 2 |
| FUNDINGPIPS_FAST_200K | $200000 | $850 | 8% / 5% | static | 80% | 2 |

## Validated specs (OOS levered metrics)
| spec @ vol | Sharpe | ret% | DD% | worst intra% |
|---|---:|---:|---:|---:|
| COMDOLL_STACK_10 | +1.25 | +13.7 | -12.3 | -3.84 |
| COMDOLL_STACK_12 | +1.25 | +16.4 | -14.8 | -4.60 |
| COMDOLL_STACK_4 | +1.25 | +5.5 | -4.9 | -1.53 |
| COMDOLL_STACK_5 | +1.25 | +6.8 | -6.2 | -1.92 |
| COMDOLL_STACK_8 | +1.25 | +10.9 | -9.9 | -3.07 |
| EURUSD_MR5_10 | +1.12 | +11.4 | -7.4 | -2.68 |
| EURUSD_MR5_12 | +1.12 | +13.7 | -8.8 | -3.22 |
| EURUSD_MR5_4 | +1.12 | +4.6 | -2.9 | -1.07 |
| EURUSD_MR5_5 | +1.12 | +5.7 | -3.7 | -1.34 |
| EURUSD_MR5_8 | +1.12 | +9.1 | -5.9 | -2.15 |
| FX_MR_STACK_10 | +1.44 | +15.2 | -15.8 | -4.30 |
| FX_MR_STACK_12 | +1.44 | +18.2 | -18.9 | -5.16 |
| FX_MR_STACK_4 | +1.44 | +6.1 | -6.3 | -1.72 |
| FX_MR_STACK_5 | +1.44 | +7.6 | -7.9 | -2.15 |
| FX_MR_STACK_8 | +1.44 | +12.2 | -12.6 | -3.44 |
| NO_EUR_STACK_10 | +1.36 | +14.4 | -17.3 | -4.31 |
| NO_EUR_STACK_12 | +1.36 | +17.2 | -20.7 | -5.17 |
| NO_EUR_STACK_4 | +1.36 | +5.7 | -6.9 | -1.72 |
| NO_EUR_STACK_5 | +1.36 | +7.2 | -8.6 | -2.15 |
| NO_EUR_STACK_8 | +1.36 | +11.5 | -13.8 | -3.45 |

## Portfolio candidates
| portfolio | accts | fees | E[gross] | E[net] | median net | P(net+) | P(≥1 fund) | avg fund | p95 net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| diversified_8x200_4firms_robust_only | 8 | $7660 | $160482 | $152822 | $137428 | 89% | 94% | 6.4 | $389022 |
| fast_eval_6x200_3firms_robust_only | 6 | $5500 | $137326 | $131826 | $119075 | 90% | 94% | 4.9 | $323715 |
| diversified_8x200_4firms_with_eurusd_legacy | 8 | $7660 | $128654 | $120994 | $106418 | 89% | 95% | 5.7 | $305211 |
| concentrated_safe_4x200_2firms_robust_only | 4 | $3500 | $104887 | $101387 | $91729 | 89% | 92% | 3.3 | $255595 |
| minimal_test_2x200_2firms | 2 | $1750 | $53536 | $51786 | $47107 | 85% | 89% | 1.7 | $127267 |

## Best realistic portfolio: **diversified_8x200_4firms_robust_only**
- Total fees    : $7660
- E[gross]      : $160482
- E[net]        : $152822
- Median net    : $137428
- CI95 net      : [$-7660, $389022]
- P(net+)       : 89%
- P(≥1 funded)  : 94%
- Avg funded    : 6.4 / 8
- Avg blown     : 3.6 / 8

Composition:
- A1: MFF_LIKE_STATIC_200K $200000, FX_MR_STACK, vol_target=10%
- A2: MFF_LIKE_STATIC_200K $200000, NO_EUR_STACK, vol_target=10%
- A3: FUNDINGPIPS_FAST_200K $200000, NO_EUR_STACK, vol_target=10%
- A4: FUNDINGPIPS_FAST_200K $200000, COMDOLL_STACK, vol_target=10%
- A5: FUNDED_NEXT_TRAIL_200K $200000, FX_MR_STACK, vol_target=8%
- A6: FUNDED_NEXT_TRAIL_200K $200000, COMDOLL_STACK, vol_target=8%
- A7: FTMO_SWING_200K $200000, NO_EUR_STACK, vol_target=8%
- A8: FTMO_SWING_200K $200000, COMDOLL_STACK, vol_target=8%

## Comparison vs codex hybrid_blend_8x200
- Codex original E[net] : $186195
- Realistic E[net]      : $152822
- Delta                 : $-33373 (-18%)

Sources of delta:
- Drop FRAGILE H1 picks (per validation)
- 1.5% slippage haircut
- Multi-firm constraint (different rule mixes per firm)

## Deployment recommendation

**This realistic estimate replaces codex hybrid_blend_8x200 as the operational target.**

Buy challenges sequentially, not in parallel:
1. Start with MFF_LIKE_STATIC_200K → FX_MR_STACK
2. After Phase 1 pass, buy next account at SAME firm if cap allows
3. After full cap at firm 1, move to firm 2
4. Repeat across 3-4 different firms over 6-12 months

Forward verification protocol:
- First 30 days demo paper-trade with full signal pipeline (news gate + delta orders)
- First real challenge : risk only 1 buy ($600-1100)
- If Phase 1 passes within 60 days → buy 2nd account
- If Phase 1 fails → re-evaluate signal vs realized P&L distribution
