"""
backtest/ — historical replay engine.

Implementations of ``BacktestEngine``. Consumes a ``Strategy`` plus an
``Iterable[Candle]`` and produces a ``ValidationReport`` whose schema is
suitable for inclusion in a ``PromotionRequest``.

A backtest result is NOT a manifest. Promotion still requires the full
suite in ``validation/``.
"""
