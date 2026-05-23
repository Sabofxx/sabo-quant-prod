"""
features/ — feature engineering for the AI path.

Implementations of ``FeaturePipeline``. Inputs always include a
``ValidatedStructureState``: feature construction never happens for
non-validated setups, keeping the AI path conditional on the gate.

Indicators live here as features only — never as decision-making engines.
"""
