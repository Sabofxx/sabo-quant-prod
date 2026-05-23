"""
research/ — exploratory research zone. NOT governed.

A low-friction zone for prototyping features, exploring data, drafting
strategies. No ``ComponentManifest`` is required here.

Rules:

* Production modules MUST NOT import from ``research``. The CI
  ``DependencyPolicy`` scan enforces this.
* ``research`` MAY import from production modules.
* The only way for code or an artifact from this zone to reach production
  is a ``PromotionRequest`` validated by ``governance/``.
"""
