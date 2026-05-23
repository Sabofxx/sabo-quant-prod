"""
sandbox/ — disposable experiments and candidate model training.

Same rules as ``research/``:

* Production modules MUST NOT import from ``sandbox``.
* ``sandbox`` MAY import from production modules.
* Promotion to production only via ``ComponentRegistry.promote`` with a
  ``PromotionRequest``.
"""
