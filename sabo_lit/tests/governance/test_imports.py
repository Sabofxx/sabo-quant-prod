"""Smoke test — every ``sabo_lit.*`` module imports cleanly.

This is the first actual execution of Phase 0.2's re-exports in
``core/__init__.py``. Pydantic did not run during the contract phases;
this test catches any collateral import error before it derails
downstream work.
"""
from __future__ import annotations

import importlib
import pkgutil

import sabo_lit


def test_all_sabo_lit_modules_import_cleanly() -> None:
    failures: list[str] = []
    # walk_packages handles namespace packages: sabo_lit/ has no __init__.py
    # at its top level, but Python treats it as a namespace package, so
    # __path__ is a valid sequence we can walk.
    for module_info in pkgutil.walk_packages(
        sabo_lit.__path__, prefix="sabo_lit."
    ):
        try:
            importlib.import_module(module_info.name)
        except Exception as exc:  # noqa: BLE001 — we want every failure
            failures.append(
                f"{module_info.name}: {type(exc).__name__}: {exc}"
            )
    assert not failures, "Import failures:\n" + "\n".join(failures)


def test_canonical_core_surface_is_importable() -> None:
    """Spot-check the public surface declared in ``core/__init__.py``.

    Sub-agents work against the canonical import form
    (``from sabo_lit.core import X``). This test makes sure every
    high-traffic symbol resolves at runtime, catching typos or missing
    re-exports introduced by the Phase 0.2 reshuffle.
    """
    from sabo_lit.core import (  # noqa: F401 — import-only assertion
        TRADE_CLOSED_TOPIC,
        ApprovedRiskDecision,
        BrokerRejectionError,
        ComponentManifest,
        DataFeedInterruptionError,
        DependencyPolicy,
        DependencyViolation,
        EventBus,
        ExecutionBroker,
        ExecutionFailureError,
        InducementEvent,
        OutOfSamplePerformance,
        PositionManager,
        PromotionRequest,
        RiskManager,
        SaboLitError,
        Signal,
        Strategy,
        StrategyContext,
        StructureValidator,
        TradeClosedEvent,
        ValidatedStructureState,
        ValidationReport,
    )
