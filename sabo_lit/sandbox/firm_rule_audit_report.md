# Firm Rule Audit — ADAPTIVE_75_50

Run date: 2026-05-22T13:10:16.623076+00:00
Backtest window: 2019-01-01 → 2025-12-31

Purpose: detect prop-firm operational rules that can invalidate the strategy even if edge is positive.
This is a compatibility audit, not a new alpha backtest.

## Verdict Summary

| firm template | verdict | confidence | blowups | inactivity | daily DD | overall DD | payout blocks | max no-trade gap |
|---|---|---|---:|---:|---:|---:|---:|---:|
| FTMO_2STEP_NORMAL | **COMPATIBLE** | verified | 12 | 0 | 0 | 12 | 0/287 | 3d |
| FTMO_SWING_TEMPLATE | **COMPATIBLE_VERIFY** | template_only | 12 | 0 | 0 | 12 | 0/287 | 3d |
| FUNDEDNEXT_CFD | **COMPATIBLE** | verified | 12 | 0 | 0 | 12 | 0/292 | 3d |
| FUNDINGPIPS_ZERO | **ABANDON** | verified | 142 | 0 | 44 | 98 | 9720/10368 | 3d |
| E8_SIGNATURE_FOREX | **RISKY** | partly_verified | 311 | 0 | 332 | 311 | 939/1087 | 3d |

## Firm Details

### FTMO_2STEP_NORMAL

- Verdict: **COMPATIBLE**
- Source: https://ftmo.com/en/trading-objectives/
- Confidence: `verified`
- Targets: `(0.1, 0.05)` | daily DD `5.0%` | overall DD `10.0%` | mode `static`
- Min trading days/phase: `4` | inactivity: `None` | profitable-day gate: `0` days at `0.00%`
- Totals: blowups `12`, phase passes `18`, funded days `6102`, payouts `287`
- Reasons:
  - no modeled operational blocker
- Notes:
  - Minimum 4 trading days applies during Challenge and Verification.
  - No minimum trading days after the 2-step FTMO Account is reached.

### FTMO_SWING_TEMPLATE

- Verdict: **COMPATIBLE_VERIFY**
- Source: https://ftmo.com/en/trading-objectives/
- Confidence: `template_only`
- Targets: `(0.1, 0.05)` | daily DD `5.0%` | overall DD `10.0%` | mode `static`
- Min trading days/phase: `0` | inactivity: `None` | profitable-day gate: `0` days at `0.00%`
- Totals: blowups `12`, phase passes `18`, funded days `6102`, payouts `287`
- Reasons:
  - rules are template_only; manual verification required
- Notes:
  - Template assumes no minimum-day friction for swing-style deployment.
  - Verify exact Swing product terms before purchase; FTMO product rules change.

### FUNDEDNEXT_CFD

- Verdict: **COMPATIBLE**
- Source: https://help.fundednext.com/en/articles/8019664-is-there-an-inactivity-period-for-my-accounts-in-fundednext-cfd
- Confidence: `verified`
- Targets: `(0.08, 0.05)` | daily DD `5.0%` | overall DD `10.0%` | mode `static`
- Min trading days/phase: `0` | inactivity: `60` | profitable-day gate: `0` days at `0.00%`
- Totals: blowups `12`, phase passes `18`, funded days `6223`, payouts `292`
- Reasons:
  - no modeled operational blocker
- Notes:
  - 60 consecutive calendar days without placing a trade can deactivate accounts.
  - ADAPTIVE_75_50 should keep trading; full-pause variants are much riskier.

### FUNDINGPIPS_ZERO

- Verdict: **ABANDON**
- Source: https://help.fundingpips.com/hc/en-us/articles/34502157694865-FundingPips-Zero
- Confidence: `verified`
- Targets: `()` | daily DD `3.0%` | overall DD `5.0%` | mode `trailing_eod`
- Min trading days/phase: `0` | inactivity: `30` | profitable-day gate: `7` days at `0.25%`
- Totals: blowups `142`, phase passes `0`, funded days `20306`, payouts `439`
- Reasons:
  - 44 strict daily-risk events
  - 98 strict/trailing overall-DD violations
  - 9720/10368 payout checks blocked by profitable-day gate
- Notes:
  - No evaluation phase; direct funded model.
  - 3% daily loss, 5% trailing drawdown, 30-day inactivity, 7 profitable days per rolling 30-day period.
  - Weekend non-crypto holds and news restrictions are not modeled here.
  - Max-risk-per-trade rule is not modeled and must be handled by execution sizing.

### E8_SIGNATURE_FOREX

- Verdict: **RISKY**
- Source: https://help.e8markets.com/en/articles/11755943-e8-signature-forex
- Confidence: `partly_verified`
- Targets: `(0.06,)` | daily DD `2.0%` | overall DD `3.0%` | mode `trailing_eod`
- Min trading days/phase: `0` | inactivity: `60` | profitable-day gate: `5` days at `0.30%`
- Totals: blowups `311`, phase passes `74`, funded days `3676`, payouts `148`
- Reasons:
  - 332 strict daily-risk events
  - 311 strict/trailing overall-DD violations
  - 939/1087 payout checks blocked by profitable-day gate
  - rules are partly_verified; manual verification required
- Notes:
  - Official source confirms 60-day activity rule, 2% daily pause, and 0.3% profitable-day definition.
  - The 5-profitable-day payout count is treated as user-provided and must be re-verified.
  - EOD dynamic drawdown details vary by product; this template uses 3% as a conservative placeholder.

## Direct Answer

- **Most strict for this strategy:** `FUNDINGPIPS_ZERO` and `E8_SIGNATURE_FOREX` because they add profitable-day gates and stricter daily/trailing drawdown.
- **Most compatible:** `FTMO_2STEP_NORMAL` / `FUNDEDNEXT_CFD` under ADAPTIVE_75_50, assuming exact symbols, payout rules, and news rules are verified before purchase.
- **Pause-friendly conclusion:** ADAPTIVE_75_50 never fully pauses, so inactivity rules are not the blocker. Full-pause variants would be materially riskier for FundedNext/FundingPips/E8.
- **Manual verification still required:** broker dashboard must confirm exact inactivity, payout, consistency, news, weekend, copy-trading, and max-risk-per-trade rules before buying a challenge.

## Known Limitations

- Does not model firm-specific consistency rules such as best-day caps.
- Does not model swap, commissions beyond strategy cost assumptions, weekend restrictions, or exact server-time cutoff.
- Does not model FundingPips max-risk-per-trade grouping rule; execution must enforce it separately.
- E8 template is intentionally conservative because product rules vary heavily by account type and region.