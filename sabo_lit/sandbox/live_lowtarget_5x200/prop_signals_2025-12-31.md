# Prop Firm Daily Signals

Generated at: 2026-05-22T12:05:54+00:00
As of close: 2025-12-31
Portfolio: cashmax_5x_200k_diversified

## Accounts

| account | strategy | target vol | leverage | realized vol | gross notional | lock |
|---|---|---:|---:|---:|---:|---|
| L1 | FX_MR_STACK | 10% | 4.87x | 2.05% | $974116 |  |
| L2 | EURUSD_MR5 | 10% | 3.22x | 3.11% | $643938 |  |
| L3 | NO_EUR_STACK | 10% | 4.44x | 2.25% | $888270 |  |
| L4 | COMDOLL_STACK | 10% | 3.06x | 3.27% | $612243 |  |
| L5 | FAST_STACK | 10% | 3.79x | 2.64% | $758210 |  |

## Non-Flat Orders

| account | strategy | symbol | side | notional | pct account | source |
|---|---|---|---|---:|---:|---|
| L1 | FX_MR_STACK | EURUSD | LONG | $162353 | 81.18% | EURUSD_MR5 |
| L1 | FX_MR_STACK | GBPUSD | LONG | $162353 | 81.18% | GBPUSD_MR3 |
| L1 | FX_MR_STACK | USDJPY | LONG | $162353 | 81.18% | USDJPY_MR10 |
| L1 | FX_MR_STACK | AUDUSD | LONG | $162353 | 81.18% | AUDUSD_MR21 |
| L1 | FX_MR_STACK | NZDUSD | SHORT | $-162353 | -81.18% | NZDUSD_MR10 |
| L1 | FX_MR_STACK | USDCAD | SHORT | $-162353 | -81.18% | USDCAD_MR3 |
| L2 | EURUSD_MR5 | EURUSD | LONG | $643938 | 321.97% | EURUSD_MR5 |
| L3 | NO_EUR_STACK | GBPUSD | LONG | $177654 | 88.83% | GBPUSD_MR3 |
| L3 | NO_EUR_STACK | USDJPY | LONG | $177654 | 88.83% | USDJPY_MR10 |
| L3 | NO_EUR_STACK | AUDUSD | LONG | $177654 | 88.83% | AUDUSD_MR21 |
| L3 | NO_EUR_STACK | NZDUSD | SHORT | $-177654 | -88.83% | NZDUSD_MR10 |
| L3 | NO_EUR_STACK | USDCAD | SHORT | $-177654 | -88.83% | USDCAD_MR3 |
| L4 | COMDOLL_STACK | AUDUSD | LONG | $204081 | 102.04% | AUDUSD_MR21 |
| L4 | COMDOLL_STACK | NZDUSD | SHORT | $-204081 | -102.04% | NZDUSD_MR10 |
| L4 | COMDOLL_STACK | USDCAD | SHORT | $-204081 | -102.04% | USDCAD_MR3 |
| L5 | FAST_STACK | EURUSD | LONG | $252737 | 126.37% | EURUSD_MR3 |
| L5 | FAST_STACK | GBPUSD | LONG | $252737 | 126.37% | GBPUSD_MR3 |
| L5 | FAST_STACK | USDCAD | SHORT | $-252737 | -126.37% | USDCAD_MR3 |

## FX Lot Estimates

| account | symbol | side | est lots | sizing note |
|---|---|---|---:|---|
| L1 | EURUSD | LONG | 1.3823 | FX quote-USD estimate |
| L1 | GBPUSD | LONG | 1.2051 | FX quote-USD estimate |
| L1 | USDJPY | LONG | 1.6235 | FX base-USD estimate |
| L1 | AUDUSD | LONG | 2.4331 | FX quote-USD estimate |
| L1 | NZDUSD | SHORT | 2.8207 | FX quote-USD estimate |
| L1 | USDCAD | SHORT | 1.6235 | FX base-USD estimate |
| L2 | EURUSD | LONG | 5.4824 | FX quote-USD estimate |
| L3 | GBPUSD | LONG | 1.3187 | FX quote-USD estimate |
| L3 | USDJPY | LONG | 1.7765 | FX base-USD estimate |
| L3 | AUDUSD | LONG | 2.6624 | FX quote-USD estimate |
| L3 | NZDUSD | SHORT | 3.0866 | FX quote-USD estimate |
| L3 | USDCAD | SHORT | 1.7765 | FX base-USD estimate |
| L4 | AUDUSD | LONG | 3.0585 | FX quote-USD estimate |
| L4 | NZDUSD | SHORT | 3.5457 | FX quote-USD estimate |
| L4 | USDCAD | SHORT | 2.0408 | FX base-USD estimate |
| L5 | EURUSD | LONG | 2.1518 | FX quote-USD estimate |
| L5 | GBPUSD | LONG | 1.8760 | FX quote-USD estimate |
| L5 | USDCAD | SHORT | 2.5274 | FX base-USD estimate |

## Execution Notes

- This file gives target exposure, not delta orders. Compare with current broker positions before trading.
- If an account lock is active, all target notionals are forced to zero.
- Broker symbols in config are placeholders until replaced with exact firm symbols.
- CFD index sizing depends on the firm's contract specification; use target notional until broker multipliers are configured.