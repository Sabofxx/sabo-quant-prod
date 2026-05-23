# EURUSD Root Cause Diagnostic — Asia London Sweep (relax_all)

Source: `asia_london_trades_v2_relax_all.json`. EURUSD n=32, GBPUSD n=43, USDJPY n=12.

Baseline EURUSD: {'n': 32, 'wr': 34.375, 'pf': 0.9025578590984593, 'exp': -0.061863631933296866}.

## H1 — Asia range too wide = trend day, not trap

EURUSD breakdown (tertiles on asia_range_pips):
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| low(<22) | 10 | 40.0 | 1.05 | +0.030 |
| mid(22-34) | 11 | 45.5 | 1.45 | +0.248 |
| hi(>34) | 11 | 18.2 | 0.40 | -0.455 |

GBPUSD/USDJPY contrast (tertiles per pair):
GBPUSD:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| low(<29) | 14 | 50.0 | 1.31 | +0.154 |
| mid(29-37) | 14 | 42.9 | 1.36 | +0.183 |
| hi(>37) | 15 | 66.7 | 3.36 | +0.785 |
USDJPY:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| low(<30) | 4 | 25.0 | 0.37 | -0.469 |
| mid(30-43) | 4 | 75.0 | 6.03 | +1.258 |
| hi(>43) | 4 | 25.0 | 0.49 | -0.349 |

verdict: CONFIRMED (spread 0.70R)
mechanism: EURUSD hi-bucket carries large negative exp_R vs mid; wide Asia ranges = mean-reversion fails, sweep extends into trend.

## H2 — Sweep depth (shallow = noise, deep = real hunt)

EURUSD breakdown:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| 2-3p | 8 | 37.5 | 1.16 | +0.084 |
| 3-5p | 14 | 28.6 | 0.68 | -0.227 |
| 5-10p | 7 | 42.9 | 1.20 | +0.117 |
| 10+p | 1 | 0.0 | 0.00 | -1.000 |

GBPUSD/USDJPY contrast:
GBPUSD:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| 2-3p | 16 | 50.0 | 1.09 | +0.043 |
| 3-5p | 13 | 61.5 | 3.23 | +0.699 |
| 5-10p | 9 | 33.3 | 1.03 | +0.019 |
| 10+p | 5 | 80.0 | 7.54 | +1.307 |
USDJPY:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| 2-3p | 2 | 100.0 | inf | +1.559 |
| 3-5p | 8 | 25.0 | 0.71 | -0.218 |
| 5-10p | 1 | 100.0 | inf | +1.124 |
| 10+p | 0 | 0.0 | 0.00 | +0.000 |

sweep_depth distribution (pips):
  EURUSD  median=4.1  mean=4.3  max=10.8
  GBPUSD  median=3.6  mean=5.1  max=23.4
  USDJPY  median=3.8  mean=3.6  max=5.2

verdict: REJECTED (spread 0.34R)
mechanism: Sweep depth not predictive on EURUSD.

## H3 — FVG depth ratio vs Asia range (entry placement quality)

EURUSD breakdown:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| <5% | 12 | 50.0 | 1.85 | +0.427 |
| 5-10% | 9 | 33.3 | 0.71 | -0.191 |
| 10-20% | 6 | 16.7 | 0.24 | -0.630 |
| 20+% | 5 | 20.0 | 0.52 | -0.321 |

GBPUSD/USDJPY contrast:
GBPUSD:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| <5% | 25 | 60.0 | 2.53 | +0.613 |
| 5-10% | 11 | 45.5 | 0.82 | -0.085 |
| 10-20% | 3 | 33.3 | 0.92 | -0.050 |
| 20+% | 4 | 50.0 | 2.13 | +0.565 |
USDJPY:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| <5% | 6 | 16.7 | 0.30 | -0.552 |
| 5-10% | 5 | 60.0 | 2.65 | +0.659 |
| 10-20% | 0 | 0.0 | 0.00 | +0.000 |
| 20+% | 1 | 100.0 | inf | +1.776 |

verdict: CONFIRMED (spread 1.06R)
mechanism: EURUSD entries with FVG ratio 10-20% of Asia range underperform; too-small FVG places entry near sweep extreme, SL too close.

## H4 — H1 context extremeness (marginal vs strong)

context_extremeness in [0,1]; 0 = entry near mid-band (marginal), 1 = at extreme (strong)

EURUSD breakdown:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| weak[0-0.2) | 9 | 44.4 | 1.46 | +0.220 |
| mid[0.2-0.5) | 7 | 14.3 | 0.36 | -0.552 |
| strong[0.5-1]) | 16 | 37.5 | 0.99 | -0.006 |

GBPUSD/USDJPY contrast:
GBPUSD:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| weak[0-0.2) | 12 | 50.0 | 1.79 | +0.337 |
| mid[0.2-0.5) | 8 | 25.0 | 0.47 | -0.399 |
| strong[0.5-1]) | 23 | 65.2 | 2.95 | +0.680 |
USDJPY:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| weak[0-0.2) | 2 | 0.0 | 0.00 | -1.000 |
| mid[0.2-0.5) | 4 | 50.0 | 1.60 | +0.301 |
| strong[0.5-1]) | 6 | 50.0 | 1.93 | +0.426 |

verdict: CONFIRMED (spread 0.77R)
mechanism: EURUSD 'mid[0.2-0.5)' context lose vs 'weak[0-0.2)'; backtest H1 filter (just upper/lower half) is too permissive.

## H5 — Direction bias (shorts vs longs)

EURUSD breakdown:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| short | 14 | 35.7 | 0.94 | -0.037 |
| long | 18 | 33.3 | 0.87 | -0.081 |

GBPUSD/USDJPY contrast:
GBPUSD:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| short | 26 | 53.8 | 1.93 | +0.427 |
| long | 17 | 52.9 | 1.76 | +0.317 |
USDJPY:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| short | 8 | 37.5 | 1.21 | +0.129 |
| long | 4 | 50.0 | 1.42 | +0.182 |

EURUSD per-year direction breakdown:
| year | n_short | exp_short | n_long | exp_long |
|---|---|---|---|---|
| 2019 | 1 | -1.000 | 1 | -1.000 |
| 2020 | 5 | +0.280 | 3 | +0.923 |
| 2021 | 1 | -1.000 | 0 | +0.000 |
| 2022 | 4 | -0.401 | 6 | -0.297 |
| 2023 | 0 | +0.000 | 3 | -0.260 |
| 2024 | 1 | +1.280 | 2 | +0.569 |
| 2025 | 2 | +0.200 | 3 | -0.601 |

verdict: REJECTED (spread 0.04R)
mechanism: No directional bias on EURUSD (both sides perform similarly).

## H6 — Sweep direction × H1 context (2×2 matrix)

Context_strong = ctx_extreme >= 0.5 ; marginal = ctx_extreme < 0.5

EURUSD breakdown:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| short_strong | 10 | 40.0 | 1.13 | +0.077 |
| short_marginal | 4 | 25.0 | 0.57 | -0.322 |
| long_strong | 6 | 33.3 | 0.78 | -0.145 |
| long_marginal | 12 | 33.3 | 0.92 | -0.049 |

GBPUSD/USDJPY contrast:
GBPUSD:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| short_strong | 18 | 55.6 | 2.04 | +0.464 |
| short_marginal | 8 | 50.0 | 1.69 | +0.344 |
| long_strong | 5 | 100.0 | inf | +1.457 |
| long_marginal | 12 | 33.3 | 0.73 | -0.159 |
USDJPY:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| short_strong | 3 | 33.3 | 1.41 | +0.277 |
| short_marginal | 5 | 40.0 | 1.07 | +0.041 |
| long_strong | 3 | 66.7 | 3.34 | +0.576 |
| long_marginal | 1 | 0.0 | 0.00 | -1.000 |

verdict: REJECTED (spread 0.40R)
mechanism: No specific direction×context combination dominates EURUSD losses.

## H7 — DXY proxy regime (EUR trend over last 120 H1 bars / ~5 days)

EUR_up_DXY_down: delta_EUR > +50p over 120 H1 ; EUR_down_DXY_up: < -50p ; ranging: else

EURUSD breakdown:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| EUR_up_DXY_down | 11 | 18.2 | 0.29 | -0.580 |
| ranging | 12 | 41.7 | 1.07 | +0.042 |
| EUR_down_DXY_up | 9 | 44.4 | 1.90 | +0.433 |

GBPUSD/USDJPY contrast:
GBPUSD:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| EUR_up_DXY_down | 17 | 70.6 | 3.46 | +0.723 |
| ranging | 13 | 23.1 | 0.27 | -0.559 |
| EUR_down_DXY_up | 13 | 61.5 | 3.81 | +0.883 |
USDJPY:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| EUR_up_DXY_down | 3 | 100.0 | inf | +1.442 |
| ranging | 4 | 25.0 | 0.45 | -0.414 |
| EUR_down_DXY_up | 5 | 20.0 | 0.76 | -0.182 |

EURUSD regime × direction cross:
| regime | dir | n | wr% | exp_R |
|---|---|---|---|---|
| EUR_up_DXY_down | short | 6 | 16.7 | -0.600 |
| EUR_up_DXY_down | long | 5 | 20.0 | -0.556 |
| ranging | short | 3 | 33.3 | -0.201 |
| ranging | long | 9 | 44.4 | +0.123 |
| EUR_down_DXY_up | short | 5 | 60.0 | +0.736 |
| EUR_down_DXY_up | long | 4 | 25.0 | +0.054 |

verdict: CONFIRMED (spread 1.01R)
mechanism: EURUSD setup works in 'EUR_down_DXY_up' regime, fails in 'EUR_up_DXY_down'; setup is regime-dependent (not direction-agnostic).

## H8 — Sweep hour (UTC) bias

EURUSD breakdown:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| 6 | 16 | 25.0 | 0.55 | -0.320 |
| 7 | 12 | 58.3 | 2.43 | +0.596 |
| 8 | 4 | 0.0 | 0.00 | -1.000 |

GBPUSD/USDJPY contrast:
GBPUSD:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| 6 | 22 | 50.0 | 1.69 | +0.315 |
| 7 | 15 | 46.7 | 1.36 | +0.190 |
| 8 | 6 | 83.3 | 7.71 | +1.118 |
USDJPY:
| bucket | n | wr% | pf | exp_R |
|---|---|---|---|---|
| 6 | 3 | 33.3 | 0.77 | -0.132 |
| 7 | 7 | 57.1 | 2.39 | +0.594 |
| 8 | 2 | 0.0 | 0.00 | -1.000 |

verdict: CONFIRMED (spread 1.60R)
mechanism: EURUSD sweep hour 8 loses vs 7; specific London sub-session matters.

## SYNTHESIS

Hypothèses confirmées (ranked by effect size):
  1. H8 (h8_sweep_hour): spread=1.60R — EURUSD sweep hour 8 loses vs 7; specific London sub-session matters.
  2. H3 (h3_fvg_ratio): spread=1.06R — EURUSD entries with FVG ratio 10-20% of Asia range underperform; too-small FVG places entry near sweep extreme, SL too close.
  3. H7 (h7_dxy_regime): spread=1.01R — EURUSD setup works in 'EUR_down_DXY_up' regime, fails in 'EUR_up_DXY_down'; setup is regime-dependent (not direction-agnostic).
  4. H4 (h4_h1_context_marginal): spread=0.77R — EURUSD 'mid[0.2-0.5)' context lose vs 'weak[0-0.2)'; backtest H1 filter (just upper/lower half) is too permissive.
  5. H1 (h1_asia_range_too_wide): spread=0.70R — EURUSD hi-bucket carries large negative exp_R vs mid; wide Asia ranges = mean-reversion fails, sweep extends into trend.

Hypothèses inconclusives (n trop faible):
  (none)

Hypothèses rejetées:
  - H2 (h2_sweep_depth): spread=0.34R
  - H5 (h5_direction_bias): spread=0.04R
  - H6 (h6_direction_x_context): spread=0.40R

Mécanisme dominant: EURUSD sweep hour 8 loses vs 7; specific London sub-session matters. (effect size 1.60R)

Implications:
  - EURUSD: apply filter from top confirmed hypothesis (h8_sweep_hour)
  - GBPUSD/JPY: contrast tables above show if filter transfers ; usually edge already
    healthy on GBPUSD so transfer may shrink N without gain
  - Setup global: keep as-is for GBPUSD/JPY, gate EURUSD with new filter

Filter proposed:
  EURUSD only: restrict sweep hour to winning bucket per H8
