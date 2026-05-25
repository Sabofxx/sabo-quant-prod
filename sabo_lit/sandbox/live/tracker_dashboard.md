# Prop Firm Live Tracker Dashboard

Generated: 2026-05-25T23:20:45+00:00

## Per-account health

| account | strategy | days | cum% | peak DD% | last day% | realized Sh | rolling 30d Sh | expected Sh | alerts |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| A1 | FX_MR_STACK | 2 | +0.40 | -0.80 | -0.80 | +2.24 | — | +1.51 |  |

## Slippage per account/instrument

| account::instrument | trades | mean bps | min bps | max bps |
|---|---:|---:|---:|---:|
| A1::EURUSD | 1 | +0.9 | +0.9 | +0.9 |

## Active alerts

None. Healthy.

## Rules of engagement

- If DAILY_NEAR_LIMIT or TRAILING_DD_NEAR_LIMIT : **stop trading account today**
- If ROLLING_SHARPE_BELOW_CI for 15+ consecutive days : **halve target_vol**
- If 30-day cum P&L outside expected CI95 : **review signal logic**
- After every trade : log fill price + slippage via `--add-trade`
- After every UTC close : log daily P&L pct via `--add-daily`