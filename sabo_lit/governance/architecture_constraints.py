"""
Architecture constraints — declarative invariants the codebase must satisfy.

Examples of constraints that will be encoded here:

* Every production module imports cross-module types only from
  ``sabo_lit.core``.
* No production module imports from ``research``, ``sandbox`` or
  ``notebooks`` (cross-checked with ``dependency_rules``).
* Every concrete ``SignalModel`` exposes a ``manifest`` whose
  ``component_type`` is ``ComponentType.MODEL``.
* No public function on a production module accepts an untyped ``dict``
  on its boundary.

Constraints are pure functions producing ``DependencyViolation`` or a
similar typed report. They do not mutate the system.

Phase 0: placeholder. Implementation deferred to Phase 7.
"""
