# Broker Symbol Map

This file is an execution template, not a certified contract specification.
Before live trading, open the exact challenge account platform and export/verify:

- FX symbol names and suffixes (`EURUSD`, `EURUSD.r`, `EUR/USD`, etc.)
- `trade_contract_size`, tick size, tick value, min/max lot, lot step
- swap long / swap short for every pair
- index/metals/oil CFD point value and margin mode
- server timezone and daily rollover time

## Platform Sources Checked

| firm | official platform source | status |
|---|---|---|
| FTMO | <https://ftmo.com/en/ftmo-trading-platforms/> | MT4/MT5/cTrader available; symbol suffixes not certified |
| FundingPips | <https://help.fundingpips.com/hc/en-us/articles/43468639481105> | MT5/cTrader/MatchTrader available; symbol suffixes not certified |
| FundedNext | <https://help.fundednext.com/en/articles/8224149-which-broker-does-fundednext-associate-with> | MT4/MT5/Match-Trader/cTrader via internal infrastructure; symbol suffixes not certified |
| E8 Markets | <https://help.e8markets.com/en/articles/9799834-available-trading-platforms> | TradeLocker/MatchTrader/cTrader/MT5 available depending on region/product |

## Practical Recommendation

Use `default_mt5` only for demo generation. For each real account:

1. Open Market Watch / Symbol Info.
2. Confirm each FX symbol is tradable.
3. Export symbol specs with `mt5_connector.py --dump-symbols`.
4. Copy exact broker suffixes into `broker_symbol_map.json`.
5. Run `propfirm_signal_generator.py --broker <broker_key>` and verify the CSV symbols.

## Why This Matters

The strategy currently trades FX majors only in production. FX lot sizing assumes
`100,000` base units per standard lot. That is usually true on MT5, but index,
metal, and oil CFD sizing is not standardized. Any CFD exposure must remain
disabled until the platform-specific multiplier is verified.

## Current Broker Keys

- `default_mt5`
- `ftmo_mt5`
- `fundingpips_mt5`
- `fundednext_mt5`
- `e8_mt5`

All entries are marked `"verified": false` until confirmed inside a live/demo
account. Do not remove this flag without a platform export.
