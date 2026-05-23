"""Tests for ``RuleBasedDependencyPolicy.scan``.

The fixture-based tests write deliberately-violating source files into
``tmp_path`` and run the scanner against that throw-away root. The final
test runs the scanner against the actual production tree and asserts a
clean result — if it ever fails, the failure is reported (NOT patched into
core/ by sub-agents).
"""
from __future__ import annotations

import pathlib
import textwrap

import pytest

from sabo_lit.governance.dependency_policy import RuleBasedDependencyPolicy
from sabo_lit.governance.scan_runner import main, run_scan


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
@pytest.fixture
def policy() -> RuleBasedDependencyPolicy:
    """A policy bound to the real production rule set."""
    return RuleBasedDependencyPolicy()


def _write(path: pathlib.Path, content: str) -> None:
    """Create parent dirs and write ``content`` (dedented) to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")


def _make_fixture_package(root: pathlib.Path, module_path: str, body: str) -> None:
    """Write a ``.py`` file at ``root / module_path`` and the chain of
    ``__init__.py`` files needed to make it a real package tree.

    ``module_path`` is a forward-slash relative path, e.g.
    ``"sabo_lit/strategy/bad.py"``.
    """
    file_path = root / module_path
    _write(file_path, body)
    # Walk back up the dirs, creating empty __init__.py at each package level
    current = file_path.parent
    stop = root
    while current != stop and current != current.parent:
        init = current / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")
        current = current.parent


# ----------------------------------------------------------------------------
# Tests — ForbiddenImportRule
# ----------------------------------------------------------------------------
def test_forbidden_import_from_research_is_flagged(
    tmp_path: pathlib.Path, policy: RuleBasedDependencyPolicy
) -> None:
    """A production module importing from research/ must be flagged."""
    _make_fixture_package(
        tmp_path,
        "sabo_lit/lit/bad.py",
        """
        from sabo_lit.research import something
        """,
    )
    violations = policy.scan(str(tmp_path))
    assert any(
        "ForbiddenImportRule" in v.rule_violated
        and v.importer_module == "sabo_lit.lit.bad"
        and v.imported_module == "sabo_lit.research"
        for v in violations
    ), f"Expected ForbiddenImportRule violation; got: {[v.rule_violated for v in violations]}"


def test_aliased_forbidden_import_is_flagged(
    tmp_path: pathlib.Path, policy: RuleBasedDependencyPolicy
) -> None:
    """``import sabo_lit.sandbox as sbx`` in production code must be flagged."""
    _make_fixture_package(
        tmp_path,
        "sabo_lit/risk/bad.py",
        """
        import sabo_lit.sandbox as sbx  # noqa: F401
        """,
    )
    violations = policy.scan(str(tmp_path))
    assert any(
        "ForbiddenImportRule" in v.rule_violated
        and v.importer_module == "sabo_lit.risk.bad"
        and v.imported_module == "sabo_lit.sandbox"
        for v in violations
    ), f"Expected aliased ForbiddenImportRule violation; got: {violations}"


def test_forbidden_import_from_sandbox_subpackage_is_flagged(
    tmp_path: pathlib.Path, policy: RuleBasedDependencyPolicy
) -> None:
    """``from sabo_lit.sandbox.something import X`` must be flagged."""
    _make_fixture_package(
        tmp_path,
        "sabo_lit/strategy/bad.py",
        """
        from sabo_lit.sandbox.experiments import helper  # noqa: F401
        """,
    )
    violations = policy.scan(str(tmp_path))
    assert any(
        "ForbiddenImportRule" in v.rule_violated
        and v.importer_module == "sabo_lit.strategy.bad"
        and v.imported_module == "sabo_lit.sandbox.experiments"
        for v in violations
    ), f"Expected subpackage ForbiddenImportRule violation; got: {violations}"


# ----------------------------------------------------------------------------
# Tests — ConstructionRule (direct + model_construct + aliased + annotation)
# ----------------------------------------------------------------------------
def test_direct_construction_outside_lit_is_flagged(
    tmp_path: pathlib.Path, policy: RuleBasedDependencyPolicy
) -> None:
    """``ValidatedStructureState(...)`` outside ``sabo_lit.lit`` must be flagged."""
    _make_fixture_package(
        tmp_path,
        "sabo_lit/strategy/bad.py",
        """
        from sabo_lit.core.schemas import ValidatedStructureState

        def cheat():
            return ValidatedStructureState()
        """,
    )
    violations = policy.scan(str(tmp_path))
    assert any(
        "ConstructionRule" in v.rule_violated
        and v.importer_module == "sabo_lit.strategy.bad"
        and "ValidatedStructureState" in v.imported_module
        for v in violations
    ), f"Expected ConstructionRule violation; got: {violations}"


def test_model_construct_escape_hatch_is_flagged(
    tmp_path: pathlib.Path, policy: RuleBasedDependencyPolicy
) -> None:
    """``Cls.model_construct(...)`` must be flagged like ``Cls(...)``."""
    _make_fixture_package(
        tmp_path,
        "sabo_lit/execution/bad.py",
        """
        from sabo_lit.core.schemas import ApprovedRiskDecision

        def cheat():
            return ApprovedRiskDecision.model_construct()
        """,
    )
    violations = policy.scan(str(tmp_path))
    assert any(
        "ConstructionRule" in v.rule_violated
        and v.importer_module == "sabo_lit.execution.bad"
        and "ApprovedRiskDecision" in v.imported_module
        for v in violations
    ), f"Expected ConstructionRule violation for model_construct; got: {violations}"


def test_aliased_construction_is_flagged(
    tmp_path: pathlib.Path, policy: RuleBasedDependencyPolicy
) -> None:
    """``from m import Cls as C; C(...)`` must be flagged."""
    _make_fixture_package(
        tmp_path,
        "sabo_lit/models/bad.py",
        """
        from sabo_lit.core.schemas import ValidatedStructureState as VS

        def cheat():
            return VS()
        """,
    )
    violations = policy.scan(str(tmp_path))
    assert any(
        "ConstructionRule" in v.rule_violated
        and v.importer_module == "sabo_lit.models.bad"
        and "ValidatedStructureState" in v.imported_module
        for v in violations
    ), f"Expected aliased ConstructionRule violation; got: {violations}"


def test_canonical_import_construction_is_flagged_via_reexport(
    tmp_path: pathlib.Path, policy: RuleBasedDependencyPolicy
) -> None:
    """Using the canonical import (``from sabo_lit.core import X``) must be
    flagged just like the schemas-direct path, via the re-export map built
    from the fixture's own ``core/__init__.py``.
    """
    # Synthetic re-export at sabo_lit/core/__init__.py
    _make_fixture_package(
        tmp_path,
        "sabo_lit/core/__init__.py",
        """
        from sabo_lit.core.schemas import ValidatedStructureState  # noqa: F401
        """,
    )
    # Synthetic schemas module so the re-export source exists for parsing
    _write(
        tmp_path / "sabo_lit" / "core" / "schemas.py",
        """
        class ValidatedStructureState:
            pass
        """,
    )
    # Production-side bad caller using the canonical import
    _make_fixture_package(
        tmp_path,
        "sabo_lit/strategy/bad.py",
        """
        from sabo_lit.core import ValidatedStructureState

        def cheat():
            return ValidatedStructureState()
        """,
    )
    violations = policy.scan(str(tmp_path))
    assert any(
        "ConstructionRule" in v.rule_violated
        and v.importer_module == "sabo_lit.strategy.bad"
        and "ValidatedStructureState" in v.imported_module
        for v in violations
    ), f"Expected ConstructionRule violation through re-export; got: {violations}"


def test_annotation_use_is_not_flagged(
    tmp_path: pathlib.Path, policy: RuleBasedDependencyPolicy
) -> None:
    """A type annotation using a restricted symbol must NOT be flagged."""
    _make_fixture_package(
        tmp_path,
        "sabo_lit/strategy/ok.py",
        """
        from __future__ import annotations
        from sabo_lit.core.schemas import ValidatedStructureState

        def consume(state: ValidatedStructureState) -> None:
            pass

        x: ValidatedStructureState | None = None
        y: list[ValidatedStructureState] = []
        """,
    )
    violations = policy.scan(str(tmp_path))
    construction_violations = [
        v
        for v in violations
        if "ConstructionRule" in v.rule_violated
        and "ValidatedStructureState" in v.imported_module
    ]
    assert construction_violations == [], (
        "Annotations must not trigger ConstructionRule, got: "
        f"{construction_violations}"
    )


def test_isinstance_check_is_not_flagged(
    tmp_path: pathlib.Path, policy: RuleBasedDependencyPolicy
) -> None:
    """``isinstance(x, Cls)`` is a Call to ``isinstance``, with ``Cls`` as a
    Name argument — not a Call on ``Cls`` itself. Must not be flagged.
    """
    _make_fixture_package(
        tmp_path,
        "sabo_lit/strategy/ok2.py",
        """
        from sabo_lit.core.schemas import ValidatedStructureState

        def check(x):
            return isinstance(x, ValidatedStructureState)
        """,
    )
    violations = policy.scan(str(tmp_path))
    construction_violations = [
        v
        for v in violations
        if "ConstructionRule" in v.rule_violated
        and "ValidatedStructureState" in v.imported_module
    ]
    assert construction_violations == [], (
        f"isinstance must not trigger ConstructionRule, got: {construction_violations}"
    )


def test_string_literal_mentioning_symbol_is_not_flagged(
    tmp_path: pathlib.Path, policy: RuleBasedDependencyPolicy
) -> None:
    """A docstring or string literal containing the symbol name is not a
    call site. AST-based scanning must ignore it.
    """
    _make_fixture_package(
        tmp_path,
        "sabo_lit/strategy/ok3.py",
        '''
        """This module talks about ValidatedStructureState but never calls it."""

        DESCRIPTION = "ValidatedStructureState(...) is forbidden here"

        def explain():
            return "ValidatedStructureState.model_construct() too"
        ''',
    )
    violations = policy.scan(str(tmp_path))
    assert violations == [], (
        f"String literals must not trigger any rule, got: {violations}"
    )


def test_construction_inside_lit_is_not_flagged(
    tmp_path: pathlib.Path, policy: RuleBasedDependencyPolicy
) -> None:
    """The same construction inside ``sabo_lit.lit`` must NOT be flagged —
    that is the producer module, after all.
    """
    _make_fixture_package(
        tmp_path,
        "sabo_lit/lit/validator.py",
        """
        from sabo_lit.core.schemas import ValidatedStructureState

        def make():
            return ValidatedStructureState()
        """,
    )
    violations = policy.scan(str(tmp_path))
    construction_violations = [
        v
        for v in violations
        if "ConstructionRule" in v.rule_violated
        and "ValidatedStructureState" in v.imported_module
    ]
    assert construction_violations == [], (
        "Constructions inside the allowed module must not be flagged, got: "
        f"{construction_violations}"
    )


# ----------------------------------------------------------------------------
# Tests — Clean production tree
# ----------------------------------------------------------------------------
def test_production_tree_is_clean() -> None:
    """Run ``run_scan`` (the SAME function ``main`` calls) over the real
    source tree and assert no violations.

    Single source of truth: ``run_scan`` is what both this test AND the
    CI entry point ``main`` invoke. Reimplementing the scan call anywhere
    else would split the verdict path. Per the Phase 1 spec, any
    violation found here is reported, NOT patched away by editing
    ``core/`` or production code.
    """
    # The conftest puts the directory CONTAINING ``sabo_lit/`` on sys.path.
    # That same directory is the scan root: files there become modules
    # named ``sabo_lit.<...>``, which is what the rules expect.
    scan_root = pathlib.Path(__file__).resolve().parents[3]
    violations = run_scan(str(scan_root))

    # Only report violations that are inside the sabo_lit/ tree — if any
    # unrelated .py files happen to be present beside sabo_lit/, they are
    # irrelevant to this assertion.
    sabo_lit_dir = scan_root / "sabo_lit"
    in_tree = [
        v
        for v in violations
        if pathlib.Path(v.file_path).resolve().is_relative_to(sabo_lit_dir)
    ]
    if in_tree:
        formatted = "\n".join(
            f"  {v.file_path}:{v.line_number}\n    {v.rule_violated}"
            for v in in_tree
        )
        pytest.fail(
            "Unexpected violations on the production tree:\n" + formatted
        )


# ----------------------------------------------------------------------------
# Tests — CI entry point (Point B: prove the red actually arrives).
#
# ``run_scan`` returning a non-empty list is already covered. These two
# tests close the missing link: invoking the CI entry point itself
# (``main``) must turn that non-empty list into a non-zero exit code, and
# a clean tree must produce exit 0. CI calls EXACTLY this function via
# `python -m sabo_lit.governance.scan_runner`, so a green here is a
# green there.
# ----------------------------------------------------------------------------
def test_main_exits_one_on_violations(tmp_path: pathlib.Path) -> None:
    """CI entry point must return exit code 1 when the tree contains
    at least one violation. This is the explicit proof that the build
    turns red — not a derivation from `scan` returning a list.
    """
    _make_fixture_package(
        tmp_path,
        "sabo_lit/lit/bad.py",
        """
        from sabo_lit.research import something  # noqa: F401
        """,
    )
    exit_code = main([str(tmp_path)])
    assert exit_code == 1, (
        f"Expected exit code 1 on violations; got {exit_code}. The CI "
        "verdict would be green even though the scanner saw the violation."
    )


def test_main_exits_zero_on_clean_tree(tmp_path: pathlib.Path) -> None:
    """CI entry point must return exit code 0 on a clean tree.

    Empty fixture -> no .py files -> no violations -> exit 0. Symmetric
    to ``test_main_exits_one_on_violations`` and rules out a "false red"
    where ``main`` would refuse a legitimate tree.
    """
    exit_code = main([str(tmp_path)])
    assert exit_code == 0, (
        f"Expected exit code 0 on clean tree; got {exit_code}. The CI "
        "would be red on a tree with no violations."
    )


def test_main_exits_two_on_missing_directory(tmp_path: pathlib.Path) -> None:
    """A non-existent root path is a usage error (exit code 2), distinct
    from a violation (1) and from success (0). Keeps "CI mis-invoked" and
    "code is broken" as separately diagnosable failure modes.
    """
    exit_code = main([str(tmp_path / "does-not-exist")])
    assert exit_code == 2


def test_allowed_imports_inverts_forbidden(
    policy: RuleBasedDependencyPolicy,
) -> None:
    """``allowed_imports()`` must expose a non-empty deny-list per importer
    once ``FORBIDDEN_IMPORTS`` is populated (it is, by Phase 0.2).
    """
    table = policy.allowed_imports()
    assert table, "allowed_imports() should reflect FORBIDDEN_IMPORTS"
    # Every production module listed in FORBIDDEN_IMPORTS must have at least
    # one denylisted import.
    for importer, denylist in table.items():
        assert importer.startswith("sabo_lit.")
        assert denylist, f"empty denylist for {importer}"
