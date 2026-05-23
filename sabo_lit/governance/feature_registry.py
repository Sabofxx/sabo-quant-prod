"""
Feature registry — implements ``ComponentRegistry`` for features.

Each registered feature carries a ``ComponentManifest`` with measured
statistical gain, OOS performance, compute cost and regime stability.

The feature schema (names + dtypes) consumed by a model is checked against
the registry: a model declaring features absent from the registry cannot
be promoted.

Phase 0: placeholder. Implementation deferred to Phase 7.
"""
