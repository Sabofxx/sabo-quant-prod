---
name: sabo-conventions
description: >-
  Operating contract for the sabo_lit codebase. Use whenever writing,
  modifying or reviewing any code in the sabo_lit project — every module
  under sabo_lit/, every test, every governance rule. Covers the LIT gate
  sovereignty, typed cross-module contracts, the research/production
  firewall, identifier conventions, canonical imports, the event-bus PnL
  loop, and subagent scope discipline. Load this before touching sabo_lit
  source.
---

# sabo_lit — operating contract

You are working on sabo_lit, a typed, modular LIT-based trading system.
This skill is the condensed contract. The authoritative file is
`CONVENTIONS.md` at the repo root — if anything here is ambiguous, read it.
Do not work around a rule; if one seems wrong for your case, STOP and ask.

## Scope discipline — read first
- One subagent works on exactly ONE folder. Never modify `core/`,
  `governance/`, or another module's source.
- If a change to `core/` or `governance/` seems necessary: STOP and ask.
  Contract changes go through a dedicated contract phase, not a subagent.
- Any new inter-module dependency is declared in
  `governance/dependency_rules.py` first.

## The LIT gate is sovereign — non-negotiable
- `StructureValidator` (in `lit/`) is the ONLY producer of
  `ValidatedStructureState`. No other module constructs it.
- `InducementEvent.confirmed` is `Literal[True]`; only `lit/` constructs
  confirmed inducement events.
- Every component downstream of the gate accepts `ValidatedStructureState`
  as input — never a plain `StructureState`.
- The AI arbiter (`ProbabilisticArbiter`) operates strictly downstream:
  it can REJECT or WEIGHT a validated setup, never manufacture a signal
  from an unvalidated one.
- ICT/SMC filters only ADJUST a confidence score; they never validate or
  invalidate a setup.

## Typed contracts
- All cross-module communication uses Pydantic v2 models from
  `core/schemas.py`. No untyped `dict`, no `Any` on a public boundary.
- Type hints mandatory on every public function, method, attribute.
- `RiskManager` is the only producer of `ApprovedRiskDecision`.
  `ExecutionBroker.execute` requires that exact type.
- `ComponentRegistry.register` requires a complete `ComponentManifest`
  AND `ValidationReport`. `OutOfSamplePerformance` / `ValidationReport`
  are constructed only in `backtest/` or `validation/`.
- All monetary values are `Decimal`, never `float`.

## Canonical imports
- Import from core ONE way: `from sabo_lit.core import X`.
- `from sabo_lit.core.schemas import ...` is discouraged outside `core/`.
- Inside `core/` itself, sibling relative imports are the only exception.

## Identifiers
- Default: UUID4 string (`uuid.uuid4().hex`, 32-char lowercase hex),
  generated at construction by the producing module.
- Exceptions (domain identifiers, not UUID4): `composite_id` (stable
  hash), `component_id`, `version`, `model_id`, `provider_id`.
- Cross-references carry the producer's id verbatim.

## Research / production firewall
- No production module imports from `research/`, `sandbox/`, `notebooks/`,
  `experiments/`. Enforced by the dependency scanner in CI.
- The only path from research to production is a `PromotionRequest`
  validated by `governance/`.

## Event bus and the PnL loop
- Each topic carries exactly one Pydantic event type. Import the topic
  constant from `core/schemas.py`; never use a string literal in
  `bus.publish` / `bus.subscribe`.
- PnL loop: `ExecutionBroker.close` publishes `TradeClosedEvent` on
  `TRADE_CLOSED_TOPIC`. `RiskManager` subscribes. The broker never calls
  the risk manager directly.

## Sync / async
- I/O at the boundary (data, microstructure, broker, bus) is `async`.
- Pipeline compute (mapper, detector, validator, regime, sub-strategy,
  filters, features, model, arbiter, risk eval, position management) is
  sync. `Strategy.on_candle` bridges the two.

## Portability
- `device` comes from config (`mps`/`cuda`/`cpu`), never hardcoded.
- PyTorch: save `state_dict` only; reload with `map_location`.

## Tooling
- Python 3.12+. `ruff` is the only linter/formatter. No hardcoded paths,
  no secrets in source (env vars via `${VAR}` in YAML).

## Errors
- Cross-module errors derive from `SaboLitError`. Each failure category
  carries a typed payload (`BrokerRejection`, `ExecutionFailure`,
  `DataFeedInterruption`). No bare exceptions, no silent failures.

## Governance
- No new model/feature/sub-strategy without a registered
  `ComponentManifest`. "It improves the backtest" is not sufficient —
  out-of-sample gain and regime stability are required.
- At comparable edge, the simpler robust variant always wins.
