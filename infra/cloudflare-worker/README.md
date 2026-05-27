# Sabo TradingView Webhook Worker

Receives TradingView alert webhooks, validates them, deduplicates accepted signals in Cloudflare KV, and optionally appends accepted signals to:

`sabo_lit/sandbox/live/tradingview_signals.jsonl`

## Endpoints

- `POST /tradingview/webhook` accepts TradingView JSON alerts.
- `GET /signals/today?date=YYYY-MM-DD` returns accepted signals from KV.
- `GET /health` returns a simple health check.

## TradingView Alert Body

```json
{
  "secret": "YOUR_SHARED_SECRET",
  "source": "tradingview",
  "strategy": "sabo_tv_filter",
  "symbol": "{{ticker}}",
  "timeframe": "{{interval}}",
  "side": "BUY",
  "price": "{{close}}",
  "time": "{{time}}"
}
```

TradingView webhooks do not normally let alerts set arbitrary HMAC headers. This Worker supports `X-Sabo-Sig: sha256=<hex>` for future relays or manual clients, but the direct TradingView path uses the body `secret`.

## Required Secrets

Set these with `wrangler secret put NAME`:

```bash
wrangler secret put TV_SHARED_SECRET
wrangler secret put READ_TOKEN
wrangler secret put GITHUB_TOKEN
```

`GITHUB_TOKEN` should be a fine-scoped token that can update only this repository content. Do not put this token in TradingView.

## KV

Create the namespace and copy the IDs into `wrangler.toml`:

```bash
wrangler kv namespace create TV_SIGNALS_KV
wrangler kv namespace create TV_SIGNALS_KV --preview
```

Cloudflare KV deduplication is sufficient for repeated TradingView deliveries, but it is not a strict atomic `SETNX`. If exact once-only processing becomes critical, move the dedup check into a Durable Object.
