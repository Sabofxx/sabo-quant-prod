"""
governance/ — Complexity Control Layer.

Declarative layer: rules, registries and verifications. Contains NO trading
logic. Stays deliberately small.

Hosts implementations of:

* ``ComponentRegistry``    — model_registry, feature_registry, strategy_registry.
* ``DependencyPolicy``     — declares and verifies inter-module imports
                             AND privileged construction sites; a non-empty
                             scan fails CI.
* ``ComplexityMonitor``    — exposes counts and depth metrics.

The registries refuse any component whose ``ComponentManifest`` or
``ValidationReport`` is incomplete. The only path from research/sandbox
into production is ``ComponentRegistry.promote`` with a ``PromotionRequest``.
"""
from sabo_lit.governance.dependency_policy import RuleBasedDependencyPolicy

__all__ = ["RuleBasedDependencyPolicy"]
