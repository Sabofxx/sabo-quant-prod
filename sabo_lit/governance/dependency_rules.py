"""
Dependency rules — declarative import / construction policy.

This file contains NO trading logic. It declares rules; the
``DependencyPolicy`` scanner (FIRST deliverable of Phase 1 — see
``PHASE_PLAN.md``) consumes them and produces ``DependencyViolation``
entries that fail CI. Governance blocks, it does not merely report.

Two rule kinds are declared, each as a frozen Pydantic schema:

1. ``ForbiddenImportRule``  — an importer module that must not import a
   given target. Used to enforce the one-way research -> production
   boundary.

2. ``ConstructionRule``     — a symbol that may only be CONSTRUCTED inside
   listed modules. Type annotations and ``from ... import`` lines remain
   legal everywhere; only call-site instantiation (``X(...)`` and
   ``X.model_construct(...)``) is restricted. This is how the LIT gate,
   the risk-approval token, and the validation reports are made
   structurally unforgeable inside the monorepo.

DEFENSE IN DEPTH

These two layers (typing + static restriction) compose to defense in
depth: accidental misuse is prevented by the type system before runtime
(Layer 1); deliberate misuse is caught by the scanner in CI before merge
(Layer 2).

A previously considered runtime HMAC layer was REMOVED in Phase 0.2.
``model_dump_json`` is not deterministic across Pydantic versions and
Python platforms (M1 vs Linux VPS), which could make a runtime token
fail on a legitimate object after a routine upgrade. The threat the
HMAC layer addressed (a hostile module within the monorepo) is
sufficiently covered by Layer 2.

Editing this file is editing the architecture. Adding rules tightens the
contract; removing rules loosens it. Both warrant review.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


# ============================================================================
# Rule schemas. Pydantic gives strict typing of the declared rules; the
# scanner reads these tuples and produces ``DependencyViolation`` instances.
# ============================================================================
class ForbiddenImportRule(BaseModel):
    """An ``importer_module`` must NOT import ``imported_module``.

    Used for the research -> production firewall. The rule is symmetric
    to ``DependencyPolicy.allowed_imports`` but expressed as an explicit
    "must not" so the intent is unambiguous in source.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    importer_module: str  # e.g. "sabo_lit.lit"
    imported_module: str  # e.g. "sabo_lit.research"
    rationale: str


class ConstructionRule(BaseModel):
    """A symbol may only be CONSTRUCTED inside
    ``allowed_constructor_modules``.

    Construction means call-site instantiation: ``Cls(...)`` or
    ``Cls.model_construct(...)``. Imports and type annotations remain
    permitted from anywhere — only the call site is restricted.

    The scanner reports a ``DependencyViolation`` for every call site
    found outside the allowed set, including aliased imports
    (``from m import Cls as C; C(...)``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fully_qualified_symbol: str  # e.g. "sabo_lit.core.schemas.ValidatedStructureState"
    allowed_constructor_modules: frozenset[str]
    rationale: str


# ============================================================================
# Declared rules.
# ============================================================================

# Every production module. The research-zone firewall iterates over this
# set rather than re-listing it per rule.
_PRODUCTION_MODULES: frozenset[str] = frozenset(
    {
        "sabo_lit.core",
        "sabo_lit.data",
        "sabo_lit.microstructure",
        "sabo_lit.lit",
        "sabo_lit.filters",
        "sabo_lit.regime",
        "sabo_lit.meta_strategy",
        "sabo_lit.adaptive",
        "sabo_lit.features",
        "sabo_lit.models",
        "sabo_lit.strategy",
        "sabo_lit.risk",
        "sabo_lit.execution",
        "sabo_lit.backtest",
        "sabo_lit.validation",
        "sabo_lit.governance",
        "sabo_lit.api",
        "sabo_lit.persistence",
    }
)

_RESEARCH_ZONE_MODULES: frozenset[str] = frozenset(
    {
        "sabo_lit.research",
        "sabo_lit.sandbox",
        "sabo_lit.notebooks",
        "sabo_lit.experiments",
    }
)


# Research -> production firewall: NO production module may import from any
# research-zone module. The opposite direction is permitted.
FORBIDDEN_IMPORTS: tuple[ForbiddenImportRule, ...] = tuple(
    ForbiddenImportRule(
        importer_module=prod,
        imported_module=research,
        rationale=(
            "Production code must not depend on the ungoverned research "
            "zone. The only path from research/sandbox/notebooks to "
            "production is a PromotionRequest validated by governance/."
        ),
    )
    for prod in sorted(_PRODUCTION_MODULES)
    for research in sorted(_RESEARCH_ZONE_MODULES)
)


# Construction restrictions — the static layer of the defense-in-depth
# strategy. Each rule pairs a Pydantic-typed invariant (in core/schemas.py)
# with a call-site restriction. The scanner makes the call-site restriction
# effective; until DependencyPolicy.scan is implemented and wired to CI
# (first deliverable of Phase 1), these rules are inert.
CONSTRUCTION_RULES: tuple[ConstructionRule, ...] = (
    # ----- LIT gate ---------------------------------------------------------
    ConstructionRule(
        fully_qualified_symbol="sabo_lit.core.schemas.ValidatedStructureState",
        allowed_constructor_modules=frozenset({"sabo_lit.lit"}),
        rationale=(
            "The LIT gate is sovereign. Only StructureValidator "
            "implementations in lit/ may construct ValidatedStructureState. "
            "Accidental misuse is caught by the type system (the class is a "
            "distinct subclass that every downstream contract requires by "
            "type); deliberate forgery inside the monorepo is caught here "
            "by the scanner before merge."
        ),
    ),
    ConstructionRule(
        fully_qualified_symbol="sabo_lit.core.schemas.InducementEvent",
        allowed_constructor_modules=frozenset({"sabo_lit.lit"}),
        rationale=(
            "InducementEvent.confirmed is Literal[True]. Only "
            "InducementDetector implementations in lit/ may construct "
            "confirmed inducement events — they are the sole source of "
            "the gate marker."
        ),
    ),
    ConstructionRule(
        fully_qualified_symbol="sabo_lit.core.schemas.SweepEvent",
        allowed_constructor_modules=frozenset({"sabo_lit.lit"}),
        rationale=(
            "SweepEvent feeds PhaseClassifier and the sweep_event_id "
            "trace on ValidatedStructureState. Only SweepDetector "
            "implementations in lit/ may construct it — symmetric with "
            "InducementEvent so the entire LIT event surface is "
            "construction-restricted to one module."
        ),
    ),
    # ----- Risk approval ----------------------------------------------------
    ConstructionRule(
        fully_qualified_symbol="sabo_lit.core.schemas.ApprovedRiskDecision",
        allowed_constructor_modules=frozenset({"sabo_lit.risk"}),
        rationale=(
            "Only RiskManager implementations in risk/ may produce "
            "risk-approved decisions. ExecutionBroker.execute requires this "
            "exact type, so restricting construction here closes the loop "
            "on risk bypass."
        ),
    ),
    # ----- Validation provenance -------------------------------------------
    ConstructionRule(
        fully_qualified_symbol="sabo_lit.core.schemas.OutOfSamplePerformance",
        allowed_constructor_modules=frozenset(
            {"sabo_lit.backtest", "sabo_lit.validation"}
        ),
        rationale=(
            "Performance metrics must originate from a real backtest or "
            "validation run — never hand-written into a manifest. "
            "Restricting construction here is the enforcement mechanism: "
            "CI flags every Cls(...) or Cls.model_construct(...) call site "
            "outside the allowed modules."
        ),
    ),
    ConstructionRule(
        fully_qualified_symbol="sabo_lit.core.schemas.ValidationReport",
        allowed_constructor_modules=frozenset(
            {"sabo_lit.backtest", "sabo_lit.validation"}
        ),
        rationale=(
            "Validation reports are produced by BacktestEngine or "
            "ValidationSuite. The construction rule blocks the "
            "hand-written report path: CI fails on any call site outside "
            "backtest/ or validation/."
        ),
    ),
    ConstructionRule(
        fully_qualified_symbol="sabo_lit.core.schemas.WalkForwardResult",
        allowed_constructor_modules=frozenset({"sabo_lit.validation"}),
        rationale="Produced only by the walk-forward suite in validation/.",
    ),
    ConstructionRule(
        fully_qualified_symbol="sabo_lit.core.schemas.MonteCarloResult",
        allowed_constructor_modules=frozenset({"sabo_lit.validation"}),
        rationale="Produced only by the Monte Carlo suite in validation/.",
    ),
    ConstructionRule(
        fully_qualified_symbol="sabo_lit.core.schemas.RegimeStabilityReport",
        allowed_constructor_modules=frozenset({"sabo_lit.validation"}),
        rationale=(
            "Produced only by the regime stability suite in validation/. "
            "Restricting construction here closes the only side door for "
            "fabricated sub-reports embedded in ValidationReport."
        ),
    ),
    ConstructionRule(
        fully_qualified_symbol="sabo_lit.core.schemas.OverfittingReport",
        allowed_constructor_modules=frozenset({"sabo_lit.validation"}),
        rationale=(
            "Produced only by the overfitting detection suite in "
            "validation/. Same side-door argument as RegimeStabilityReport."
        ),
    ),
)
