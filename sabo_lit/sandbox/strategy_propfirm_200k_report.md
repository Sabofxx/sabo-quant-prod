# PROP FIRM STRATEGY — $200000 funded account

**Base strategy**: FX equal-weight stack 6 pairs, best per-pair MR lookback
  Lookbacks: {'EURUSD': 5, 'GBPUSD': 3, 'USDJPY': 10, 'AUDUSD': 21, 'NZDUSD': 10, 'USDCAD': 3}
  Base unlevered OOS Sharpe: +1.51, Calmar 1.50

**Prop firm rules modeled (FTMO standard)**:
  - Max daily loss: 5% ($10000)
  - Max overall DD: 10% ($20000)
  - Phase 1 target: 10% in 90 days
  - Phase 2 target: 5% in 90 days
  - Funded profit share: 80%
  - Monte Carlo paths: 5000 per vol_target

## VOL-TARGET COMPARISON (under prop firm rules)
| TV | OOS Sh | OOS vol% | OOS DD% | lev | Phase1 pass% | DD_breach% | daily_breach% | avg_days_pass | Phase2 pass% | Funded survival% (12mo) | avg ann payout$ |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 3% | +1.44 | 3.2 | -4.7 | 0.9× | 0 | 0 | 0 | 0.0 | 6 | 100 | $8169 |
| 5% | +1.44 | 5.3 | -7.9 | 1.5× | 2 | 0 | 0 | 76.4 | 30 | 100 | $13594 |
| 7% | +1.44 | 7.4 | -11.0 | 2.1× | 11 | 0 | 0 | 68.5 | 49 | 97 | $19001 |
| 10% | +1.44 | 10.6 | -15.8 | 3.0× | 31 | 3 | 0 | 59.9 | 67 | 83 | $26612 |
| 12% | +1.44 | 12.7 | -18.9 | 3.6× | 42 | 8 | 0 | 55.9 | 72 | 69 | $30680 |
| 15% | +1.44 | 15.8 | -23.7 | 4.4× | 52 | 11 | 8 | 48.8 | 76 | 39 | $32456 |

## RECOMMENDED VOL_TARGET: **10%**
  Criteria: max (funded_survival × annual_payout) with pass_rate > 30%
  OOS Sharpe   : +1.44
  OOS ann_vol  : 10.6%  (daily vol ~0.66%)
  OOS maxDD    : -15.8%  ($-31534 on $200000)
  avg leverage : 3.0× (max 5.7×)

  ### Phase 1 evaluation (30 days, 10% target)
    Pass rate           : **31%**
    DD breach rate      : 3%
    Daily breach rate   : 0%
    Timeout rate        : 66%
    Avg days to pass    : 59.9
    Median days to pass : 61

  ### Phase 2 evaluation (60 days, 5% target)
    Pass rate           : **67%**

  ### Funded phase (12 months)
    Survival rate       : **83%**
    Avg monthly profit  : +1.07%
    Avg total payouts/yr: **$26612** (80% share)
    Avg # payouts/yr    : 5.9

## EXPECTED INCOME PROJECTION (first year)
  P(pass Phase 1) × P(pass Phase 2)  = 31% × 67% = 21%
  P(funded survival 12mo)            = 83%
  Expected annual payout if funded   = $26612
  Expected first-year income (E[payout × P(pass) × P(survival)]) = **$4552**

  Less Phase 1 eval fee (~$600) = E[first year net] ~$3952

  ROI on $600 eval fee: 7.6×

## EXECUTION SPEC
```
Account: $200000 funded (e.g., FTMO $200k)
Strategy: 6 FX pairs equal-weight MR with per-pair best lookback
  {'EURUSD': 5, 'GBPUSD': 3, 'USDJPY': 10, 'AUDUSD': 21, 'NZDUSD': 10, 'USDCAD': 3}

Daily at 22:00 UTC:
  1. For each pair, compute MR signal: -sign(cum_N_day_return)
  2. Compute realized 60-day portfolio vol
  3. Leverage = 0.1 / realized_vol  (capped 10.0×)
  4. Per-pair position notional = $200000 × leverage / 6 × signal
     Average notional/pair = $98708

Risk management (must match firm rules):
  - Hard stop trading day if intraday loss > $8000 (80% of daily limit = safety margin)
  - Halt strategy if total DD > $16000
  - Reduce position size 50% after 3 consecutive losing days

News management:
  - Skip trading on FOMC days (8 per year)
  - Skip trading on NFP day (12 per year)
  - Reduce size 50% during ECB / BoE / BoC meetings
```

## VERDICT: **NOT_PROP_SUITABLE**

  Strategy does not survive prop firm rules. Iterate strategy or accept retail-only.
