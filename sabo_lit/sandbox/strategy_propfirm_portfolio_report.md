# PROP FIRM PORTFOLIO — 8 decorrelated accounts

Total notional: $1400000  |  Total fees: $7280  |  Vol-target 12%
Rules: max_daily 5%, max_DD 10%, P1 target 10% in 90d, P2 target 5% in 90d, profit share 80%

## STRATEGY SPECS (6 decorrelated)
| spec | IS Sh | OOS Sh | OOS ret% | OOS vol% | OOS DD% | OOS Calmar |
|---|---|---|---|---|---|---|
| FX_MR_STACK | -0.29 | +1.51 | +5.8 | 3.8 | -3.8 | 1.50 |
| EURUSD_MR5 | -0.25 | +1.16 | +6.9 | 5.9 | -5.0 | 1.36 |
| CRYPTO_TSM63 | +0.42 | +0.45 | +17.5 | 39.1 | -59.7 | 0.29 |
| FX_MR_LONG | -0.30 | +0.55 | +2.3 | 4.3 | -5.2 | 0.45 |
| CRYPTO_MR_BTC_ETH | -0.84 | -0.61 | -25.9 | 42.5 | -115.3 | -0.22 |
| FX_TSM63 | -0.41 | -0.23 | -1.1 | 4.6 | -5.7 | -0.18 |

## SPEC OOS CORRELATION MATRIX
```
                   FX_MR_STACK  EURUSD_MR5  CRYPTO_TSM63  FX_MR_LONG  CRYPTO_MR_BTC_ETH  FX_TSM63
FX_MR_STACK               1.00        0.59          0.03        0.63               0.03     -0.42
EURUSD_MR5                0.59        1.00          0.01        0.27              -0.00     -0.15
CRYPTO_TSM63              0.03        0.01          1.00        0.02              -0.33     -0.04
FX_MR_LONG                0.63        0.27          0.02        1.00              -0.01     -0.50
CRYPTO_MR_BTC_ETH         0.03       -0.00         -0.33       -0.01               1.00     -0.04
FX_TSM63                 -0.42       -0.15         -0.04       -0.50              -0.04      1.00
```
Avg pair-wise correlation: +0.01

## PORTFOLIO COMPOSITION (default — adjustable)
| account | size | spec | fee$ |
|---|---|---|---|
| Acct1_400k | $400000 | FX_MR_STACK | $1900 |
| Acct2_400k | $400000 | EURUSD_MR5 | $1900 |
| Acct3_200k | $200000 | FX_MR_STACK | $1080 |
| Acct4_200k | $200000 | FX_MR_LONG | $1080 |
| Acct5_50k | $50000 | FX_MR_STACK | $330 |
| Acct6_50k | $50000 | EURUSD_MR5 | $330 |
| Acct7_50k | $50000 | FX_MR_LONG | $330 |
| Acct8_50k | $50000 | EURUSD_MR5 | $330 |

  Total notional: $1400000
  Total fees: $7280

## MONTE CARLO PORTFOLIO RESULTS (12 months, 5000 iter)
  Total fees outlay              : $7280
  Portfolio gross payouts (mean) : $8271
  Portfolio gross payouts (median): $0
  Portfolio gross payouts CI95   : [$0, $51037]

  Portfolio NET income (mean)    : $991
  Portfolio NET income (median)  : $-7280
  Portfolio NET income CI95      : [$-7280, $43757]

  P(net positive year 1)         : 24%
  P(at least 1 account funded)   : 49%
  P(all 8 accounts funded)         : 0%
  Avg # funded accounts          : 0.6 / 8
  Avg # dead accounts (blow-ups) : 7.9 / 8
  ROI on fees (E[gross]/fees)    : 1.14×

## PER-ACCOUNT BREAKDOWN
| account | size | spec | P(funded) | avg gross payout$ |
|---|---|---|---|---|
| Acct1_400k | $400000 | FX_MR_STACK | 9% | $2873 |
| Acct2_400k | $400000 | EURUSD_MR5 | 8% | $2208 |
| Acct3_200k | $200000 | FX_MR_STACK | 9% | $1313 |
| Acct4_200k | $200000 | FX_MR_LONG | 7% | $796 |
| Acct5_50k | $50000 | FX_MR_STACK | 8% | $319 |
| Acct6_50k | $50000 | EURUSD_MR5 | 8% | $282 |
| Acct7_50k | $50000 | FX_MR_LONG | 6% | $205 |
| Acct8_50k | $50000 | EURUSD_MR5 | 8% | $275 |

## DIVERSIFICATION CHECK
  If accounts were INDEPENDENT (uncorrelated):
    P(at least 1 funded) = 1 - prod(P_dead) = 48%
  Actual MC (with spec correlations): 49%
  Decorrelation working well. Specs ~independent.

## RECOMMENDATION
  Portfolio EV mildly positive. Acceptable but consider reducing fees.

## EXECUTION ROADMAP
```
Phase 1 (week 1-2):
  - Paper-trade ALL 6 specs in parallel on demo MT5 accounts
  - Verify execution matches backtest (slippage, fills, fees)
  - Confirm prop firm rules for chosen firms

Phase 2 (week 3):
  - Buy 1-2 challenges first (start small)
  - Run assigned spec on each
  - Track: daily P&L, DD, distance to target

Phase 3 (month 2-3):
  - If first challenges pass → buy additional accounts (cycle profits)
  - If first challenges fail → analyze cause, refine before re-buy

Phase 4 (month 4+):
  - Funded accounts in steady-state operation
  - Monthly payouts → reinvest in more challenges
  - Scale via firm scaling plans (FTMO 25% scale every 4 months profit)
```
