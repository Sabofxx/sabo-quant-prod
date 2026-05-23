"""
Shared Pydantic schemas — the only allowed inter-module communication contract.

These types intentionally encode invariants at the type level so that violations
are caught at construction time rather than runtime. In particular:

- ``InducementEvent.confirmed`` is ``Literal[True]``: only confirmed events exist
  as instances of the type.
- ``ValidatedStructureState`` is a distinct subclass of ``StructureState``. Every
  downstream contract (arbiter, strategy, risk, execution, position manager)
  accepts only that exact type. ``governance/dependency_rules.py`` declares a
  ``ConstructionRule`` restricting instantiation to ``sabo_lit.lit``; the
  ``DependencyPolicy.scan`` check in CI flags any call-site elsewhere.
- ``ApprovedRiskDecision`` is the only acceptable input for the broker. A
  parallel ``ConstructionRule`` restricts its instantiation to ``sabo_lit.risk``.
- ``OutOfSamplePerformance`` and ``ValidationReport`` are restricted by
  ``ConstructionRule`` to ``sabo_lit.backtest`` and ``sabo_lit.validation``.
  ``ComponentManifest`` and ``PromotionRequest`` therefore cannot embed
  hand-written metrics — CI flags any attempt before runtime. ``run_id`` is
  retained as a traceability link to the originating run in experiment
  tracking, independently of any signature scheme.
- ``ComponentManifest``, ``ValidationReport`` and ``PromotionRequest`` have NO
  optional fields — registries refuse incomplete artifacts at construction time.

DEFENSE-IN-DEPTH MODEL (typing + scanner; no runtime crypto):

  Layer 1 — typing: the Python type system catches accidental misuse before
            runtime. Subclassing, ``Literal`` markers and required typed
            inputs make most bypasses a static error.
  Layer 2 — scanner: ``DependencyPolicy.scan`` (governance) reads the
            declarative rules in ``governance/dependency_rules.py`` and
            fails CI on any forbidden import or forbidden construction site.
            This catches deliberate forgery inside the monorepo before merge.

  A previously considered HMAC-based runtime layer was REMOVED in Phase 0.2:
  ``model_dump_json`` is not deterministic across Pydantic versions and
  Python platforms (M1, Linux VPS), so a runtime token could reject a
  legitimate object after a routine upgrade. The threat that layer addressed
  (a hostile module inside the monorepo) is sufficiently covered by Layer 2.

Read CONVENTIONS.md before modifying anything here.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ----------------------------------------------------------------------------
# Common base. Every cross-module schema is frozen + strict + extra=forbid to:
#   * prevent silent mutation after construction;
#   * prevent silent coercion (e.g. "1" -> 1) on a type boundary;
#   * reject unknown fields so contract drift surfaces immediately.
# ----------------------------------------------------------------------------
class FrozenStrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


# ============================================================================
# Market data
# ============================================================================
class Candle(FrozenStrictModel):
    """OHLCV candle. Single timeframe; aggregation is performed upstream."""

    symbol: str
    timeframe: str  # e.g. "M1", "M5", "H1"
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    tick_count: int = Field(ge=0, default=0)


class OrderFlowSnapshot(FrozenStrictModel):
    """Point-in-time microstructure snapshot inferred from public market data.

    No claim is made about real institutional order flow — the values are
    proxies built from the book and the trade tape.
    """

    symbol: str
    timestamp: datetime
    bid_volume: Decimal
    ask_volume: Decimal
    delta: Decimal  # signed buy-sell pressure proxy
    cumulative_delta: Decimal
    imbalance_ratio: float
    spread: Decimal


# ============================================================================
# LIT primitives
# ============================================================================
class LiquidityZoneKind(str, Enum):
    EQUAL_HIGHS = "equal_highs"
    EQUAL_LOWS = "equal_lows"
    TRENDLINE_LIQUIDITY = "trendline_liquidity"
    # ROLLING_HIGH / ROLLING_LOW are the highest / lowest extreme of a
    # rolling lookback window of candles. The window is timezone-free —
    # naming it "rolling" rather than "session" avoids confusing this
    # purely mechanical aggregation with the regime-aware SessionLabel
    # produced by the regime engine (Phase 2).
    ROLLING_HIGH = "rolling_high"
    ROLLING_LOW = "rolling_low"
    PREVIOUS_DAY_HIGH = "previous_day_high"
    PREVIOUS_DAY_LOW = "previous_day_low"


class LiquidityZone(FrozenStrictModel):
    """A zone where stop liquidity is INFERRED to accumulate.

    ``zone_id`` is a DETERMINISTIC hash of
    ``(kind, level_mid quantized to equal_level_tolerance_pips,
    anchor_timestamp)`` — see CONVENTIONS.md §13. Two emissions of the
    same conceptual zone (same call or different calls on overlapping
    windows) produce the same ``zone_id``, so a sliding-window consumer
    can deduplicate without inventing its own identity.
    """

    zone_id: str  # deterministic 16-char hex — see CONVENTIONS.md §13
    symbol: str
    kind: LiquidityZoneKind
    price_upper: Decimal
    price_lower: Decimal
    created_at: datetime
    strength_score: float = Field(ge=0.0, le=1.0)


class InducementEvent(FrozenStrictModel):
    """A confirmed inducement pattern — the seed of the LIT gate.

    The ``confirmed`` literal is the type-level marker that distinguishes
    an inducement that has passed its detector's criteria from a mere
    candidate. ``InducementPatternDetector`` in ``lit/`` is the ONLY
    component allowed to construct instances of this class; the
    ``ConstructionRule`` in ``governance/dependency_rules.py`` is verified
    in CI by ``DependencyPolicy.scan``.
    """

    # Deterministic 16-char hex of (kind, inducement_zone.zone_id,
    # timestamp). Two re-emissions of the same conceptual inducement
    # collapse to the same event_id, so sliding-window consumers can
    # dedupe without a side-table. See CONVENTIONS.md §13.
    event_id: str
    symbol: str
    timestamp: datetime
    inducement_zone: LiquidityZone
    confirmed: Literal[True]  # type-level invariant — see class docstring
    inferred_direction: Literal["bullish", "bearish"]
    confidence: float = Field(ge=0.0, le=1.0)


class SweepEvent(FrozenStrictModel):
    """An inferred liquidity sweep — price taking out a liquidity zone.

    Constructible only by ``SweepDetector`` implementations in
    ``sabo_lit.lit`` — see ``CONSTRUCTION_RULES``.
    """

    # Deterministic 16-char hex of (kind, swept_zone.zone_id, timestamp).
    # Same dedup contract as InducementEvent.event_id. See
    # CONVENTIONS.md §13.
    sweep_id: str
    symbol: str
    timestamp: datetime
    swept_zone: LiquidityZone
    swept_direction: Literal["upside", "downside"]
    rejection_strength: float = Field(ge=0.0, le=1.0)


class PhaseKind(str, Enum):
    PHASE_1_INDUCEMENT = "phase_1_inducement"
    PHASE_2_MITIGATION = "phase_2_mitigation"
    UNDEFINED = "undefined"


class MarketPhase(FrozenStrictModel):
    """Current LIT phase classification for a symbol."""

    symbol: str
    timestamp: datetime
    phase: PhaseKind
    confidence: float = Field(ge=0.0, le=1.0)


class StructureState(FrozenStrictModel):
    """Base structure state. NOT validated until promoted to
    ``ValidatedStructureState``.

    Plain ``StructureState`` is the unvalidated form that the validator MAY
    consume internally. Downstream components MUST type their inputs as
    ``ValidatedStructureState`` — that is what makes the gate inviolable
    by the type system.
    """

    symbol: str
    timestamp: datetime
    inferred_direction: Literal["bullish", "bearish"]
    phase: MarketPhase


class ValidatedStructureState(StructureState):
    """Output of the LIT gate. Constructible only by ``StructureValidator``.

    Type-level invariants:

    * ``gate_passed`` is ``Literal[True]``.
    * ``inducement_event_id`` is mandatory — traceability back to the
      gate-validating inducement.

    Defense in depth (two layers, both required):

    1. **Typing**: this class is a distinct subclass of ``StructureState``.
       Every downstream contract (arbiter, strategy, risk, execution,
       position manager) types its inputs as ``ValidatedStructureState``.
       Feeding an unvalidated structure forward is a static type error.
    2. **Scanner**: ``governance/dependency_rules.py`` declares a
       ``ConstructionRule`` restricting instantiation of this class to
       ``sabo_lit.lit``. ``DependencyPolicy.scan`` reports any call-site
       outside ``lit/`` as a ``DependencyViolation`` and fails CI.

    These two layers are sufficient: Layer 1 prevents accidental misuse
    before runtime; Layer 2 prevents deliberate forgery inside the
    monorepo before merge. No runtime cryptographic ceremony is required.
    """

    gate_passed: Literal[True] = True
    inducement_event_id: str  # UUID — mandatory pointer to the originating InducementEvent
    sweep_event_id: str | None = None  # optional Phase-2 confirmation


# ============================================================================
# Secondary filters (ICT / SMC)
# ============================================================================
class FilterResult(FrozenStrictModel):
    """Output of a ``SecondaryFilter``.

    Filters can only ADJUST a confidence score (positive or negative) on a
    setup that the gate has already validated. They never produce or revoke
    a validation.
    """

    filter_name: str
    score_adjustment: float = Field(ge=-1.0, le=1.0)
    reason: str
    metadata: dict[str, float] = Field(default_factory=dict)


# ============================================================================
# Regime
# ============================================================================
class VolatilityRegimeLabel(str, Enum):
    CONTRACTED = "contracted"
    NORMAL = "normal"
    EXPANDED = "expanded"
    EXTREME = "extreme"


class LiquidityRegimeLabel(str, Enum):
    THIN = "thin"
    NORMAL = "normal"
    DEEP = "deep"


class SessionLabel(str, Enum):
    SYDNEY = "sydney"
    TOKYO = "tokyo"
    LONDON = "london"
    NEW_YORK = "new_york"
    OVERLAP_LONDON_NY = "overlap_london_ny"
    DEAD = "dead"


class MacroRegimeLabel(str, Enum):
    RISK_ON = "risk_on"
    RISK_OFF = "risk_off"
    NEUTRAL = "neutral"


class MarketRegime(FrozenStrictModel):
    """Composite regime descriptor produced by ``RegimeClassifier``.

    Regime informs sub-strategy selection and confidence weighting; it
    cannot enable or disable the LIT gate.
    """

    symbol: str
    timestamp: datetime
    volatility: VolatilityRegimeLabel
    liquidity: LiquidityRegimeLabel
    session: SessionLabel
    macro: MacroRegimeLabel
    # composite_id is a STABLE HASH of (symbol, volatility, liquidity, session,
    # macro) — same inputs always produce the same string. Format documented in
    # CONVENTIONS.md §13. NOT a UUID4: callers must be able to recompute it.
    composite_id: str


# ============================================================================
# Sub-strategies
# ============================================================================
class SubStrategyKind(str, Enum):
    LIT_TREND_CONTINUATION = "lit_trend_continuation"
    LIT_REVERSAL = "lit_reversal"
    LIQUIDITY_EXHAUSTION = "liquidity_exhaustion"
    SESSION_MANIPULATION = "session_manipulation"
    VOLATILITY_EXPANSION = "volatility_expansion"
    COMPRESSION_BREAKOUT = "compression_breakout"
    SWEEP_CONTINUATION = "sweep_continuation"
    MEAN_REVERSION_POST_INDUCEMENT = "mean_reversion_post_inducement"


class SubStrategyDecision(FrozenStrictModel):
    """A sub-strategy's verdict on a validated setup."""

    strategy_kind: SubStrategyKind
    accepted: bool
    score: float = Field(ge=0.0, le=1.0)
    suggested_stop: Decimal
    suggested_target: Decimal
    rationale: str


# ============================================================================
# Adaptive confidence
# ============================================================================
class ConfidenceState(FrozenStrictModel):
    """Rolling confidence assigned to a sub-strategy by the
    ``ConfidenceEngine``.
    """

    strategy_kind: SubStrategyKind
    symbol: str
    timestamp: datetime
    weight: float = Field(ge=0.0, le=1.0)
    rolling_win_rate: float = Field(ge=0.0, le=1.0)
    rolling_expectancy: Decimal
    decay_factor: float = Field(ge=0.0, le=1.0)
    sample_size: int = Field(ge=0)


# ============================================================================
# Models — typed inference IO
# ============================================================================
class FeatureVector(FrozenStrictModel):
    """A typed feature vector handed to a ``SignalModel``."""

    symbol: str
    timestamp: datetime
    feature_names: tuple[str, ...]
    values: tuple[float, ...]


class SignalModelOutput(FrozenStrictModel):
    """Output of a ``SignalModel``."""

    model_config = ConfigDict(
        frozen=True, strict=True, extra="forbid", protected_namespaces=()
    )

    # model_id and model_version are domain identifiers (e.g. "xgb_eurusd_m5",
    # "v3.2.1"), NOT UUID4 — see CONVENTIONS.md §13.
    model_id: str
    model_version: str
    probability: float = Field(ge=0.0, le=1.0)
    inferred_direction: Literal["bullish", "bearish"]
    uncertainty: float = Field(ge=0.0, le=1.0)


# ============================================================================
# Final trading signal (post-arbitration)
# ============================================================================
class Signal(FrozenStrictModel):
    """Final signal emitted by ``Strategy`` after gate + arbitrage.

    The presence of ``validated_structure`` (a ``ValidatedStructureState``)
    is the type-level proof that this signal originated from a passed LIT
    gate.
    """

    signal_id: str  # UUID4
    symbol: str
    timestamp: datetime
    direction: Literal["long", "short"]
    validated_structure: ValidatedStructureState
    chosen_strategy: SubStrategyKind
    confidence: float = Field(ge=0.0, le=1.0)
    entry: Decimal
    stop_loss: Decimal
    take_profit: Decimal


# ============================================================================
# Risk
# ============================================================================
class RiskDecision(FrozenStrictModel):
    """Base risk decision. Use ``ApprovedRiskDecision`` when execution is
    allowed.
    """

    decision_id: str  # UUID4
    signal_id: str  # UUID4, cross-reference to the originating Signal
    approved: bool
    reason: str


class ApprovedRiskDecision(RiskDecision):
    """Risk-approved decision. Only ``RiskManager`` constructs these.

    ``ConstructionRule`` restricts instantiation to ``sabo_lit.risk`` — even
    an implementation bug elsewhere cannot fabricate one.
    ``ExecutionBroker.execute`` requires this exact type, which makes it
    structurally impossible to execute an order that has not been
    risk-approved.
    """

    approved: Literal[True] = True
    position_size: Decimal
    max_loss_amount: Decimal


# ============================================================================
# Execution — orders and positions
# ============================================================================
class OrderKind(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class Order(FrozenStrictModel):
    order_id: str  # UUID4
    symbol: str
    kind: OrderKind
    side: OrderSide
    quantity: Decimal
    price: Decimal | None = None
    stop_loss: Decimal
    take_profit: Decimal
    risk_decision_id: str  # UUID4, cross-reference to the approved risk decision


class PositionStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    PARTIALLY_CLOSED = "partially_closed"


class Position(FrozenStrictModel):
    position_id: str  # UUID4
    symbol: str
    side: OrderSide
    quantity: Decimal
    entry_price: Decimal
    current_price: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    status: PositionStatus
    opened_at: datetime
    closed_at: datetime | None = None


# ============================================================================
# Position management — updates and exits AFTER entry.
# Consumed by ``PositionManager`` (interfaces.py) and applied through
# ``ExecutionBroker.apply_update`` / ``ExecutionBroker.close``.
# ============================================================================
class ExitReason(str, Enum):
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    TIME_STOP = "time_stop"
    MANUAL = "manual"
    RISK_REDUCTION = "risk_reduction"
    REVERSE_SIGNAL = "reverse_signal"


class ExitDecision(FrozenStrictModel):
    """A decision to close an open position (fully or partially)."""

    decision_id: str  # UUID4
    position_id: str  # UUID4, cross-reference to the Position being closed
    timestamp: datetime
    reason: ExitReason
    quantity_to_close: Decimal  # equals position quantity for a full close
    rationale: str


class PositionUpdate(FrozenStrictModel):
    """A modification of an open position's stop, take, or trailing
    settings, without closing it.
    """

    update_id: str  # UUID4
    position_id: str  # UUID4, cross-reference to the Position being updated
    timestamp: datetime
    new_stop_loss: Decimal | None = None
    new_take_profit: Decimal | None = None
    new_trailing_distance: Decimal | None = None
    rationale: str


# ============================================================================
# Bus events — topic/event bindings live HERE, next to the event schema, so
# producers and subscribers import both from one place.
#
# Each topic carries exactly one Pydantic event type (enforced by
# ``EventBus[T]`` in events.py). Consumers MUST use the canonical topic
# constant — string literals on bus.publish / bus.subscribe are a contract
# violation (no string typo can silently route to nowhere).
# ============================================================================

# Topic on which ExecutionBroker.close publishes a TradeClosedEvent after
# every successful close. The RiskManager subscribes during bootstrap and
# updates its rolling daily / weekly loss accounting from these events.
TRADE_CLOSED_TOPIC: str = "sabo_lit.trade.closed"


class TradeClosedEvent(FrozenStrictModel):
    """A position has been closed (fully or partially).

    Published on ``TRADE_CLOSED_TOPIC`` by ``ExecutionBroker.close`` after
    every successful close, in place of any direct call from the broker
    to the risk manager. ``RiskManager`` subscribes to this topic during
    its bootstrap and forwards each event to ``record_closed_trade`` —
    closing the PnL feedback loop without coupling the broker to the
    risk manager.

    Multiple independent subscribers (risk, dashboard, audit log) may
    consume the same event without affecting one another.
    """

    event_id: str  # UUID4
    position: Position
    realized_pnl: Decimal
    closed_at: datetime
    # Why the close happened, mirroring ExitReason values for human / log
    # consumers. Kept as str (not the ExitReason enum) so broker-driven
    # closes (e.g. a server-side stop fill) can carry a broker-specific
    # value that does not perfectly map to an ExitReason.
    triggered_by: str
    # Cross-reference to the ApprovedRiskDecision that authorised the
    # original entry. Allows the risk manager to reconcile the realised
    # PnL against the originally budgeted loss amount.
    risk_decision_id: str  # UUID4
    # Cross-reference to the ExitDecision (if the close was driven by a
    # PositionManager decision) or None if the close was triggered
    # broker-side (e.g. a server stop fill).
    exit_decision_id: str | None = None


# ============================================================================
# Typed failure payloads — carried by the typed exceptions defined in
# ``core.interfaces``. No silent failures, no untyped exceptions on a public
# boundary.
# ============================================================================
class BrokerRejection(FrozenStrictModel):
    """Returned/carried when a broker refuses an order pre-trade
    (insufficient margin, instrument unavailable, etc.).
    """

    order_id: str  # UUID4 of the offending order
    timestamp: datetime
    reason: str
    broker_code: str | None = None


class ExecutionFailure(FrozenStrictModel):
    """Returned/carried when an order was accepted by the broker but
    execution failed (requote, timeout, partial fill abandoned).
    """

    order_id: str  # UUID4 of the offending order
    timestamp: datetime
    reason: str
    partial_quantity: Decimal | None = None
    broker_code: str | None = None


class DataFeedInterruption(FrozenStrictModel):
    """Carried when a data or microstructure stream stops delivering
    values (disconnect, stale tick, provider error).
    """

    symbol: str
    # provider_id is a domain identifier (e.g. "mt5_eurusd"), NOT UUID4 —
    # see CONVENTIONS.md §13.
    provider_id: str
    last_message_at: datetime
    reason: str


# ============================================================================
# Validation / robustness
#
# WalkForwardResult, MonteCarloResult, RegimeStabilityReport and
# OverfittingReport are sub-reports embedded inside ValidationReport.
# CONSTRUCTION_RULES restricts each sub-report's instantiation to
# ``sabo_lit.validation``.
# ============================================================================
class WalkForwardResult(FrozenStrictModel):
    folds: int = Field(ge=1)
    in_sample_sharpe: float
    out_of_sample_sharpe: float
    degradation_ratio: float


class MonteCarloResult(FrozenStrictModel):
    simulations: int = Field(ge=1)
    p5_pnl: float
    p50_pnl: float
    p95_pnl: float
    max_drawdown_p95: float


class RegimeStabilityReport(FrozenStrictModel):
    regimes_tested: tuple[str, ...]
    per_regime_sharpe: tuple[tuple[str, float], ...]
    worst_regime: str
    stability_score: float = Field(ge=0.0, le=1.0)


class OverfittingReport(FrozenStrictModel):
    train_test_gap: float
    deflated_sharpe: float
    bootstrap_pvalue: float = Field(ge=0.0, le=1.0)


class ValidationReport(FrozenStrictModel):
    """Aggregate validation report. Required for promotion via
    ``PromotionRequest``.

    Defense in depth:

    1. **Typing**: every robustness section is mandatory — no Optional
       escape. ``PromotionRequest`` requires this exact type, so a
       registry cannot accept a partial report.
    2. **Scanner**: ``ConstructionRule`` restricts instantiation to
       ``sabo_lit.backtest`` and ``sabo_lit.validation``. CI flags any
       hand-written construction site in any other module.

    ``run_id`` is preserved as a traceability link to the originating
    run logged in experiment tracking — independently of any provenance
    signature.
    """

    component_id: str  # domain identifier (e.g. "xgb_eurusd_m5"), not UUID
    component_version: str  # domain identifier (e.g. "v3.2.1"), not UUID
    generated_at: datetime
    run_id: str  # UUID4 — identifier of the backtest / validation run
    walk_forward: WalkForwardResult
    monte_carlo: MonteCarloResult
    regime_stability: RegimeStabilityReport
    overfitting: OverfittingReport
    out_of_sample_passed: bool


# ============================================================================
# Governance
# ============================================================================
class ComponentType(str, Enum):
    MODEL = "model"
    FEATURE = "feature"
    SUB_STRATEGY = "sub_strategy"
    FILTER = "filter"
    REGIME_CLASSIFIER = "regime_classifier"


class ComputeCost(FrozenStrictModel):
    inference_ms_p50: float = Field(ge=0.0)
    inference_ms_p99: float = Field(ge=0.0)
    memory_mb: float = Field(ge=0.0)
    training_minutes: float = Field(ge=0.0)


class OutOfSamplePerformance(FrozenStrictModel):
    """Performance metrics drawn from a real backtest / validation run.

    A manifest is EARNED, not WRITTEN. Defense in depth:

    1. **Typing**: this class is required (non-optional) by
       ``ComponentManifest``; there is no Optional escape.
    2. **Scanner**: ``ConstructionRule`` restricts instantiation to
       ``sabo_lit.backtest`` and ``sabo_lit.validation``. CI flags any
       hand-written construction site outside those modules.

    ``run_id`` is preserved as a traceability link to the originating
    run logged in experiment tracking.
    """

    run_id: str  # UUID4 — identifier of the run that produced these metrics
    sharpe: float
    sortino: float
    max_drawdown: float
    win_rate: float = Field(ge=0.0, le=1.0)
    expectancy: Decimal


class ComponentManifest(FrozenStrictModel):
    """Required to register any model, feature, sub-strategy or filter.

    All fields are mandatory — there is no Optional escape. The embedded
    ``OutOfSamplePerformance`` is itself construction-restricted, so a
    manifest with fabricated metrics is caught by CI before runtime.
    """

    component_id: str  # domain identifier (e.g. "xgb_eurusd_m5"), not UUID
    component_type: ComponentType
    version: str  # domain identifier (e.g. "v3.2.1"), not UUID
    statistical_gain: float
    out_of_sample: OutOfSamplePerformance
    compute_cost: ComputeCost
    regime_stability: RegimeStabilityReport
    created_at: datetime
    author: str


class ProductionModule(str, Enum):
    """Whitelist of production-side targets for a ``PromotionRequest``.

    ``research``, ``sandbox`` and ``notebooks`` are deliberately absent: an
    artifact cannot be promoted "to research". Promotion has one direction.
    """

    DATA = "data"
    MICROSTRUCTURE = "microstructure"
    LIT = "lit"
    FILTERS = "filters"
    REGIME = "regime"
    META_STRATEGY = "meta_strategy"
    ADAPTIVE = "adaptive"
    FEATURES = "features"
    MODELS = "models"
    STRATEGY = "strategy"
    RISK = "risk"
    EXECUTION = "execution"


class PromotionRequest(FrozenStrictModel):
    """The ONLY mechanism by which a research / sandbox artifact reaches
    production.

    Both ``ComponentManifest`` (carrying a construction-restricted
    ``OutOfSamplePerformance``) and ``ValidationReport`` are required;
    Pydantic refuses construction otherwise, and both nested types are
    themselves construction-restricted to ``backtest/`` and
    ``validation/``. Governance is the sole gateway across the research /
    production boundary.
    """

    request_id: str  # UUID4
    requested_at: datetime
    requested_by: str
    source_path: str  # path within research/ or sandbox/
    target_module: ProductionModule
    manifest: ComponentManifest
    validation_report: ValidationReport


class DependencyViolation(FrozenStrictModel):
    """A violation of the rules declared in
    ``governance.dependency_rules``.

    Produced by ``DependencyPolicy.scan``; a non-empty scan result MUST
    cause the test suite to fail (governance blocks rather than reports).
    """

    importer_module: str
    imported_module: str
    rule_violated: str
    file_path: str
    line_number: int = Field(ge=1)


class ComplexityReport(FrozenStrictModel):
    """Snapshot of system complexity metrics, exposed by
    ``ComplexityMonitor``.
    """

    timestamp: datetime
    active_features: int = Field(ge=0)
    active_models: int = Field(ge=0)
    active_sub_strategies: int = Field(ge=0)
    max_dependency_depth: int = Field(ge=0)
    total_lines_of_code: int = Field(ge=0)
