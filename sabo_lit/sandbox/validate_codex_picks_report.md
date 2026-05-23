# Codex H1 Picks — Statistical Validation

Three corrections applied vs original score-function selection:

- **HELD-OUT TEST** : 2025-10-01 → 2025-12-31 never touched by codex selection
- **BOOTSTRAP CI95** : 2000 resamples on full OOS daily P&L
- **NULL DISTRIBUTION** : 1000 random ±1 signals on each instrument OOS

Verdict thresholds:
- ROBUST : held_out Sh > 0.4 AND CI low > 0 AND actual > null p95
- PROBABLE : 2 of 3 pass
- FRAGILE : 1 of 3 pass
- LIKELY_NOISE : 0 of 3 pass

## Results per pick
| pick | val OOS Sh | held-out Sh | CI95 low | null p95 | verdict |
|---|---:|---:|---:|---:|---|
| H1_NASDAQ_TREND | +0.83 | +0.46 | -0.35 | +0.80 | **FRAGILE** |
| H1_CHFJPY_BREAK | +0.15 | +1.44 | -0.65 | -0.23 | **PROBABLE** |
| H1_DOW_TSM | +0.45 | +1.08 | -0.62 | +0.76 | **FRAGILE** |

### H1_NASDAQ_TREND (usatechidxusd MA_LONG L=200)
- Full OOS    : Sharpe +0.77, ret +10.2%, n=731
- Val OOS     : Sharpe +0.83, ret +10.7%, n=639
- Held-out Q4 : Sharpe +0.46, ret +7.1%, n=92
- Bootstrap CI95: [-0.35, +1.99] median +0.80
- Null distrib  : p50 -0.08 / p90 +0.61 / p95 +0.80
- VERDICT: **FRAGILE**
  - held_out_Sh > 0.4 : PASS (+0.46)
  - CI95 low > 0    : FAIL (-0.35)
  - actual > null_p95 : FAIL (actual +0.77 vs p95 +0.80)

### H1_CHFJPY_BREAK (chfjpy DON_BREAK L=100)
- Full OOS    : Sharpe +0.46, ret +0.9%, n=731
- Val OOS     : Sharpe +0.15, ret +0.2%, n=639
- Held-out Q4 : Sharpe +1.44, ret +5.4%, n=92
- Bootstrap CI95: [-0.65, +1.53] median +0.47
- Null distrib  : p50 -1.23 / p90 -0.46 / p95 -0.23
- VERDICT: **PROBABLE**
  - held_out_Sh > 0.4 : PASS (+1.44)
  - CI95 low > 0    : FAIL (-0.65)
  - actual > null_p95 : PASS (actual +0.46 vs p95 -0.23)

### H1_DOW_TSM (usa30idxusd TSM L=5)
- Full OOS    : Sharpe +0.51, ret +6.3%, n=731
- Val OOS     : Sharpe +0.45, ret +5.8%, n=639
- Held-out Q4 : Sharpe +1.08, ret +10.2%, n=92
- Bootstrap CI95: [-0.62, +1.76] median +0.52
- Null distrib  : p50 -0.16 / p90 +0.57 / p95 +0.76
- VERDICT: **FRAGILE**
  - held_out_Sh > 0.4 : PASS (+1.08)
  - CI95 low > 0    : FAIL (-0.62)
  - actual > null_p95 : FAIL (actual +0.51 vs p95 +0.76)

## Interpretation

- ROBUST: 0 / 3
- PROBABLE: 1 / 3
- FRAGILE: 2 / 3
- LIKELY_NOISE: 0 / 3

**Recommendation per verdict**:
- ROBUST → keep in production allocation
- PROBABLE → keep with reduced sizing (50%) for 30-day forward test
- FRAGILE → research-only ; do not allocate live capital
- LIKELY_NOISE → drop from allocation immediately

**Note on null distribution interpretation**:
Per-instrument null is conservative. With 1748 candidates tested, family-wise null
(Bonferroni-style) would be wider. If pick beats per-instrument p95 but not by margin,
treat as PROBABLE not ROBUST.
