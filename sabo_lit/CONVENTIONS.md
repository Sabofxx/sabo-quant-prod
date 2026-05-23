# CONVENTIONS — sabo_lit

This file is the operating contract for everyone — human or agent — writing
code in this repository. Read it before you write a single line. The rules
below are not suggestions: they are enforced by the type system, by the
governance layer, and by CI.

---

## 1. Language and tooling

- Python **3.12+**. Features below that version are not allowed.
- `ruff` is the **only** linter and formatter. Configuration lives at the
  repository root.
- Type hints are **mandatory on every public function, method and attribute**.
  No `Any`, no untyped `dict` on a public boundary.
- All cross-module messages are **Pydantic v2 models defined in `core/schemas.py`**.
  Passing a raw `dict` between modules is a contract violation.

## 2. Scope discipline for subagents

- One subagent works on **exactly one folder**.
- A subagent **never** modifies `core/` and **never** modifies another
  module's source files.
- If a subagent believes an interface in `core/` needs to change, it
  **STOPS** and requests the change. It does not patch around it.
- Any new module-level dependency must be declared in
  `governance/dependency_rules.py` first.

## 3. Decision hierarchy — the LIT gate is sovereign

The flow of a decision is fixed:

```
data -> microstructure -> lit (GATE)
                            |
                            v
                 ValidatedStructureState (only StructureValidator can create one)
                            |
        +-------------------+-------------------+
        |                                       |
        v                                       v
     regime / filters / confidence       feature pipeline + model
        |                                       |
        +-------------------+-------------------+
                            |
                            v
                  ProbabilisticArbiter   (REJECTS or WEIGHTS — never creates)
                            |
                            v
                          Signal
                            |
                            v
                      RiskManager        (only producer of ApprovedRiskDecision)
                            |
                            v
                     ExecutionBroker     (requires ApprovedRiskDecision)
                            |
                            v
                          Position
                            |
                            v
                   PositionManager       (lifecycle: updates, partial / full exits)
                            |
                            v
                  ExecutionBroker.close
                            |
                            v
                    TradeClosedEvent     (published on TRADE_CLOSED_TOPIC)
                            |
                            v
                      RiskManager        (subscribed; updates loss limits)
```

- The **LIT gate is binary and sovereign**. No regime label, no filter, no
  AI score, no confidence weight can transform a rejected setup into an
  accepted one.
- Regime, filters, `ConfidenceEngine` and `ProbabilisticArbiter` act in
  AVAL of the gate. They adjust scores, seeds, thresholds and weights —
  never the gate verdict.
- ICT/SMC filters are secondary: they can only nudge a score.
- Technical indicators are features and scoring inputs, never the
  decision-making engine.

## 4. Device portability

- The execution device (`mps`, `cuda`, `cpu`) is **always read from config**.
  Never hard-coded.
- PyTorch models save **`state_dict` only** and reload with `map_location`.
- A model trained on CUDA must load and infer on MPS or CPU with no source
  change — verified by a portability test in `tests/`.

## 5. Configuration, secrets, paths

- **No file paths are hard-coded**. Paths come from config or are computed
  from a config-provided root.
- **No secrets in source** (API keys, broker credentials, DB passwords).
  Secrets are loaded from environment variables and referenced in YAML via
  `${VAR_NAME}`.
- One YAML config per environment (`dev`, `paper`, `live`). The active
  environment is selected by `SABO_LIT_ENV`.

## 6. Logging, errors, tests

- Logging goes through a single project logger (`sabo_lit.<module>`).
  Format is configured centrally; modules do not configure logging on import.
- Errors crossing a module boundary derive from `SaboLitError`
  (`core.interfaces`). Each typed failure category carries a typed payload
  (`BrokerRejection`, `ExecutionFailure`, `DataFeedInterruption`). No bare
  exceptions, no silent returns of `None` to signal failure.
- Tests live in `tests/`, mirroring the production tree. Every public
  interface in `core/interfaces.py` has at least one contract test (a typed
  fake plus a behavioural assertion).

## 7. Type-level invariants — non-negotiable

These invariants are encoded in `core/schemas.py`, `core/interfaces.py` and
`governance/dependency_rules.py`. You may not weaken them.

1. `InducementEvent.confirmed: Literal[True]` — only confirmed inducements
   exist as instances of the type. `InducementDetector` (concrete
   `InducementPatternDetector` in `lit/`) is the sole producer, enforced
   by a `ConstructionRule` restricting `InducementEvent` to `sabo_lit.lit`.
1b. `SweepEvent` is symmetric: `SweepDetector` (concrete
    `RuleBasedSweepDetector` in `lit/`) is the sole producer, with a
    parallel `ConstructionRule` restricting `SweepEvent` to
    `sabo_lit.lit`. Activated in Phase 1.4.
2. `ValidatedStructureState` is the only acceptable input to downstream
   components. `StructureValidator` is the only producer. A
   `ConstructionRule` restricts its instantiation to `sabo_lit.lit`.
3. `ProbabilisticArbiter.arbitrate` returns `Signal | None` from a
   `ValidatedStructureState` — it cannot manufacture a signal from an
   unvalidated setup.
4. `Signal.validated_structure: ValidatedStructureState` — every signal
   carries its gate-validated structure. Checkable at any downstream point.
5. `ExecutionBroker.execute/apply_update/close` require typed inputs
   (`ApprovedRiskDecision`, `PositionUpdate`, `ExitDecision`). Risk
   approval cannot be bypassed; position-management provenance is always
   present.
6. `RiskManager` is the only producer of `ApprovedRiskDecision`. A
   `ConstructionRule` restricts its instantiation to `sabo_lit.risk`.
7. `ComponentRegistry.register(manifest, validation)` — both are required;
   no overload accepts less.
8. `PromotionRequest` requires `ComponentManifest` and `ValidationReport`;
   it is the only path from research/sandbox to production. Both nested
   types are construction-restricted to `backtest/` or `validation/`.
9. The PnL feedback loop is closed by the event bus, not by a direct
   call. `ExecutionBroker.close` publishes `TradeClosedEvent` on
   `TRADE_CLOSED_TOPIC`; `RiskManager` subscribes during bootstrap and
   forwards events to `record_closed_trade`. See §15.
10. `Strategy.context: StrategyContext` — every strategy exposes its
    dependency container. Strategies MUST NOT instantiate pipeline
    components themselves.
11. Sync vs async split: I/O at the boundary (data, microstructure,
    broker, event bus) is async; pipeline compute is sync.
    `Strategy.on_candle` bridges them.

Defense in depth for the privileged construction sites is **typing +
scanner**: a `ConstructionRule` declared in
`governance/dependency_rules.py` is verified by `DependencyPolicy.scan`,
which fails CI on any forbidden call site. The runtime HMAC layer
considered in Phase 0.1 was retired in Phase 0.2 — see the notes in
`schemas.py` and `dependency_rules.py`.

## 8. Governance and registration

- **No new model, feature or sub-strategy enters production without a
  registered `ComponentManifest`** — even if "the backtest looks great".
  The criterion is out-of-sample gain plus inter-regime stability.
- `model_registry.register` refuses any model whose `ValidationReport` is
  missing or fails its acceptance thresholds.
- `dependency_rules` declares the allowed imports AND the privileged
  construction sites in a machine-readable form.
  `DependencyPolicy.scan` returns `list[DependencyViolation]`; a non-empty
  list **fails CI**. Governance blocks; it does not merely warn.
- The implementation of `DependencyPolicy.scan` is the **first
  deliverable of Phase 1** — until it is in place and wired to CI, every
  `ConstructionRule` and `ForbiddenImportRule` declaration is inert. See
  `PHASE_PLAN.md`.
- `ComplexityMonitor` publishes counts (active features, active models,
  active sub-strategies, dependency depth, LOC). It is a sensor, not a
  trader.

## 9. Research vs production — one-way street

- The repository has two zones:
  - **Production (governed):** `data`, `microstructure`, `lit`, `filters`,
    `regime`, `meta_strategy`, `adaptive`, `features`, `models`,
    `strategy`, `risk`, `execution`, `backtest`, `validation`,
    `governance`, `api`, `persistence`, `core`.
  - **Research (ungoverned):** `research`, `sandbox`, `notebooks`,
    `experiments`.
- **No production module may import from `research/`, `sandbox/` or
  `notebooks/`.** This is verified by `DependencyPolicy.scan` and breaks
  CI on violation.
- The opposite direction is fine: research code may import from production.
- The ONLY way for a research artifact to reach production is a
  `PromotionRequest` validated by `governance/`. Manifest and validation
  report are both mandatory; the type system refuses anything less, and
  both nested objects are construction-restricted.
- Research is intentionally low-friction. The discipline applies at the
  border, not upstream.

## 10. Live vs paper

- The execution mode is **frozen at startup from config**. `ExecutionBroker.mode`
  is a read-only property. No code path mutates it.
- A live broker connection is created only when `execution.mode == "live"`.
- The dashboard surfaces `mode` and the global stop-loss state at all times.

## 11. Model lifecycle

- **Retraining and deployment are two separate steps.** A retrained model
  is written to `experiments/candidates/` and is never loaded into a
  production strategy without an explicit, distinct action.
- A candidate model becomes a production model only via a
  `PromotionRequest` against `model_registry`.

## 12. Simplicity as a principle

- At comparable statistical edge, **always prefer the simpler variant**.
  Robust simplicity beats theoretical performance.
- Three repetitive lines are better than a premature abstraction.
- A subagent that adds a layer of indirection without a clearly measured
  gain has overspent the complexity budget.

## 13. Identifier conventions

Every cross-module schema in `core/schemas.py` has at least one `*_id`
field. To prevent each sub-agent inventing its own format, the rules below
are binding.

**Default: UUID4 string.**

Generated at construction by the producing module, using
`uuid.uuid4().hex` (32-char lowercase hex; no dashes). One UUID per
runtime object / event. Identity is the only purpose — no information is
encoded into the value.

This applies to:

- `event_id` (`TradeClosedEvent` and any future bus event); NOT
  `InducementEvent.event_id` — see exemptions
- `decision_id`, `signal_id`, `order_id`, `position_id`
- `update_id` (PositionUpdate), `exit_decision_id` cross-references
- `run_id` (OutOfSamplePerformance, ValidationReport, TradeClosedEvent)
- `request_id` (PromotionRequest)
- Any new `*_id` field introduced in future phases — unless explicitly
  exempted below.

**Cross-references** (one schema referring to another's id, e.g.
`Order.risk_decision_id` referencing the `ApprovedRiskDecision.decision_id`)
carry the SAME string the producing schema generated. The producer mints
the id; consumers carry it verbatim.

**Exemptions (NOT UUID4):**

| Field | Format | Why |
|---|---|---|
| `MarketRegime.composite_id` | Stable hash of `(symbol, volatility, liquidity, session, macro)` rendered as 16-char lowercase hex (sha256 prefix) | Callers MUST be able to recompute it from the inputs; two regimes with identical components MUST yield the same id. |
| `LiquidityZone.zone_id` | Stable hash of `(kind, level_mid quantized to equal_level_tolerance_pips, anchor_timestamp)` rendered as 16-char lowercase hex (sha256 prefix) | Sliding-window consumers must be able to deduplicate the same conceptual zone across overlapping calls without keeping a side-table; same inputs MUST yield the same id. |
| `InducementEvent.event_id` | Stable hash of `("inducement", inducement_zone.zone_id, timestamp.isoformat())` rendered as 16-char lowercase hex (sha256 prefix) | Detectors are stateless and re-emit the same event every tick the pattern holds; downstream consumers (sub-strategies, arbiter, persistence) MUST be able to dedupe by `event_id` directly, without inspecting the nested zone. |
| `SweepEvent.sweep_id` | Stable hash of `("sweep", swept_zone.zone_id, timestamp.isoformat())` rendered as 16-char lowercase hex (sha256 prefix) | Symmetric to `InducementEvent.event_id`; same dedup contract. |
| `ComponentManifest.component_id` | Domain identifier, e.g. `xgb_eurusd_m5` | Used as a stable key for the registry across runs; must survive process restarts. |
| `ComponentManifest.version` | Semantic version or commit SHA, e.g. `v3.2.1`, `abc12345` | Same reasoning — durable, not per-run. |
| `SignalModelOutput.model_id`, `model_version` | Same convention as ComponentManifest | Inference output must be traceable back to the registered model entry. |
| `DataFeedInterruption.provider_id` | Domain identifier, e.g. `mt5_eurusd` | Names a configured provider, not a one-shot event. |

When in doubt: **if the field names a single runtime occurrence, use
UUID4**; if it names a logical concept that survives restarts, use a
domain identifier.

## 14. Canonical imports

There is **one** way to import a public symbol from `core/`:

```python
from sabo_lit.core import ValidatedStructureState, Strategy, StrategyContext
```

Sub-agents MUST use this form. `from sabo_lit.core.schemas import …` and
`from sabo_lit.core.interfaces import …` are **discouraged in
non-`core/` code** — they couple a consumer to the internal layout of
`core/`, and a future split or rename would silently miss them.

Inside `core/` itself, sibling modules use relative imports
(`from .schemas import X`). That is the only context where reaching into a
submodule is appropriate.

The full re-export list is in `core/__init__.py`; adding a new symbol to
the public surface means adding it to that `__all__`.

## 15. Event bus and the PnL feedback loop

Cross-module asynchronous communication is typed and goes through the
`EventBus` (`core.events`). Each topic carries exactly one Pydantic event
type; the topic-event binding lives next to the event in
`core/schemas.py`, e.g. `TRADE_CLOSED_TOPIC` next to `TradeClosedEvent`.

**Rule.** Producers and subscribers MUST import the topic constant.
String literals in `bus.publish(...)` / `bus.subscribe(...)` are a
contract violation — a typo would route silently to nowhere.

**PnL feedback loop.** This is the canonical example of the bus pattern
and must not be replaced by a direct call.

1. `ExecutionBroker.close` publishes `TradeClosedEvent` on
   `TRADE_CLOSED_TOPIC` after every full or partial close. The broker
   has no knowledge of any specific consumer.
2. `RiskManager` implementations subscribe to `TRADE_CLOSED_TOPIC` during
   their bootstrap. Their handler forwards the event to
   `record_closed_trade`.
3. `current_limits()` therefore reflects the cumulative state of every
   `TradeClosedEvent` consumed by the manager since process start (plus
   any state rehydrated from persistence).
4. Other consumers (dashboard, audit log) may subscribe to the same
   topic without affecting the risk path.

Any change to this loop — direct call from broker to risk, an
intermediate aggregator, a typed return value from `close()` consumed
synchronously by something else — is a contract change and pauses the
current phase.

---

If a rule seems wrong in a specific case, stop and ask. Do not work around it.
