"""
validation/ — robustness suites required before any promotion.

Each suite implements ``ValidationSuite``. The aggregate output is a
``ValidationReport`` whose fields are all mandatory:

* ``walk_forward``      — ``WalkForwardResult``
* ``monte_carlo``       — ``MonteCarloResult``
* ``regime_stability``  — ``RegimeStabilityReport``
* ``overfitting``       — ``OverfittingReport``
* ``out_of_sample_passed``

A ``PromotionRequest`` cannot be constructed without a fully populated
``ValidationReport``.
"""
