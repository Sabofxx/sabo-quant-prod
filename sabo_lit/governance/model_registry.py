"""
Model registry — implements ``ComponentRegistry`` for ``SignalModel``.

Refuses any model without a complete ``ComponentManifest`` AND a complete
``ValidationReport``. Refuses any model whose validation thresholds
(declared in ``config.validation``) are not met.

``register`` is for promoted artifacts only. Candidate artifacts live under
``experiments/candidates/`` and are not loaded by production strategies.

``promote`` is the only transition from research/sandbox to production;
its ``PromotionRequest`` parameter is type-checked.

Phase 0: placeholder. Implementation deferred to Phase 7.
"""
