# GBPUSD Validation — Asia London Sweep v3_h3_strict

Source: `asia_london_trades_v3_v3_h3_strict.json`. n=36 GBPUSD trades. Spec figée v3_h3_strict (rr=1.2, fvg=10, bos=16, fvg_ratio_cap=0.10).

Headline: n=36, wr=55.6%, pf=1.95, exp_R=+0.399, total_R=+14.38.

## V1 WALK-FORWARD HALVES

| half | range | n | wr% | pf | exp_R | total_R |
|---|---|---|---|---|---|---|
| H1 | 2019-01-01 → 2022-06-30 | 19 | 47.4 | 1.29 | +0.140 | +2.66 |
| H2 | 2022-07-01 → 2025-12-31 | 17 | 64.7 | 2.95 | +0.689 | +11.72 |
  Δ PF = +1.66, Δ exp_R = +0.549
verdict: STRENGTHENING

## V2 PER-YEAR

| year | n | wr% | pf | exp_R | total_R | profitable |
|---|---|---|---|---|---|---|
| 2019 | 5 | 60.0 | 1.16 | +0.062 | +0.31 | Y |
| 2020 | 4 | 25.0 | 0.81 | -0.099 | -0.40 | N |
| 2021 | 8 | 37.5 | 0.78 | -0.136 | -1.09 | N |
| 2022 | 3 | 100.0 | inf | +1.945 | +5.83 | Y |
| 2023 | 2 | 50.0 | 0.50 | -0.250 | -0.50 | N |
| 2024 | 6 | 83.3 | 7.10 | +1.017 | +6.10 | Y |
| 2025 | 8 | 50.0 | 2.03 | +0.515 | +4.12 | Y |
year_dominance: 0.37
profitable_count: 4/7
verdict: INCONSISTENT

## V3 BOOTSTRAP CI (10000 resamples)

PF      CI95: [0.95, 4.15]
exp_R   CI95: [-0.027, +0.837]
wr%     CI95: [38.9, 72.2]
total_R CI95: [-0.96, +30.13]
verdict: INDISTINGUISHABLE_FROM_NOISE

## V4 EXPANDING WALK-FORWARD

| train range | train_n | train_pf | test year | test_n | test_pf | ratio test/train |
|---|---|---|---|---|---|---|
| 2019-2020 | 9 | 0.98 | 2021 | 8 | 0.78 | 0.80 |
| 2019-2021 | 17 | 0.87 | 2022 | 3 | inf | 113.66 |
| 2019-2022 | 20 | 1.51 | 2023 | 2 | 0.50 | 0.33 |
| 2019-2023 | 22 | 1.41 | 2024 | 6 | 7.10 | 5.03 |
| 2019-2024 | 28 | 1.93 | 2025 | 8 | 2.03 | 1.05 |
median(ratio test/train): 1.05
verdict: ERRATIC

## V5 DRAWDOWN PROFILE

max_DD: -3.70R
DD duration max: 475 days
DDs > 3R: 1
recovery_factor (total_R / |max_DD|): 3.89
R-sharpe (mean/std per-trade): 0.30

## V6 DISTRIBUTION

```
  [ -1.0,  -0.5)   15  ########################################
  [ -0.5,  +0.0)    1  ##
  [ +0.5,  +1.0)    5  #############
  [ +1.0,  +1.5)    5  #############
  [ +1.5,  +2.0)    4  ##########
  [ +2.0,  +2.5)    5  #############
  [ +2.5,  +3.0)    1  ##
```
skewness: +0.18
kurtosis: -1.49
modality: bimodal

## FINAL SYNTHESIS

- V1 verdict: STRENGTHENING
- V2 verdict: INCONSISTENT
- V3 verdict: INDISTINGUISHABLE_FROM_NOISE
- V4 verdict: ERRATIC

overall_grade: OVERFIT_DISGUISED
confidence: HIGH
score: -4

interpretation: OVERFIT_DISGUISED → abandon GBPUSD aussi, repenser

actionable_findings:
  - Bootstrap CI95 exp_R lower=-0.027R ≤ 0 — sample edge could be noise
  - Edge concentrated H2 (2022+) — regime-specific overfit risk
  - Expanding walk-forward erratic — per-year edge unstable
