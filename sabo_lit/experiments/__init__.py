"""
experiments/ — training runs, candidate models and per-run metadata.

NOT part of the governed production zone. ``experiments/candidates/`` holds
candidate model artifacts produced by training runs; they are referenced by
a ``ComponentManifest`` and reach production only via a ``PromotionRequest``
validated by ``governance/``.

Production code MUST NOT load artifacts directly from ``candidates/``: it
loads only from the path returned by ``model_registry`` for a registered
component.
"""
