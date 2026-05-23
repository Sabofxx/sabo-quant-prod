"""
Strategy selector — implements ``StrategySelector``.

Selects the most appropriate ``SubStrategy`` for the current ``MarketRegime``,
informed by per-strategy ``ConfidenceState``. The selector may be:

* a rule-based table keyed by composite regime;
* a meta-model (gradient boosting) trained on past performance per regime;
* a small RL policy.

The choice is config-driven (``meta_strategy.selector``). The selector does
not bypass the gate, does not produce signals, and only chooses among
already-instantiated sub-strategies.

Phase 0: placeholder. Implementation deferred to Phase 3.
"""
