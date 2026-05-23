# Strategy Comparator — 6 FX pairs daily, USD-basket hedge

IS: 2019 → 2023 | OOS: 2024 → 2025 (incl. partial). Aligned days: 2556

## COMPARATIVE (hedged Sharpe, vs random baseline 95p)
| strategy   | IS Sh | OOS Sh | OOS ann_ret% | OOS maxDD% | rand p95 | bootstrap OOS CI95 | verdict |
|---|---|---|---|---|---|---|---|
| TSM_21     | +0.19 | -0.70 | -2.97 | -13.24 | +0.45 | [-1.82, +0.46] | DEAD |
| TSM_63     | -0.38 | -0.19 | -0.92 | -6.25 | +0.36 | [-1.31, +0.95] | DEAD |
| TSM_126    | -0.10 | -0.51 | -2.61 | -10.55 | +0.40 | [-1.62, +0.65] | DEAD |
| CSM_21     | +0.19 | -0.63 | -2.95 | -9.92 | +0.56 | [-1.78, +0.58] | DEAD |
| CSM_63     | -0.17 | -0.04 | -0.20 | -7.42 | +0.56 | [-1.23, +1.10] | DEAD |
| MR_1d      | +0.08 | +0.32 | +1.39 | -5.80 | +0.43 | [-0.89, +1.48] | WEAK |
| MR_5d      | +0.28 | +0.72 | +3.15 | -5.31 | +0.53 | [-0.56, +1.81] | WEAK |
| TSM_63_VOL | -0.34 | -0.23 | -1.73 | -10.30 | +0.43 | [-1.36, +0.88] | DEAD |

## TSM_21 — full-sample details
  hedge_beta (IS): +0.029
  IS  hedged: Sharpe=+0.19 ann_ret=+0.92% vol=4.93% maxDD=-7.89% wr=43.1%
  OOS hedged: Sharpe=-0.70 ann_ret=-2.97% vol=4.28% maxDD=-13.24% wr=40.6%
  FULL hedged: Sharpe=-0.04 ann_ret=-0.19% calmar=-0.01
  bootstrap OOS Sharpe CI95: [-1.82, +0.46] median=-0.66
  random baseline OOS-shape: p5=-0.43 med=+0.01 p95=+0.45

## TSM_63 — full-sample details
  hedge_beta (IS): +0.230
  IS  hedged: Sharpe=-0.38 ann_ret=-1.84% vol=4.83% maxDD=-22.39% wr=43.0%
  OOS hedged: Sharpe=-0.19 ann_ret=-0.92% vol=4.87% maxDD=-6.25% wr=44.9%
  FULL hedged: Sharpe=-0.33 ann_ret=-1.58% calmar=-0.06
  bootstrap OOS Sharpe CI95: [-1.31, +0.95] median=-0.18
  random baseline OOS-shape: p5=-0.56 med=-0.09 p95=+0.36

## TSM_126 — full-sample details
  hedge_beta (IS): +0.334
  IS  hedged: Sharpe=-0.10 ann_ret=-0.45% vol=4.61% maxDD=-11.00% wr=43.1%
  OOS hedged: Sharpe=-0.51 ann_ret=-2.61% vol=5.08% maxDD=-10.55% wr=43.0%
  FULL hedged: Sharpe=-0.22 ann_ret=-1.07% calmar=-0.05
  bootstrap OOS Sharpe CI95: [-1.62, +0.65] median=-0.50
  random baseline OOS-shape: p5=-0.48 med=-0.01 p95=+0.40

## CSM_21 — full-sample details
  hedge_beta (IS): +0.042
  IS  hedged: Sharpe=+0.19 ann_ret=+0.98% vol=5.21% maxDD=-8.36% wr=42.9%
  OOS hedged: Sharpe=-0.63 ann_ret=-2.95% vol=4.68% maxDD=-9.92% wr=38.7%
  FULL hedged: Sharpe=-0.03 ann_ret=-0.14% calmar=-0.01
  bootstrap OOS Sharpe CI95: [-1.78, +0.58] median=-0.62
  random baseline OOS-shape: p5=-0.53 med=+0.02 p95=+0.56

## CSM_63 — full-sample details
  hedge_beta (IS): +0.240
  IS  hedged: Sharpe=-0.17 ann_ret=-0.87% vol=5.01% maxDD=-14.61% wr=41.7%
  OOS hedged: Sharpe=-0.04 ann_ret=-0.20% vol=5.10% maxDD=-7.42% wr=44.7%
  FULL hedged: Sharpe=-0.13 ann_ret=-0.68% calmar=-0.04
  bootstrap OOS Sharpe CI95: [-1.23, +1.10] median=-0.04
  random baseline OOS-shape: p5=-0.55 med=+0.01 p95=+0.56

## MR_1d — full-sample details
  hedge_beta (IS): -0.016
  IS  hedged: Sharpe=+0.08 ann_ret=+0.36% vol=4.74% maxDD=-17.98% wr=41.3%
  OOS hedged: Sharpe=+0.32 ann_ret=+1.39% vol=4.39% maxDD=-5.80% wr=43.2%
  FULL hedged: Sharpe=+0.14 ann_ret=+0.65% calmar=0.04
  bootstrap OOS Sharpe CI95: [-0.89, +1.48] median=+0.32
  random baseline OOS-shape: p5=-0.51 med=-0.05 p95=+0.43

## MR_5d — full-sample details
  hedge_beta (IS): -0.053
  IS  hedged: Sharpe=+0.28 ann_ret=+1.37% vol=4.92% maxDD=-16.40% wr=44.0%
  OOS hedged: Sharpe=+0.72 ann_ret=+3.15% vol=4.35% maxDD=-5.31% wr=44.9%
  FULL hedged: Sharpe=+0.39 ann_ret=+1.88% calmar=0.11
  bootstrap OOS Sharpe CI95: [-0.56, +1.81] median=+0.71
  random baseline OOS-shape: p5=-0.38 med=+0.11 p95=+0.53

## TSM_63_VOL — full-sample details
  hedge_beta (IS): +0.226
  IS  hedged: Sharpe=-0.34 ann_ret=-2.28% vol=6.74% maxDD=-27.77% wr=43.0%
  OOS hedged: Sharpe=-0.23 ann_ret=-1.73% vol=7.55% maxDD=-10.30% wr=43.9%
  FULL hedged: Sharpe=-0.30 ann_ret=-2.12% calmar=-0.06
  bootstrap OOS Sharpe CI95: [-1.36, +0.88] median=-0.23
  random baseline OOS-shape: p5=-0.53 med=-0.03 p95=+0.43

## SUMMARY
  ROBUST: (none)
  WEAK  : ['MR_1d', 'MR_5d']
  DEAD  : ['TSM_21', 'TSM_63', 'TSM_126', 'CSM_21', 'CSM_63', 'TSM_63_VOL']

  Best WEAK: MR_5d (OOS Sharpe +0.72)
  next_step: No robust spec found. Try (a) gating multi-lookback agreement, (b) regime conditional (trade only when vol-of-vol low), (c) accept FX-daily-on-6-pairs not enough universe. Best candidate = MR_5d (still weak).
