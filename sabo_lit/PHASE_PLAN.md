# PHASE PLAN — sabo_lit

This file ordains the sequence of phases. The order is binding: a phase
begins only after its predecessor's deliverables are reviewed and merged.
Cross-phase "while we're at it" changes are forbidden — they become a
contract phase of their own.

Phases 0, 0.1 and 0.2 are **contract phases**: interfaces, schemas,
governance rules and documentation only. No business logic, no production
implementation.

---

## Phase 0 — Architecture and contracts ✅
- Directory structure for the production zone and the research zone.
- Abstract interfaces (`core/interfaces.py`).
- Pydantic schemas (`core/schemas.py`).
- Event bus interface (`core/events.py`).
- `README.md`, `CONVENTIONS.md`, `requirements.txt`, `docker-compose.yml`,
  one YAML config per environment.

## Phase 0.1 — Contract corrections, iteration 1 ✅
Subsequently amended by Phase 0.2 (the HMAC mechanism it introduced was
withdrawn).
- Tightened gate, manifest provenance, strategy DI, position lifecycle,
  typed errors, sync/async coherence.

## Phase 0.2 — Contract corrections, iteration 2 ✅
- Removed HMAC-based provenance. Defense in depth is now **typing +
  scanner**; runtime crypto was a hazard on cross-platform deployment.
- Closed the PnL feedback loop through the event bus
  (`TradeClosedEvent` on `TRADE_CLOSED_TOPIC`) instead of a documented
  direct call.
- Added identifier conventions and a canonical import form to
  `CONVENTIONS.md`.
- Re-exported the public surface from `sabo_lit.core` so sub-agents have
  one import form.

---

## Phase 1 — START HERE

### Phase 1, deliverable 1 (FIRST): `DependencyPolicy.scan` + CI wiring

**This is the absolute first work item of Phase 1.** No other module work
begins until it is merged.

**Why first.** `governance/dependency_rules.py` declares
`CONSTRUCTION_RULES` and `FORBIDDEN_IMPORTS`. Until
`DependencyPolicy.scan` implements them and CI runs the scan on every PR,
the rules are inert — and Phase 1 is precisely when multiple sub-agents
work in parallel, with maximum risk of violation. Implementing the
scanner first means every subsequent PR is checked against the contract,
including the very first LIT module.

**Deliverables.**

1. `sabo_lit.governance` concrete implementation of `DependencyPolicy`:
   - `allowed_imports()` derived from `FORBIDDEN_IMPORTS` (inversion).
   - `scan(root_path)` that walks every `.py` file under `root_path`,
     parses each AST, and reports violations of:
     - `FORBIDDEN_IMPORTS` (any `import M` or `from M import …` whose
       importer is forbidden — including aliased imports);
     - `CONSTRUCTION_RULES` (any call site `Cls(...)` or
       `Cls.model_construct(...)` of a restricted symbol outside its
       allowed constructor modules — AST-based, so
       `from m import Cls as C; C(...)` is correctly classified).
2. `tests/governance/test_dependency_scan.py`:
   - Asserts the scan flags a deliberately-forbidden import in a fixture
     file.
   - Asserts the scan flags a deliberately-forbidden construction in a
     fixture file (`ValidatedStructureState(...)` outside `lit/`).
   - Asserts the scan returns an empty list on the current production
     tree.
3. CI hook (GitHub Actions or equivalent) that runs the scan on every
   pull request and fails the build on a non-empty violation list.
4. Smoke test that imports every `sabo_lit.*` module and asserts no
   collateral import error after the Phase 0.2 re-export changes.

Only after these four items are merged does the rest of Phase 1 begin.

### Phase 1, deliverable 2 onward: LIT primitives
- `lit.LiquidityMapper` — implements `LiquidityMapper`.
- `lit.InducementPatternDetector` — implements `InducementDetector`;
  sole producer of `InducementEvent(confirmed=True)`.
- `lit.PhaseClassifier` — implements `PhaseClassifier`.
- `lit.RuleBasedStructureValidator` — implements `StructureValidator`;
  sole producer of `ValidatedStructureState`.

### Phase 1.4 — `RuleBasedSweepDetector` (added by contract phase 0.4)
- `lit.RuleBasedSweepDetector` — implements `SweepDetector`; sole
  producer of `SweepEvent`. Required for `PhaseClassifier` to ever
  reach `PHASE_2_MITIGATION` and for the `sweep_event_id` trace on
  `ValidatedStructureState` to be populated. Phase 1.4 lives between
  Phase 1 (LIT primitives) and Phase 2 (regime engine).
- Activates dormant `CONSTRUCTION_RULES` entry for `SweepEvent`
  (uncomment in `governance/dependency_rules.py`). After activation,
  the `_sweep` fixture in `tests/lit/test_phase_classifier.py` must
  be re-routed through `RuleBasedSweepDetector` and `_synth_sweep` in
  `sandbox/eval_lit_pipeline.py` must be deleted in favour of a real
  detector call.

---

## Phase 2 — Regime engine + secondary filters

## Phase 3 — Sub-strategies + selector + adaptive confidence engine

## Phase 4 — Features pipeline + signal model + probabilistic arbiter

## Phase 5 — Risk manager + paper broker + position manager

Includes:
- `RiskManager` subscribing to `TRADE_CLOSED_TOPIC` during bootstrap.
- Paper broker publishing `TradeClosedEvent` on `TRADE_CLOSED_TOPIC` in
  its `close()` method.
- End-to-end test of the PnL feedback loop on the in-memory bus.

## Phase 6 — Backtest engine + validation suites

## Phase 7 — API / dashboard + persistence + experiment tracking + live broker

## Phase 8 — Live monitoring and operations

---

## Invariants across all phases

- A sub-agent works on one folder. It does not modify `core/`,
  `governance/`, or another module's source.
- Any change to `core/` or `governance/` goes through a contract phase
  (0.N), reviewed end-to-end.
- The phase order is binding. A "while we're at it" cross-phase change
  is a contract change and pauses the current phase.
- Every PR runs `DependencyPolicy.scan`. Non-empty violation list ==
  build failure.
- Every public surface is documented in `CONVENTIONS.md`. Adding a
  schema, an interface or a topic without updating CONVENTIONS is a
  contract violation.
