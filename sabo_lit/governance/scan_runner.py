"""
Single source of truth for the dependency-scan CI verdict.

Both the pytest suite (``tests/governance/test_dependency_scan.py``) and
the GitHub Actions workflow (``.github/workflows/ci.yml``) call into
THIS module. The YAML contains no inline Python that decides the build
status — that logic lives here and is exercised by tests.

Public surface
--------------

* ``run_scan(root_path)`` — pure: scans, returns a typed violation list,
  no I/O beyond reading source files, no exit, no print. Used both by
  ``test_production_tree_is_clean`` (which wants the typed list for
  diagnostics) and internally by ``main`` (which converts it to an exit
  code).

* ``format_violations(violations)`` — human-readable rendering for CI
  logs; pure string transformation.

* ``main(argv)`` — CLI entry point. Returns an int exit code:
    0 — clean tree (no violations);
    1 — at least one violation;
    2 — usage error (missing or non-directory ``root``).

  Invoked as ``python -m sabo_lit.governance.scan_runner <root>``.

Tests assert on both ``run_scan`` (semantics) and ``main`` (exit codes),
so the CI verdict path is fully covered before any branch goes red.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from sabo_lit.core import DependencyViolation
from sabo_lit.governance.dependency_policy import RuleBasedDependencyPolicy


def run_scan(root_path: str) -> list[DependencyViolation]:
    """Execute the scan once on ``root_path`` and return the typed
    violation list. No side effects beyond reading source files.

    This function is THE single scan call shared by the pytest suite and
    by ``main``. Reimplementing the scan call elsewhere defeats the
    single-source-of-truth guarantee.
    """
    return RuleBasedDependencyPolicy().scan(root_path)


def format_violations(violations: list[DependencyViolation]) -> str:
    """Human-readable rendering of a violation list for CI logs.

    Empty list -> empty string. Otherwise one header line plus two lines
    per violation (location + rule). The caller is responsible for
    routing to stderr / stdout as appropriate.
    """
    if not violations:
        return ""
    lines = [f"DependencyPolicy: {len(violations)} violation(s) found"]
    for v in violations:
        lines.append(f"  {v.file_path}:{v.line_number}")
        lines.append(f"    {v.rule_violated}")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sabo_lit.governance.scan_runner",
        description=(
            "Scan a source tree for DependencyPolicy violations. "
            "Exit 0 if clean, 1 if any violation, 2 on usage error."
        ),
    )
    parser.add_argument(
        "root",
        type=str,
        help=(
            "Path to the directory containing the sabo_lit/ package "
            "(i.e. the directory that should be on sys.path)."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code.

    Exit codes:
        0 — clean tree (no violations).
        1 — at least one violation; the formatted list is on stderr.
        2 — usage error: ``root`` is missing or is not a directory.

    Tests call ``main([...])`` with an explicit argv to assert on the
    return code. The ``__main__`` guard at the bottom of the file
    forwards to ``sys.exit(main())`` so ``python -m`` invocations behave
    as a normal CLI.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(
            f"sabo_lit.governance.scan_runner: error: "
            f"{args.root!r} is not a directory",
            file=sys.stderr,
        )
        return 2

    violations = run_scan(str(root))
    if violations:
        print(format_violations(violations), file=sys.stderr)
        return 1

    print(f"DependencyPolicy: clean (0 violations) in {root}")
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised via `python -m`
    sys.exit(main())
