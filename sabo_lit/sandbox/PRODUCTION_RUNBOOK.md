# Production Runbook — FX Prop Firm System

This runbook is for the current production stack:

- Strategies: `FX_MR_STACK`, `NO_EUR_STACK`, `COMDOLL_STACK`
- Sizing: `ADAPTIVE_75_50`
- Execution: daily after the UTC 22:00 close
- Production account config: `sabo_lit/sandbox/propfirm_hybrid_8x200_accounts.json`

## 1. Pre-Live Checklist

Do not buy a challenge until all items are complete:

1. Run firm compatibility audit.
2. Verify exact firm rules inside dashboard before purchase.
3. Verify broker symbols with demo platform.
4. Generate daily signal once using `--broker`.
5. Run `demo_30day_harness.py`.
6. Run a real MT5/cTrader demo for 30 calendar days.
7. Compare realized slippage and P&L against `live_tracker.py`.

```bash
/Users/oscarmischler/sabo-quant/.venv/bin/python sabo_lit/sandbox/firm_rule_audit.py
```

Expected output:

```text
FTMO_2STEP_NORMAL      COMPATIBLE
FUNDEDNEXT_CFD         COMPATIBLE
FUNDINGPIPS_ZERO       ABANDON
E8_SIGNATURE_FOREX     RISKY
```

## 2. Daily Execution Checklist

Run after UTC 22:00 close, before opening/adjusting positions.

### One-command dry-run

Use this first. It performs the full daily workflow without sending live orders:

```bash
/Users/oscarmischler/sabo-quant/.venv/bin/python sabo_lit/sandbox/daily_ops_runner.py \
  --broker ftmo_mt5 \
  --positions sabo_lit/sandbox/propfirm_positions.example.json
```

Expected outputs:

```text
Decision: DEMO_OK_LIVE_NO_GO
sabo_lit/sandbox/live/daily_ops_report_latest.md
sabo_lit/sandbox/live/daily_ops_metrics_latest.json
```

`DEMO_OK_LIVE_NO_GO` is normal until broker symbols are verified from the real
platform. This runner never sends live orders.

### Step 1 — Update/verify news calendar

Trading Economics is used only if `TRADING_ECONOMICS_KEY` exists. Otherwise the
system falls back to the hardcoded calendar.

```bash
export TRADING_ECONOMICS_KEY="your_key_here"
/Users/oscarmischler/sabo-quant/.venv/bin/python sabo_lit/sandbox/live_news_calendar.py --start 2026-05-22 --end 2026-06-30 --refresh
```

Expected output:

```text
News events: <n> source=trading_economics
Cache: sabo_lit/sandbox/cache/live_news_calendar.json
```

If API fails, output says `source=fallback`. That is acceptable short-term, but
must be reviewed manually around FOMC/NFP/ECB/BoE/BoC weeks.

### Step 2 — Export current broker positions

For live execution, current positions must be known before delta orders are sent.
During early demo, use/edit:

```text
sabo_lit/sandbox/propfirm_positions.example.json
```

For MT5 later:

```bash
/Users/oscarmischler/sabo-quant/.venv/bin/python sabo_lit/sandbox/mt5_connector.py --dump-symbols EURUSD GBPUSD USDJPY AUDUSD NZDUSD USDCAD
```

### Step 3 — Generate target and delta orders

```bash
/Users/oscarmischler/sabo-quant/.venv/bin/python sabo_lit/sandbox/propfirm_signal_generator.py \
  --adaptive \
  --broker ftmo_mt5 \
  --positions sabo_lit/sandbox/propfirm_positions.example.json
```

Expected outputs:

```text
sabo_lit/sandbox/live/prop_signals_latest.md
sabo_lit/sandbox/live/prop_orders_latest.csv
sabo_lit/sandbox/live/prop_delta_orders_latest.csv
```

Only execute `prop_delta_orders_latest.csv`, not the full target file.

### Step 4 — Dry-run MT5 execution

```bash
/Users/oscarmischler/sabo-quant/.venv/bin/python sabo_lit/sandbox/mt5_connector.py \
  --delta-csv sabo_lit/sandbox/live/prop_delta_orders_latest.csv
```

Expected output:

```text
Orders processed: <n> dry_run=True
```

Live execution requires `--live`, MT5 installed, and platform credentials:

```bash
export MT5_LOGIN="..."
export MT5_PASSWORD="..."
export MT5_SERVER="..."
/Users/oscarmischler/sabo-quant/.venv/bin/python sabo_lit/sandbox/mt5_connector.py --live
```

Do not run `--live` before 30 days of demo execution.

### Step 5 — Log daily P&L

After the trading day closes:

```bash
/Users/oscarmischler/sabo-quant/.venv/bin/python sabo_lit/sandbox/live_tracker.py --add-daily A1 2026-05-22 0.0012
/Users/oscarmischler/sabo-quant/.venv/bin/python sabo_lit/sandbox/live_tracker.py
```

Expected outputs:

```text
sabo_lit/sandbox/live/tracker_dashboard.md
sabo_lit/sandbox/live/tracker_alerts.json
```

## 3. Weekly Review

Every weekend:

- Review `tracker_dashboard.md` for each account.
- Check rolling 30-day Sharpe vs expected CI lower bound.
- Review fill slippage in `sabo_lit/sandbox/live/mt5_fills.jsonl`.
- Confirm no firm rule changed in dashboard/email.
- Confirm next week’s FOMC/NFP/ECB/BoE/BoC dates.

If realized slippage exceeds backtest assumptions by more than 2× for two
straight weeks, halt new challenges and keep demo-only until explained.

## 4. Monthly Review

Every month:

- Compare monthly realized P&L to expected CI95.
- Count rule near-misses: daily drawdown, trailing drawdown, news lockouts.
- Review whether a firm delayed payout due to profitable-day/consistency rules.
- Re-run `firm_rule_audit.py` if firm rules changed.
- Do not retune lookbacks monthly; retuning is overfit risk.

## 5. Emergency Procedures

### Account near daily DD limit

- If daily P&L <= `-4%`, flatten account.
- Do not reopen the same day.
- Mark account manual lock in account state JSON.

### Account near overall DD limit

- If cumulative/trailing DD <= `-8%`, flatten account.
- Disable account in `propfirm_hybrid_8x200_accounts.json`.
- Review before paying for a replacement challenge.

### Broker disconnect

- Do not send manual duplicate orders blindly.
- Export current positions from platform.
- Re-run signal generator with latest positions file.
- Execute only remaining delta.

### News calendar failure

- If Trading Economics and fallback disagree or API is unavailable on a major week, manually verify ForexFactory/central bank calendar.
- When uncertain, skip. Missing one trade is cheaper than a rule breach.

## 6. System-Level Kill Switches

Halt all new challenge purchases if any condition is hit:

- Net fees + losses exceed `$15k` before first payout.
- Three accounts breach within 30 calendar days for the same strategy.
- Live slippage/commissions are 2× modeled cost for 20 trading days.
- News/platform rule breach occurs once.
- Current live 60-day Sharpe is below `-0.5` on at least two strategies.

## 7. Firm Selection

Current audit ranking:

1. `FTMO_2STEP_NORMAL`: compatible, but expensive.
2. `FUNDEDNEXT_CFD`: compatible under the modeled 60-day inactivity rule.
3. `E8_SIGNATURE_FOREX`: risky due strict daily/trailing rules and payout gates.
4. `FUNDINGPIPS_ZERO`: abandon for this system; too strict for rolling 30-day profitable-day and trailing-DD rules.

## 8. Known Limitations

- The strategy edge remains regime-dependent.
- The 2010 H1 validation does not prove `ADAPTIVE_75_50` is a standalone alpha enhancer; it is mainly a recent-regime risk overlay.
- `multi_asset_adaptive.py` found crypto full-sample strength, but crypto failed the OOS production gate; keep crypto research-only.
- Broker symbols in `broker_symbol_map.json` are templates until exported from the actual platform.
- MT5 Python automation usually needs Windows/VPS. On macOS, use dry-run/manual execution until infrastructure is confirmed.
