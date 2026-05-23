# Strategy Synthesis — Sandbox 6-FX universe, 2019-2025

## Setup
- 6 FX pairs (EURUSD, GBPUSD, USDJPY, AUDUSD, NZDUSD, USDCAD), M5 + daily resampled
- Validation stack: IS 2019-2023, OOS 2024-2025, USD-basket beta hedge IS-fitted,
  random baseline 200 iter, bootstrap CI95 1000 resamples
- Hard verdicts: ROBUST iff (OOS Sharpe > 0.5 AND > random_p95 AND CI_low > 0)

## Specs tested (11 total)

| spec        | category        | OOS Sharpe | OOS CI95 low | Random p95 | Verdict |
|-------------|-----------------|-----------:|-------------:|-----------:|---------|
| TSM_21      | momentum daily  |     -0.70  |       -1.82  |      +0.45 | DEAD    |
| TSM_63      | momentum daily  |     -0.19  |       -1.31  |      +0.36 | DEAD    |
| TSM_126     | momentum daily  |     -0.51  |       -1.62  |      +0.40 | DEAD    |
| TSM_63_VOL  | momentum daily  |     -0.23  |       -1.36  |      +0.43 | DEAD    |
| CSM_21      | cross-sec daily |     -0.63  |       -1.78  |      +0.56 | DEAD    |
| CSM_63      | cross-sec daily |     -0.04  |       -1.23  |      +0.56 | DEAD    |
| MR_1d       | mean rev daily  |     +0.32  |       -0.89  |      +0.43 | WEAK    |
| **MR_5d**   | mean rev daily  |     **+0.72**  |   -0.56  |      +0.53 | **WEAK (best)** |
| GAP_REV     | intraday        |     -0.33  |       -1.55  |      +0.56 | DEAD    |
| LDN_BO      | intraday        |     +0.26  |       -1.07  |      +0.57 | WEAK    |
| SES_MOM     | intraday        |     -0.27  |       -1.65  |      +0.65 | DEAD    |

## Findings

### 1. Trend strategies dead on 6 FX pairs 2019-2025
TSM (Moskowitz Ooi Pedersen 2012) requires broad universe. With only 6 pairs,
diversification insufficient. 4 TSM variants tested (21/63/126 lookback + vol-targeted),
all OOS Sharpe < 0. CSM (cross-sectional) same outcome.

### 2. Mean reversion shows directional signal but underpowered
MR_5d (5-day reversal) only spec with OOS Sharpe > random p95.
- IS Sharpe +0.28 / OOS Sharpe +0.72 → consistency H1/H2 OK
- ann_ret OOS +3.15% with vol 4.35% (Calmar 0.11)
- BUT bootstrap CI95 [-0.56, +1.81] → lower bound negative
- Random p95 +0.53, actual +0.72 → beats by 0.19 Sharpe

Interpretation : real signal probable but n=731 OOS days insufficient for tight CI.
**Research-grade**, not production-grade.

### 3. Intraday session strategies dead too
GAP_REV (overnight reversion), SES_MOM (Asia→London continuation) both DEAD.
LDN_BO (London open breakout) IS Sharpe 0.75 → OOS Sharpe 0.26 = strong degradation,
fails random baseline. Looks like IS overfit.

### 4. Universe insufficient
Sample size analysis : MR_5d OOS bootstrap CI half-width ~1.2 Sharpe. To halve CI
width → need 4x trades → 4x days OR 4x pairs. 4x days impossible (need 28 years
M5). 4x pairs = 24-pair G10+EM universe. **Data acquisition is the bottleneck.**

## Decision

**No ROBUST strategy found across 11 specs on 6-FX-pair universe.**

MR_5d is the strongest candidate but doesn't meet hedge-fund-grade validation.

## Next actions (ranked)

### Option A — Expand FX universe (RECOMMENDED if FX is the goal)
Acquire M5 data for G10 + EM majors : USDCHF, USDSEK, USDNOK, USDPLN, USDMXN,
USDZAR, USDTRY, EURGBP, EURJPY, GBPJPY. Total ~16-20 pairs.
- Re-run MR_5d, TSM, CSM with broader universe
- Expected: bootstrap CI tightens 2x ; if MR_5d edge real, becomes ROBUST
- Cost: Dukascopy / TickData / Polygon free tier covers most. 1-2 days work.

### Option B — Pivot crypto (FAST, free data)
Same validation stack on BTC/ETH/SOL/BNB/AVAX/MATIC daily. Crypto has stronger
MR / momentum signals empirically (lower efficiency, more retail flow). Free
data via Binance public API.
- Expected: better edge / pair, but higher risk of overfit due to short history
  (most coins < 5 yr post-2017)
- Cost: 1 day to wire API + adapt scripts

### Option C — Accept current state, live-test MR_5d at micro size
Deploy MR_5d at 0.1% of capital per pair (~$600 nominal on $10k account).
Live for 30 trading days = ~30 daily P&L observations. If live performance
within CI95, real edge confirmed. If outside, signal was noise.
- Risk: 30 days too short to reject null
- Cost: live broker + tiny size

### Option D — Stop, learn more
Read : Asness Moskowitz Pedersen 2013 "Value and Momentum Everywhere",
Lo 2002 "Adaptive Markets", Bouchaud 2018 "Trades Quotes Prices" (market
microstructure). Build statistical intuition before more code.

## My recommendation (hedge-fund-grade view)

**Option A first (expand universe).** Cheap, low-risk, directly tests whether
MR_5d signal is real or noise. Decision Friday → Monday morning :
1. Download M5 for 10 additional pairs from Dukascopy free
2. Re-run `sandbox/strategy_compare.py` with expanded universe
3. If MR_5d achieves ROBUST → Option C (live micro)
4. If still WEAK after expansion → Option B (crypto pivot)

**Do not** :
- Continue adding strategies to current 6-pair universe (diminishing returns)
- Try ML on this sample size (87 → 731 daily points = noise modeling)
- Resurrect SMC concepts (already validated as overfit)
- Build more infrastructure (validation stack is already excellent)

## Files produced this session
- `strategy_tsm_hedged.py` + report + equity + metrics (TSM_21 isolated)
- `strategy_compare.py` + report + equity + metrics (8 daily specs)
- `strategy_intraday.py` + report + equity + metrics (3 intraday specs)
- `strategy_synthesis.md` (this file)
