# Multi-Asset Adaptive Test

Question: does crypto improve the final FX prop-firm stack under ADAPTIVE_75_50?

## Crypto Candidates

| spec | period | Sharpe | ann% | DD% | Calmar | worst day% |
|---|---|---:|---:|---:|---:|---:|
| CRYPTO_TSM63 | FULL | +0.92 | +9.6 | -17.8 | 0.54 | -3.7 |
| CRYPTO_TSM63 | OOS_2024_2025 | -0.13 | -1.1 | -17.3 | -0.06 | -2.4 |
| CRYPTO_TSM126 | FULL | +0.98 | +12.2 | -17.1 | 0.71 | -3.9 |
| CRYPTO_TSM126 | OOS_2024_2025 | -0.32 | -2.5 | -17.1 | -0.15 | -2.7 |
| CRYPTO_MR5 | FULL | -0.39 | -4.2 | -47.5 | -0.09 | -7.7 |
| CRYPTO_MR5 | OOS_2024_2025 | +0.12 | +1.0 | -8.9 | 0.11 | -2.3 |
| CRYPTO_MR10 | FULL | -0.62 | -6.3 | -58.6 | -0.11 | -7.6 |
| CRYPTO_MR10 | OOS_2024_2025 | -0.54 | -3.6 | -9.4 | -0.38 | -1.9 |

## FX + Crypto Portfolios

| portfolio | period | Sharpe | ann% | DD% | Calmar | worst day% |
|---|---|---:|---:|---:|---:|---:|
| FX_ONLY | FULL | +0.43 | +3.5 | -27.1 | 0.13 | -3.0 |
| FX_ONLY | OOS_2024_2025 | +1.56 | +13.7 | -11.2 | 1.22 | -2.0 |
| CRYPTO_TSM126 | FULL | +0.98 | +12.2 | -17.1 | 0.71 | -3.9 |
| CRYPTO_TSM126 | OOS_2024_2025 | -0.32 | -2.5 | -17.1 | -0.15 | -2.7 |
| FX_90_CRYPTO_10 | FULL | +0.55 | +4.0 | -19.5 | 0.20 | -2.7 |
| FX_90_CRYPTO_10 | OOS_2024_2025 | +1.53 | +12.2 | -10.2 | 1.19 | -1.8 |
| FX_80_CRYPTO_20 | FULL | +0.67 | +4.5 | -15.1 | 0.30 | -2.5 |
| FX_80_CRYPTO_20 | OOS_2024_2025 | +1.47 | +10.6 | -9.2 | 1.16 | -1.6 |
| FX_70_CRYPTO_30 | FULL | +0.79 | +5.0 | -14.1 | 0.36 | -2.2 |
| FX_70_CRYPTO_30 | OOS_2024_2025 | +1.38 | +9.1 | -8.2 | 1.11 | -1.5 |

## Verdict

- Best crypto candidate: `CRYPTO_TSM126`
- Best FX+crypto mix: `FX_70_CRYPTO_30`
- FX full Sharpe: `+0.43`
- FX OOS Sharpe: `+1.56`
- Best mix full Sharpe: `+0.79`
- Best mix OOS Sharpe: `+1.38`
- Best crypto OOS Sharpe: `-0.32`
- Add crypto to production now: **NO**

Rule: crypto must improve full-sample Sharpe by at least +0.15, keep OOS Sharpe within -0.10 of FX-only, and have positive standalone OOS Sharpe. Otherwise it stays research-only.

## Caveats

- Crypto is tested on spot daily bars; prop-firm crypto weekend/news rules differ by firm.
- Cost model is a flat 0.20% round-trip; real prop spreads can be worse.
- This does not override the production rule: current live stack remains FX-only unless improvement is large and stable.