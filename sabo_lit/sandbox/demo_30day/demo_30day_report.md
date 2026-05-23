# 30-Day Demo Harness Report

Run date: 2026-05-22T13:19:29+00:00
Window: 2025-12-01 → 2025-12-31
Trading days: 23
Fills simulated: 897

## P&L vs Expected

- Realized cumulative portfolio P&L: `+5.62%`
- Historical expected cumulative: `+0.25%`
- Historical CI95: `[-4.27%, +4.78%]`

## Slippage

- Mean simulated slippage: `0.28 bps`
- Max simulated slippage: `1.47 bps`

## Alerts

- None

## Daily Portfolio P&L

| date | gross% | slippage% | net% | blackout |
|---|---:|---:|---:|---|
| 2025-12-01 | +0.21 | 0.014 | +0.20 |  |
| 2025-12-02 | -0.44 | 0.006 | -0.45 |  |
| 2025-12-03 | -0.83 | 0.003 | -0.84 |  |
| 2025-12-04 | +0.42 | 0.000 | +0.42 |  |
| 2025-12-05 | +0.00 | 0.010 | -0.01 | NFP |
| 2025-12-08 | +0.39 | 0.013 | +0.38 |  |
| 2025-12-09 | +0.27 | 0.009 | +0.26 |  |
| 2025-12-10 | +0.00 | 0.014 | -0.01 | FOMC,BoC |
| 2025-12-11 | -0.12 | 0.010 | -0.13 |  |
| 2025-12-12 | +0.51 | 0.002 | +0.51 |  |
| 2025-12-15 | +0.73 | 0.003 | +0.72 |  |
| 2025-12-16 | -0.30 | 0.000 | -0.30 |  |
| 2025-12-17 | +1.48 | 0.013 | +1.46 |  |
| 2025-12-18 | +0.00 | 0.009 | -0.01 | ECB,BoE |
| 2025-12-19 | +0.48 | 0.012 | +0.47 |  |
| 2025-12-22 | +1.10 | 0.014 | +1.08 |  |
| 2025-12-23 | -0.30 | 0.004 | -0.30 |  |
| 2025-12-24 | +0.34 | 0.000 | +0.34 |  |
| 2025-12-25 | -0.31 | 0.000 | -0.31 |  |
| 2025-12-26 | -0.02 | 0.004 | -0.03 |  |
| 2025-12-29 | +0.87 | 0.012 | +0.86 |  |
| 2025-12-30 | +0.58 | 0.008 | +0.57 |  |
| 2025-12-31 | +0.73 | 0.005 | +0.72 |  |

## Caveats

- This is an offline rehearsal using historical prices, not a broker demo account.
- Fills use synthetic slippage, not real spread/commission from a prop-firm platform.
- CFD multipliers are not used because production stack is FX-only.
- Use this harness to verify file flow and risk alarms; then run a real MT5/cTrader demo for execution quality.