"""
Experiment tracking — index of runs in ``experiments/``.

Records the metadata of each training / backtest run: configuration hash,
git commit, dataset window, seed, artifact path, ``ValidationReport``.

Tracking is the bookkeeping side of governance; promotion still goes
through ``ComponentRegistry.promote`` with a typed ``PromotionRequest``.

Phase 0: placeholder. Implementation deferred to Phase 7.
"""
