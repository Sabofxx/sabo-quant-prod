# FX Baseline Statistical Validation

Same 3-test framework as `validate_codex_picks` applied to FX BASELINE specs.
Goal: confirm baselines pass the same statistical bar before deployment.

## Summary table
| spec | val_OOS Sh | held-out Sh | CI95 low | null p95 | verdict |
|---|---:|---:|---:|---:|---|
| FX_MR_STACK | +1.49 | +2.28 | +0.39 | +0.94 | **ROBUST** |
| EURUSD_MR5 | +1.40 | -1.65 | +0.02 | +0.86 | **PROBABLE** |
| NO_EUR_STACK | +1.30 | +3.07 | +0.25 | +0.93 | **ROBUST** |
| COMDOLL_STACK | +1.28 | +1.28 | +0.10 | +0.92 | **ROBUST** |

### FX_MR_STACK
- Full OOS    : Sh +1.51, ret +5.8%, n=731
- Held-out Q4 : Sh +2.28, ret +4.3%, n=92
- Bootstrap CI95 : [+0.39, +2.63] median +1.50
- Null distrib   : p50 -0.04 / p90 +0.69 / p95 +0.94
- VERDICT: **ROBUST**
  - held_out_Sh > 0.4 : PASS (+2.28)
  - CI95 low > 0    : PASS (+0.39)
  - actual > null_p95 : PASS (actual +1.51 vs p95 +0.94)

### EURUSD_MR5
- Full OOS    : Sh +1.16, ret +6.9%, n=731
- Held-out Q4 : Sh -1.65, ret -5.8%, n=92
- Bootstrap CI95 : [+0.02, +2.27] median +1.15
- Null distrib   : p50 -0.01 / p90 +0.69 / p95 +0.86
- VERDICT: **PROBABLE**
  - held_out_Sh > 0.4 : FAIL (-1.65)
  - CI95 low > 0    : PASS (+0.02)
  - actual > null_p95 : PASS (actual +1.16 vs p95 +0.86)

### NO_EUR_STACK
- Full OOS    : Sh +1.39, ret +5.5%, n=731
- Held-out Q4 : Sh +3.07, ret +6.3%, n=92
- Bootstrap CI95 : [+0.25, +2.51] median +1.39
- Null distrib   : p50 -0.03 / p90 +0.70 / p95 +0.93
- VERDICT: **ROBUST**
  - held_out_Sh > 0.4 : PASS (+3.07)
  - CI95 low > 0    : PASS (+0.25)
  - actual > null_p95 : PASS (actual +1.39 vs p95 +0.93)

### COMDOLL_STACK
- Full OOS    : Sh +1.27, ret +6.3%, n=731
- Held-out Q4 : Sh +1.28, ret +3.8%, n=92
- Bootstrap CI95 : [+0.10, +2.34] median +1.28
- Null distrib   : p50 -0.01 / p90 +0.72 / p95 +0.92
- VERDICT: **ROBUST**
  - held_out_Sh > 0.4 : PASS (+1.28)
  - CI95 low > 0    : PASS (+0.10)
  - actual > null_p95 : PASS (actual +1.27 vs p95 +0.92)

## Verdict counts
- ROBUST: ['FX_MR_STACK', 'NO_EUR_STACK', 'COMDOLL_STACK']
- PROBABLE: ['EURUSD_MR5']
- FRAGILE: []
- LIKELY_NOISE: []

## Action
- ROBUST baselines → deploy as-is in multi-firm allocation
- PROBABLE → deploy with 75% sizing in forward test
- FRAGILE → research-only, do not deploy
- LIKELY_NOISE → drop entirely
