"""
Abstract base classes for every pluggable component in sabo_lit.

Implementations live in their respective modules. ``core/`` defines the
contract; no other module is allowed to define cross-module interfaces.

Read CONVENTIONS.md before modifying anything here. A change to this file
is a change to the system's central contract and propagates to every
downstream module.

NON-NEGOTIABLE INVARIANTS (enforced through type signatures + the
governance dependency scanner; defense in depth is typing + scanner, no
runtime crypto — see schemas.py top-of-file note):

* The LIT gate is sovereign. ``StructureValidator`` is the only producer
  of ``ValidatedStructureState``, and every component downstream of the
  gate (``SecondaryFilter``, ``SubStrategy``, ``FeaturePipeline``,
  ``ProbabilisticArbiter``, ``Strategy``, ``RiskManager``,
  ``PositionManager``) accepts only that exact type. A
  ``ConstructionRule`` in ``governance/dependency_rules.py`` restricts
  instantiation of ``ValidatedStructureState`` to ``sabo_lit.lit`` and
  is verified in CI by ``DependencyPolicy.scan``.
* ``ProbabilisticArbiter.arbitrate`` returns ``Signal | None`` from an
  already-validated input — it can only REJECT or WEIGHT, never
  manufacture a signal from an unvalidated setup.
* ``RiskManager`` is the only producer of ``ApprovedRiskDecision``, and
  ``ExecutionBroker.execute`` requires that exact type — risk cannot be
  bypassed by any code path. ``ConstructionRule`` restricts
  ``ApprovedRiskDecision`` instantiation to ``sabo_lit.risk``.
* ``ExecutionBroker.execute`` raises ``BrokerRejectionError`` or
  ``ExecutionFailureError`` on failure; never returns silently on error.
* Data streams raise ``DataFeedInterruptionError`` on disconnect / stale
  feed; never yield silently when the feed is broken.
* The realised PnL feedback loop runs through the typed event bus, NOT
  through a direct broker-to-risk call:
    - ``ExecutionBroker.close`` publishes ``TradeClosedEvent`` on
      ``TRADE_CLOSED_TOPIC`` after every full or partial close.
    - ``RiskManager`` subscribes to that topic during bootstrap and
      forwards each received event to ``record_closed_trade``.
  The broker has no knowledge of the risk manager; multiple independent
  subscribers (risk, dashboard, audit log) may consume the same event.
* ``OutOfSamplePerformance`` and ``ValidationReport`` are construction-
  restricted to ``backtest/`` and ``validation/``.
  ``ComponentRegistry.register`` and ``PromotionRequest`` therefore
  cannot receive hand-written metrics — CI flags any attempt before
  runtime.
* ``Strategy`` exposes its ``StrategyContext`` as a property so that the
  full dependency graph is observable; Strategy implementations MUST NOT
  instantiate pipeline components themselves.

SYNC vs ASYNC (deliberate split — see ``Strategy`` docstring):

* I/O at the system boundary (data, microstructure, broker, event bus) is
  **async**.
* Pure compute inside the pipeline (mapper, detector, phase, validator,
  regime, sub-strategy, filters, feature pipeline, signal model, arbiter,
  risk evaluation, position management decisions) is **sync**.
* ``Strategy.on_candle`` bridges the two: it awaits I/O at the edges and
  drives the synchronous compute pipeline in between.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .schemas import (
    ApprovedRiskDecision,
    BrokerRejection,
    Candle,
    ComplexityReport,
    ComponentManifest,
    ConfidenceState,
    DataFeedInterruption,
    DependencyViolation,
    ExecutionFailure,
    ExitDecision,
    FeatureVector,
    FilterResult,
    InducementEvent,
    LiquidityZone,
    MarketPhase,
    MarketRegime,
    Order,
    OrderFlowSnapshot,
    Position,
    PositionUpdate,
    PromotionRequest,
    RiskDecision,
    Signal,
    SignalModelOutput,
    SubStrategyDecision,
    SubStrategyKind,
    SweepEvent,
    ValidatedStructureState,
    ValidationReport,
)


# ============================================================================
# Typed errors — every public boundary documents what it raises and carries
# a typed payload. No bare exceptions, no silent failures.
# ============================================================================
class SaboLitError(Exception):
    """Project-wide base exception. All cross-module errors derive from
    this class so callers can catch broadly when desired.
    """


class BrokerRejectionError(SaboLitError):
    """Raised by ``ExecutionBroker.execute`` (and ``apply_update`` /
    ``close``) when the broker rejects the operation pre-trade
    (insufficient margin, instrument unavailable, market closed, etc.).

    The ``.rejection`` attribute carries the typed ``BrokerRejection``
    payload so the caller can route, log or surface it without parsing
    free-form strings.
    """

    rejection: BrokerRejection

    def __init__(self, rejection: BrokerRejection) -> None:
        super().__init__(rejection.reason)
        self.rejection = rejection


class ExecutionFailureError(SaboLitError):
    """Raised by ``ExecutionBroker.execute`` (and ``apply_update`` /
    ``close``) when the broker accepted the operation but execution
    failed (requote, timeout, partial fill abandoned).

    The ``.failure`` attribute carries the typed ``ExecutionFailure``
    payload, including any partially filled quantity.
    """

    failure: ExecutionFailure

    def __init__(self, failure: ExecutionFailure) -> None:
        super().__init__(failure.reason)
        self.failure = failure


class DataFeedInterruptionError(SaboLitError):
    """Raised by ``DataProvider.stream`` / ``DataProvider.history`` /
    ``MicrostructureProvider.stream`` when the feed stops delivering
    (disconnect, stale tick beyond a configured threshold, provider
    error). NEVER yielded silently — callers can rely on the iterator
    raising rather than going quiet.

    The ``.interruption`` attribute carries the typed
    ``DataFeedInterruption`` payload.
    """

    interruption: DataFeedInterruption

    def __init__(self, interruption: DataFeedInterruption) -> None:
        super().__init__(interruption.reason)
        self.interruption = interruption


# ============================================================================
# Data ingestion
# ============================================================================
class DataProvider(ABC):
    """Source of OHLCV candles. One provider per market data feed.

    Both methods MAY raise ``DataFeedInterruptionError`` carrying a
    ``DataFeedInterruption`` payload when the upstream feed is unavailable
    or stale beyond its configured tolerance. Implementations MUST NOT
    swallow the failure and return empty results — silent emptiness
    cannot be distinguished from "no new candle yet".
    """

    @abstractmethod
    async def stream(self, symbol: str, timeframe: str) -> AsyncIterator[Candle]:
        """Yield candles in chronological order as they close.

        Raises ``DataFeedInterruptionError`` when the feed dies.
        """
        ...

    @abstractmethod
    async def history(
        self, symbol: str, timeframe: str, lookback: int
    ) -> list[Candle]:
        """Return the last ``lookback`` closed candles, oldest first.

        Raises ``DataFeedInterruptionError`` if the historical store is
        unavailable.
        """
        ...


class MicrostructureProvider(ABC):
    """Source of inferred microstructure snapshots.

    Raises ``DataFeedInterruptionError`` on feed loss, same contract as
    ``DataProvider``.
    """

    @abstractmethod
    async def stream(self, symbol: str) -> AsyncIterator[OrderFlowSnapshot]:
        """Yield order-flow snapshots as the book and tape update.

        Raises ``DataFeedInterruptionError`` when the feed dies.
        """
        ...


# ============================================================================
# LIT pipeline (synchronous compute)
# ============================================================================
class LiquidityMapper(ABC):
    """Maps zones where stop liquidity is INFERRED to rest."""

    @abstractmethod
    def map_zones(self, candles: list[Candle]) -> list[LiquidityZone]:
        ...


class InducementDetector(ABC):
    """Detects inducement patterns. Only emits ``InducementEvent`` if
    confirmed.

    Implementations MUST NOT construct ``InducementEvent`` (whose
    ``confirmed`` field is ``Literal[True]``) unless their detection
    criteria are met. ``CONSTRUCTION_RULES`` restricts instantiation to
    ``sabo_lit.lit``; this is the only point in the codebase where the
    gate marker is created.
    """

    @abstractmethod
    def detect(
        self,
        candles: list[Candle],
        zones: list[LiquidityZone],
        flow: OrderFlowSnapshot | None,
    ) -> InducementEvent | None:
        ...


class SweepDetector(ABC):
    """Detects liquidity sweeps — price taking out a previously-mapped
    ``LiquidityZone``. Only emits ``SweepEvent`` if its detection
    criteria are met.

    Sole producer of ``SweepEvent``. ``CONSTRUCTION_RULES`` in
    ``governance/dependency_rules.py`` restricts instantiation of
    ``SweepEvent`` to ``sabo_lit.lit``; this ABC is the only contract
    surface for sweep emission. Verified in CI by
    ``DependencyPolicy.scan``.

    Returns the FRESHEST candidate sweep when several zones qualify on
    the same input — same single-value convention as
    ``InducementDetector.detect`` (one event per call, dedup at the
    caller via ``event.swept_zone.zone_id``).
    """

    @abstractmethod
    def detect(
        self,
        candles: list[Candle],
        liquidity_zones: list[LiquidityZone],
    ) -> SweepEvent | None:
        ...


class PhaseClassifier(ABC):
    """Classifies the current LIT phase for a symbol."""

    @abstractmethod
    def classify(
        self,
        candles: list[Candle],
        latest_inducement: InducementEvent | None,
        latest_sweep: SweepEvent | None,
    ) -> MarketPhase:
        ...


class StructureValidator(ABC):
    """THE GATE.

    Returns ``ValidatedStructureState`` ONLY if the inducement event
    chain is confirmed and the structure conditions are satisfied.
    Returns ``None`` otherwise.

    Implementations live in ``sabo_lit.lit`` and ONLY there: a
    ``ConstructionRule`` in ``governance/dependency_rules.py`` forbids
    instantiation of ``ValidatedStructureState`` from any other module,
    verified in CI by ``DependencyPolicy.scan``.

    Downstream consumers (arbiter, strategy, risk, execution, position
    manager) type their inputs as ``ValidatedStructureState`` — gate
    bypass is a type error AND a CI failure.
    """

    @abstractmethod
    def validate(
        self,
        inducement: InducementEvent,
        phase: MarketPhase,
        candles: list[Candle],
    ) -> ValidatedStructureState | None:
        ...


# ============================================================================
# Secondary filters (ICT / SMC) — score adjustment only
# ============================================================================
class SecondaryFilter(ABC):
    """ICT / SMC-style filter.

    A filter can ADJUST the confidence score of an already-validated
    structure via ``FilterResult.score_adjustment``. By contract a filter
    cannot:

    * turn a non-validated structure into a validated one (the input
      type forbids it);
    * prevent the gate from being consulted;
    * emit a ``Signal``.
    """

    @abstractmethod
    def evaluate(
        self,
        validated_structure: ValidatedStructureState,
        candles: list[Candle],
    ) -> FilterResult:
        ...


# ============================================================================
# Regime
# ============================================================================
class RegimeClassifier(ABC):
    """Composite regime classifier.

    Produces ``MarketRegime``. Never produces gate decisions. ``regime/``
    is consumed by the selector, the confidence engine and the arbiter;
    it is not consumed by the gate.
    """

    @abstractmethod
    def classify(
        self,
        candles: list[Candle],
        flow: OrderFlowSnapshot | None,
    ) -> MarketRegime:
        ...


# ============================================================================
# Sub-strategies and selector
# ============================================================================
class SubStrategy(ABC):
    """A specialized sub-model.

    Inputs are always a ``ValidatedStructureState`` plus the regime. A
    sub-strategy may decline (``accepted=False``) or propose a
    ``SubStrategyDecision`` with its own entry/stop/target levels. It
    cannot by contract emit a ``Signal``.
    """

    @property
    @abstractmethod
    def kind(self) -> SubStrategyKind:
        ...

    @abstractmethod
    def evaluate(
        self,
        validated_structure: ValidatedStructureState,
        regime: MarketRegime,
        candles: list[Candle],
    ) -> SubStrategyDecision:
        ...


class StrategySelector(ABC):
    """Chooses the most appropriate ``SubStrategy`` for the current
    regime.

    A selector cannot create signals; it can only choose among already
    instantiated ``SubStrategy`` implementations registered in the
    system.
    """

    @abstractmethod
    def select(
        self,
        regime: MarketRegime,
        confidence_states: list[ConfidenceState],
    ) -> SubStrategy:
        ...


# ============================================================================
# Adaptive confidence
# ============================================================================
class ConfidenceEngine(ABC):
    """Rolling performance windows, dynamic weighting, probabilistic
    decay, online adaptation.

    Produces ``ConfidenceState``; never makes gate decisions.

    Note: ``realized_pnl`` is a ``Decimal`` so all monetary values in the
    system remain in a single numeric type; using float here would
    silently lose precision when combined with broker amounts.
    """

    @abstractmethod
    def update(
        self,
        strategy_kind: SubStrategyKind,
        realized_pnl: Decimal,
        timestamp: datetime,
    ) -> None:
        ...

    @abstractmethod
    def current(
        self, strategy_kind: SubStrategyKind, symbol: str
    ) -> ConfidenceState:
        ...


# ============================================================================
# Features and models
# ============================================================================
class FeaturePipeline(ABC):
    """Transforms raw candles, flow and the validated structure into a
    typed ``FeatureVector``.

    The pipeline requires a ``ValidatedStructureState`` — feature
    construction never happens for non-validated setups, which keeps the
    cost of the AI path conditional on the gate passing.
    """

    @abstractmethod
    def transform(
        self,
        candles: list[Candle],
        flow: OrderFlowSnapshot | None,
        validated_structure: ValidatedStructureState,
        regime: MarketRegime,
    ) -> FeatureVector:
        ...


class SignalModel(ABC):
    """A probabilistic model that scores a feature vector.

    Every concrete implementation MUST expose a complete
    ``ComponentManifest`` — the property is non-optional. This prevents
    an unregistered model from being plugged into a strategy.
    """

    @abstractmethod
    def predict(self, features: FeatureVector) -> SignalModelOutput:
        ...

    @property
    @abstractmethod
    def manifest(self) -> ComponentManifest:
        ...


class ProbabilisticArbiter(ABC):
    """The AI arbiter — operates STRICTLY downstream of the LIT gate.

    Type-level guarantees:

    * The input ``validated_structure`` is a ``ValidatedStructureState``;
      there is no overload accepting an unvalidated structure.
    * The return is ``Signal | None``. The arbiter can REJECT (``None``)
      or RETURN a ``Signal`` that re-uses the same
      ``ValidatedStructureState``. It cannot manufacture a ``Signal``
      from anything other than a gate-validated input — the ``Signal``
      schema itself requires a ``ValidatedStructureState`` field.
    """

    @abstractmethod
    def arbitrate(
        self,
        validated_structure: ValidatedStructureState,
        regime: MarketRegime,
        sub_decision: SubStrategyDecision,
        confidence: ConfidenceState,
        model_output: SignalModelOutput,
        filters: list[FilterResult],
    ) -> Signal | None:
        ...


# ============================================================================
# Strategy dependency container — explicit dependency injection.
#
# A frozen dataclass groups every collaborator that Strategy.on_candle
# needs to orchestrate the canonical pipeline. Strategy implementations
# receive a StrategyContext at construction and MUST NOT instantiate any
# pipeline component themselves — that would defeat testability and hide
# wiring from the governance registries.
#
# Pydantic was not used here because the fields are ABCs (mocked in tests
# with arbitrary classes); a dataclass is the lighter, more honest fit.
# ============================================================================
@dataclass(frozen=True, slots=True, kw_only=True)
class StrategyContext:
    """All dependencies a ``Strategy`` needs.

    Construct one at startup, passing in concrete implementations (or
    fakes/mocks in tests). The dataclass is ``frozen=True`` so a strategy
    cannot mutate its wiring at runtime; ``kw_only=True`` so a new field
    cannot silently shift positional arguments.

    ``sub_strategies`` and ``secondary_filters`` are tuples (immutable,
    ordered) rather than lists — once a strategy is built, the set of
    plugins is fixed for the run.
    """

    # Data plane
    data_provider: "DataProvider"
    microstructure_provider: "MicrostructureProvider | None"

    # LIT gate
    liquidity_mapper: "LiquidityMapper"
    inducement_detector: "InducementDetector"
    phase_classifier: "PhaseClassifier"
    structure_validator: "StructureValidator"

    # Regime + selection
    regime_classifier: "RegimeClassifier"
    strategy_selector: "StrategySelector"
    sub_strategies: tuple["SubStrategy", ...]

    # Secondary filters
    secondary_filters: tuple["SecondaryFilter", ...]

    # AI path
    feature_pipeline: "FeaturePipeline"
    signal_model: "SignalModel"
    arbiter: "ProbabilisticArbiter"

    # Adaptive
    confidence_engine: "ConfidenceEngine"


# ============================================================================
# Strategy = canonical wiring (gate -> regime -> sub-strategy -> arbiter)
# ============================================================================
class Strategy(ABC):
    """End-to-end pipeline: candles -> ``Signal | None``.

    Dependency injection
    --------------------
    Implementations receive a ``StrategyContext`` at construction. The
    abstract ``context`` property below makes the container OBSERVABLE
    from outside — tests assert against ``strategy.context.signal_model``
    rather than fishing through private attributes, and the governance
    layer can introspect the wired graph.

    Implementations MUST NOT instantiate pipeline components themselves;
    every collaborator comes through the ``StrategyContext``. This is
    what makes a strategy testable with mocked components.

    Canonical wiring
    ----------------
    1. ``LiquidityMapper`` -> zones
    2. ``InducementDetector`` -> ``InducementEvent`` (or stop)
    3. ``PhaseClassifier`` -> ``MarketPhase``
    4. ``StructureValidator`` -> ``ValidatedStructureState`` (or stop)
    5. ``RegimeClassifier`` -> ``MarketRegime``
    6. ``StrategySelector`` -> ``SubStrategy``
    7. ``SubStrategy.evaluate`` -> ``SubStrategyDecision``
    8. ``SecondaryFilter[*].evaluate`` -> ``list[FilterResult]``
    9. ``FeaturePipeline.transform`` -> ``FeatureVector``
    10. ``SignalModel.predict`` -> ``SignalModelOutput``
    11. ``ProbabilisticArbiter.arbitrate`` -> ``Signal | None``

    An implementation that emits a ``Signal`` whose
    ``validated_structure`` did not come from step 4 violates the
    architecture and will not type-check against this interface.

    Sync vs async
    -------------
    ``on_candle`` is **async** because it must await I/O at the edges:
    the data and microstructure providers, the event bus (downstream
    publication of the produced Signal), and the broker call that will
    eventually consume the Signal. Steps 1 through 11 themselves are
    **sync** — they are pure compute over buffers already in memory, and
    making them async would force every caller to await without any real
    concurrency benefit. This split is acted upon throughout ``core/``:
    I/O at the boundary is async, compute in the middle is sync.
    """

    @property
    @abstractmethod
    def context(self) -> StrategyContext:
        """The dependency container received at construction.

        Required so that strategies are testable, observable by
        governance, and impossible to "secretly" wire up.
        """
        ...

    @abstractmethod
    async def on_candle(self, candle: Candle) -> Signal | None:
        ...


# ============================================================================
# Risk
# ============================================================================
class RiskManager(ABC):
    """The only component that can produce ``ApprovedRiskDecision``.

    ``ConstructionRule`` restricts instantiation of
    ``ApprovedRiskDecision`` to ``sabo_lit.risk`` — even an
    implementation bug elsewhere cannot fabricate one.

    Realised PnL feedback loop — bus-based
    --------------------------------------
    Realised PnL reaches the risk manager via the typed event bus, not
    via a direct broker-to-risk call:

    * ``ExecutionBroker.close`` publishes ``TradeClosedEvent`` on
      ``TRADE_CLOSED_TOPIC`` after every full or partial close.
    * ``RiskManager`` implementations subscribe to that topic during
      their bootstrap (typically by registering a handler that calls
      ``record_closed_trade`` with the event's fields).

    The broker has no knowledge of the risk manager; the risk manager
    has no knowledge of which broker produced the event. Adding another
    consumer (dashboard, audit log) does not affect the risk path.

    Consequently, ``current_limits()`` reflects the state derived from
    every ``TradeClosedEvent`` consumed by the manager since process
    start (plus any state rehydrated from persistence at boot).
    """

    @abstractmethod
    def evaluate(
        self, signal: Signal, open_positions: list[Position]
    ) -> RiskDecision:
        """Returns either an ``ApprovedRiskDecision`` (when execution is
        allowed) or a base ``RiskDecision`` with ``approved=False``.
        """
        ...

    @abstractmethod
    def record_closed_trade(
        self,
        position: Position,
        realized_pnl: Decimal,
        closed_at: datetime,
    ) -> None:
        """Update the rolling daily / weekly loss accounting from a closed
        trade.

        Invoked by the manager's own subscription handler when a
        ``TradeClosedEvent`` arrives on ``TRADE_CLOSED_TOPIC``. Tests and
        reconciliation tools may also call it directly. Without
        ``TradeClosedEvent`` flowing on the bus, ``current_limits()``
        returns stale ``daily_loss_used`` / ``weekly_loss_used``.
        """
        ...

    @abstractmethod
    def current_limits(self) -> dict[str, Decimal]:
        """Return at least the keys ``global_stop_loss``,
        ``daily_loss_used``, ``daily_loss_limit``, ``weekly_loss_used``,
        ``weekly_loss_limit``.

        Reflects the cumulative state derived from every
        ``TradeClosedEvent`` consumed by the manager (plus any
        rehydrated state). Values are ``Decimal`` — no precision drift
        between broker, risk manager and dashboard.
        """
        ...


# ============================================================================
# Execution
# ============================================================================
class ExecutionBroker(ABC):
    """Executes orders and applies in-flight position changes.

    ``execute``, ``apply_update`` and ``close`` REQUIRE typed inputs
    (``ApprovedRiskDecision``, ``PositionUpdate``, ``ExitDecision``) so
    that risk approval and position-management provenance are always
    present. ``mode`` is frozen at construction: the interface exposes
    no setter and no mutating method.

    Failure contract
    ----------------
    Every mutating method may raise:

    * ``BrokerRejectionError`` — broker refused pre-trade. Carries a
      typed ``BrokerRejection``.
    * ``ExecutionFailureError`` — broker accepted but execution failed
      (requote, timeout, partial fill abandoned). Carries a typed
      ``ExecutionFailure``.

    Implementations MUST NOT return a sentinel or ``None`` to signal
    failure: callers should be able to distinguish "no result yet" from
    "broker refused" through the exception system.

    Event-bus contract
    ------------------
    ``close`` publishes a ``TradeClosedEvent`` on the canonical topic
    ``TRADE_CLOSED_TOPIC`` (defined in ``core.schemas``). The broker
    must not assume a specific consumer of that event; the risk manager
    subscribes independently, as may other modules. This is the only
    coupling between the broker and the risk feedback loop.
    """

    @abstractmethod
    async def execute(
        self, order: Order, risk_decision: ApprovedRiskDecision
    ) -> Position:
        """Open a position. Raises ``BrokerRejectionError`` or
        ``ExecutionFailureError`` on failure.
        """
        ...

    @abstractmethod
    async def apply_update(self, update: PositionUpdate) -> Position:
        """Apply a ``PositionUpdate`` (modify SL/TP/trailing) to an open
        position. Returns the updated ``Position``.

        Raises ``BrokerRejectionError`` if the broker refuses the
        modification, ``ExecutionFailureError`` if the modification
        fails after acceptance.
        """
        ...

    @abstractmethod
    async def close(self, decision: ExitDecision) -> Position:
        """Close a position (fully or partially) according to
        ``ExitDecision.quantity_to_close``. Returns the resulting
        ``Position`` (``CLOSED`` or ``PARTIALLY_CLOSED``).

        After a successful close, implementations MUST publish a
        ``TradeClosedEvent`` on ``TRADE_CLOSED_TOPIC``. The risk manager
        and any other interested consumer subscribe to that topic
        independently of the broker. Implementations MUST NOT call the
        risk manager directly — the bus is the contract.

        Raises ``BrokerRejectionError`` / ``ExecutionFailureError``.
        """
        ...

    @abstractmethod
    async def positions(self) -> list[Position]:
        ...

    @abstractmethod
    async def cancel(self, order_id: str) -> None:
        ...

    @property
    @abstractmethod
    def mode(self) -> str:
        """``"paper"`` or ``"live"``. Frozen at construction by contract.
        """
        ...


# ============================================================================
# Position management — lifecycle AFTER entry.
#
# ``PositionManager`` covers what happens between the moment
# ``ExecutionBroker.execute`` returns a ``Position`` and the moment that
# position is fully closed: trailing stops, partial profit takes, time
# stops, risk-driven exits. It does NOT itself talk to the broker — the
# strategy / risk layer applies its outputs through
# ``ExecutionBroker.apply_update`` / ``ExecutionBroker.close``.
# ============================================================================
class PositionManager(ABC):
    """Manages open positions after entry.

    ``evaluate`` returns one of:

    * ``None``           — no action this tick;
    * ``PositionUpdate`` — modify SL / TP / trailing in place;
    * ``ExitDecision``   — close fully or partially.

    The decision is then applied via ``ExecutionBroker``. The position
    manager is pure compute and therefore sync; the broker layer
    converts decisions into async I/O.
    """

    @abstractmethod
    def evaluate(
        self,
        position: Position,
        candles: list[Candle],
        flow: OrderFlowSnapshot | None,
    ) -> PositionUpdate | ExitDecision | None:
        ...


# ============================================================================
# Backtest
# ============================================================================
class BacktestEngine(ABC):
    """Replays candles through a ``Strategy`` and produces a
    ``ValidationReport`` suitable for a ``PromotionRequest``.

    Implementations live in ``sabo_lit.backtest`` — the only module
    (alongside ``sabo_lit.validation``) allowed to construct
    ``ValidationReport`` and ``OutOfSamplePerformance``, per
    ``ConstructionRule``.
    """

    @abstractmethod
    async def run(
        self,
        strategy: Strategy,
        candles: Iterable[Candle],
    ) -> ValidationReport:
        ...


# ============================================================================
# Validation
# ============================================================================
class ValidationSuite(ABC):
    """Common interface for robustness suites (walk-forward, Monte
    Carlo, out-of-sample, overfitting, regime stability).

    Every suite produces a fully populated ``ValidationReport`` for the
    component under test. Construction of ``ValidationReport`` and its
    sub-reports is restricted to ``backtest/`` and ``validation/`` by
    ``ConstructionRule``.
    """

    @abstractmethod
    def run(self, component_id: str) -> ValidationReport:
        ...


# ============================================================================
# Governance
# ============================================================================
class ComponentRegistry(ABC):
    """Common interface for model, feature, sub-strategy and filter
    registries.

    The registry refuses any registration without ``ComponentManifest``
    AND ``ValidationReport`` together — the type signature alone enforces
    this. On top of that, both objects are construction-restricted to
    ``backtest/`` and ``validation/``, so the registry cannot be tricked
    with hand-written metrics: any such construction site is caught by
    ``DependencyPolicy.scan`` in CI before the change can be merged.
    """

    @abstractmethod
    def register(
        self, manifest: ComponentManifest, validation: ValidationReport
    ) -> None:
        ...

    @abstractmethod
    def get(self, component_id: str) -> ComponentManifest | None:
        ...

    @abstractmethod
    def list_all(self) -> list[ComponentManifest]:
        ...

    @abstractmethod
    def promote(self, request: PromotionRequest) -> None:
        """Promote a research artifact to production.

        Sole entry point from ``research/`` or ``sandbox/`` to a
        production module. ``PromotionRequest`` requires a complete
        ``ComponentManifest`` AND a complete ``ValidationReport``, both
        construction-restricted to ``backtest/`` or ``validation/``.
        Promotion is therefore structurally impossible without both,
        and CI catches any attempt to forge either nested object.
        """
        ...


class ComplexityMonitor(ABC):
    """Exposes complexity metrics. Does NOT take trading decisions."""

    @abstractmethod
    def snapshot(self) -> ComplexityReport:
        ...


class DependencyPolicy(ABC):
    """Declares and verifies import / construction rules between modules.

    A non-empty ``scan`` result MUST cause the test suite to fail.
    Governance blocks rather than reports.

    The implementation of ``scan`` is the FIRST deliverable of Phase 1
    — see ``PHASE_PLAN.md`` at the repository root. Until ``scan``
    exists and is wired to CI, every ``ConstructionRule`` and
    ``ForbiddenImportRule`` declared in
    ``governance/dependency_rules.py`` is inert.
    """

    @abstractmethod
    def allowed_imports(self) -> dict[str, frozenset[str]]:
        """Map of importer module name -> set of allowed imported module
        names.

        Production modules MUST NOT include ``research``, ``sandbox`` or
        ``notebooks`` in their allowed set.
        """
        ...

    @abstractmethod
    def scan(self, root_path: str) -> list[DependencyViolation]:
        """Scan the source tree rooted at ``root_path`` and return every
        violation of the rules declared in
        ``governance/dependency_rules.py``.

        Detects two rule kinds:

        1. ``ForbiddenImportRule`` — any ``import M`` or
           ``from M import …`` inside a forbidden importer module is
           reported. Aliased imports (``import M as N``) and re-exports
           must also be detected.

        2. ``ConstructionRule`` — any CALL-SITE instantiation of a
           restricted symbol outside its allowed constructor modules is
           reported. BOTH the plain ``Cls(...)`` form AND the
           ``Cls.model_construct(...)`` escape hatch must be detected.
           Type annotations (``x: Cls``, ``Callable[..., Cls]``) and
           bare ``from m import Cls`` lines are NOT call sites and
           remain permitted everywhere.

        Implementations SHALL parse each ``.py`` file's AST (no string
        matching) so that ``from m import Cls as C; C(...)`` is correctly
        classified as a construction of ``m.Cls``.

        A non-empty return MUST cause the test suite to fail and CI to
        block — governance is preventive, not informational.
        """
        ...
