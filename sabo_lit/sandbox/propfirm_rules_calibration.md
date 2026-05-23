# Prop Firm Rules Calibration Sheet

Human-checkable reference for the 4 firm templates modeled in
`strategy_propfirm_multifirm.py`. Verify each row against the actual firm rules
page BEFORE purchasing a challenge.

Rules vary frequently. Last checked: 2026-05 (template defaults). Confirm live.

## Template assumptions vs reality

### FTMO_SWING_200K

Modeled assumption | Verify against actual FTMO Swing $200k page
---|---
Fee: **$1,080** | Confirm — varies by promo, sometimes $1,180-1,250
Phase 1 target: **10%** in unlimited days | FTMO Swing: 10% no time limit ✓
Phase 2 target: **5%** in unlimited days | ✓
Max daily loss: **5%** EOD | Confirm: FTMO uses EOD, not intraday ✓
Max overall: **10% trailing EOD** | FTMO Swing trails EOD ✓
Profit share: **80%** (scales to 90%) | First payout 80%, escalates ✓
News trading: **allowed on Swing** | FTMO Normal forbids ; Swing allows
Weekend holding: **allowed on Swing** | Normal forbids ; Swing allows
Min trading days: **none on Swing** | Normal requires 4 days/phase
Payout cycle: **every 30 days** | First payout 14 days after funded, then every 30
Refund fee on first payout: **NO** | Some promo codes do refund
Scaling plan: **+25% every 4 months** if profit > 10% | Verify current rules
Max allocation per trader: **$400k** | Hard cap on Swing
Copy trading: **prohibited cross-firm** | Common rule
What breaks if mismatch | If rule is actually intraday DD or shorter timeline → halt deployment

### MFF_LIKE_STATIC_200K (template for static-DD firms like FundingPips Pro, E8 etc)

Modeled assumption | Verify against actual firm page
---|---
Fee: **$900** | Varies $600-1,200 by firm and promo
Phase 1 target: **5%** | Confirm — some firms use 6%, 7%, 8%
Phase 2 target: **5%** | Confirm
Max daily loss: **5%** | Usually intraday calc, check
Max overall: **10% STATIC** (from initial balance) | Critical : static vs trailing changes everything
Profit share: **80%** | Some firms 70%, some 85%
Daily profit cap during eval: **2%** | Some firms cap to prevent gambling
Refund fee on first payout: **YES** | Some firms refund, some don't
Payout cycle: **every 14-30 days** | Varies
Max allocation per trader: **$400k** | Verify
What breaks if mismatch | Trailing DD instead of static → strategy dies faster

### FUNDED_NEXT_TRAIL_200K

Modeled assumption | Verify against actual FundedNext page
---|---
Fee: **$1,000** | Varies by plan and promo
Phase 1 target: **8%** | FundedNext Stellar 1-step uses 8% ✓
Phase 2 target: **5%** | Stellar 2-step ✓
Max daily loss: **5%** | EOD typically
Max overall: **10% trailing** | Stellar uses trailing for some plans
Profit share: **85%** (up to 90% with scaling) | ✓
Refund on first payout: **YES** | Common promo
Payout cycle: **5-day biweekly or 14-day monthly** | Confirm
What breaks if mismatch | Static DD vs trailing → adjust simulation

### FUNDINGPIPS_FAST_200K

Modeled assumption | Verify against actual FundingPips page
---|---
Fee: **$850** | Promo codes can drop to $600
Phase 1 target: **8%** | FundingPips Pro uses 8% ✓
Phase 2 target: **5%** | ✓
Max daily loss: **5%** | EOD calc ✓
Max overall: **10% STATIC** | FundingPips uses static ✓
Profit share: **80%** | Starting tier
News trading: **restricted** on some plans | Critical : check news rule for Pro plan
What breaks if mismatch | Restricted news → drop news-day trades (already handled by news_calendar)

## Cross-firm operational gotchas

| Gotcha | Impact |
|---|---|
| Multiple accounts at same firm need separate IPs sometimes | VPN setup required |
| Copy-trading across firms = ban risk | Each account needs distinct signal/timing |
| Tax forms required after first payout | US residents : W-9 / 1099 ; EU : self-employment |
| Withdrawal min thresholds ($100-500 typical) | Plan payout aggregation |
| Account inactivity rules | Some firms suspend after 30 days no trades |
| Hedging same instrument across accounts | Sometimes prohibited, check |
| News calendar : firm-specific list vs ours | Verify our hardcoded calendar matches |
| Symbol naming differences MT5 broker → broker | EURUSD vs EURUSD.r vs EUR/USD etc |
| Contract multiplier for CFD indices | USTEC : 1 USD/pt vs 20 USD/pt — 20x sizing diff |

## Before first real challenge — checklist

```
[ ] Confirmed firm name, plan, fee, current promo code
[ ] Verified Phase 1 / Phase 2 targets (%)
[ ] Verified max daily loss (% and EOD vs intraday)
[ ] Verified max overall DD (% and static vs trailing)
[ ] Verified profit share %
[ ] Verified news rule (allowed / forbidden / restricted)
[ ] Verified weekend holding rule
[ ] Verified min trading days requirement
[ ] Verified payout cycle + first payout delay
[ ] Verified scaling plan (auto vs request)
[ ] Verified max allocation per trader
[ ] Verified copy-trading rule
[ ] Verified MT5 symbol naming (EURUSD vs EURUSD.r etc)
[ ] Verified CFD index multiplier (USTEC, USA30 etc)
[ ] Set up news calendar matching firm's list
[ ] Set up VPN / IP per account if multi-account same firm
[ ] Demo account first : verify signal generator output matches firm UI
[ ] First trade at micro size (0.01 lot) to verify execution mechanics
```

## Recommended firm prioritization (by simulator advantage)

| Rank | Template | Why preferred | Watch for |
|---|---|---|---|
| 1 | MFF_LIKE_STATIC_200K | Static DD = predictable, low target (5%/5%) | Daily profit cap |
| 2 | FUNDINGPIPS_FAST_200K | Cheap fee, static DD, 8% target manageable | News rule |
| 3 | FUNDED_NEXT_TRAIL_200K | High profit share 85%, refund on payout | Trailing DD |
| 4 | FTMO_SWING_200K | Most respected, swing trading allowed | Highest fee, trailing DD |

Suggested deployment order:
1. **MFF-like static** firm (or FundingPips static plan) for first account = lowest pressure
2. After Phase 1 pass, buy second account same firm
3. Move to FundingPips for diversification (different daily DD rule)
4. After 2-3 funded accounts, expand to FundedNext / FTMO
