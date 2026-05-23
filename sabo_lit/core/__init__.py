"""
core/ — shared types, interfaces and event bus.

This is the ONLY module that defines cross-module contracts. Any other
module that needs to talk to another module imports from ``sabo_lit.core``.

CANONICAL IMPORT FORM (see CONVENTIONS.md §14):

    from sabo_lit.core import ValidatedStructureState, Strategy, ...

Every public symbol from ``core/schemas.py``, ``core/interfaces.py`` and
``core/events.py`` is re-exported below and listed in ``__all__``. Sub-agents
MUST use this form rather than reaching into submodules directly
(``from sabo_lit.core.schemas import …``) — the canonical form keeps a single
review surface when a contract changes.

Modifying anything in this package changes the system's central contract and
must be reviewed accordingly. See CONVENTIONS.md.
"""
from __future__ import annotations

from .events import EventBus
from .interfaces import (
    BacktestEngine,
    BrokerRejectionError,
    ComplexityMonitor,
    ComponentRegistry,
    ConfidenceEngine,
    DataFeedInterruptionError,
    DataProvider,
    DependencyPolicy,
    ExecutionBroker,
    ExecutionFailureError,
    FeaturePipeline,
    InducementDetector,
    LiquidityMapper,
    MicrostructureProvider,
    PhaseClassifier,
    PositionManager,
    ProbabilisticArbiter,
    RegimeClassifier,
    RiskManager,
    SaboLitError,
    SecondaryFilter,
    SignalModel,
    Strategy,
    StrategyContext,
    StrategySelector,
    StructureValidator,
    SubStrategy,
    SweepDetector,
    ValidationSuite,
)
from .schemas import (
    TRADE_CLOSED_TOPIC,
    ApprovedRiskDecision,
    BrokerRejection,
    Candle,
    ComplexityReport,
    ComponentManifest,
    ComponentType,
    ComputeCost,
    ConfidenceState,
    DataFeedInterruption,
    DependencyViolation,
    ExecutionFailure,
    ExitDecision,
    ExitReason,
    FeatureVector,
    FilterResult,
    FrozenStrictModel,
    InducementEvent,
    LiquidityRegimeLabel,
    LiquidityZone,
    LiquidityZoneKind,
    MacroRegimeLabel,
    MarketPhase,
    MarketRegime,
    MonteCarloResult,
    Order,
    OrderFlowSnapshot,
    OrderKind,
    OrderSide,
    OutOfSamplePerformance,
    OverfittingReport,
    PhaseKind,
    Position,
    PositionStatus,
    PositionUpdate,
    ProductionModule,
    PromotionRequest,
    RegimeStabilityReport,
    RiskDecision,
    SessionLabel,
    Signal,
    SignalModelOutput,
    StructureState,
    SubStrategyDecision,
    SubStrategyKind,
    SweepEvent,
    TradeClosedEvent,
    ValidatedStructureState,
    ValidationReport,
    VolatilityRegimeLabel,
    WalkForwardResult,
)

__all__ = [
    # ---- Event bus -----------------------------------------------------
    "EventBus",
    # ---- Topic constants ----------------------------------------------
    "TRADE_CLOSED_TOPIC",
    # ---- Interfaces : data --------------------------------------------
    "DataProvider",
    "MicrostructureProvider",
    # ---- Interfaces : LIT ---------------------------------------------
    "LiquidityMapper",
    "InducementDetector",
    "SweepDetector",
    "PhaseClassifier",
    "StructureValidator",
    # ---- Interfaces : filters / regime --------------------------------
    "SecondaryFilter",
    "RegimeClassifier",
    # ---- Interfaces : strategy / models -------------------------------
    "SubStrategy",
    "StrategySelector",
    "ConfidenceEngine",
    "FeaturePipeline",
    "SignalModel",
    "ProbabilisticArbiter",
    "StrategyContext",
    "Strategy",
    # ---- Interfaces : risk / execution / lifecycle -------------------
    "RiskManager",
    "ExecutionBroker",
    "PositionManager",
    # ---- Interfaces : backtest / validation --------------------------
    "BacktestEngine",
    "ValidationSuite",
    # ---- Interfaces : governance --------------------------------------
    "ComponentRegistry",
    "ComplexityMonitor",
    "DependencyPolicy",
    # ---- Exceptions ---------------------------------------------------
    "SaboLitError",
    "BrokerRejectionError",
    "ExecutionFailureError",
    "DataFeedInterruptionError",
    # ---- Schemas : base -----------------------------------------------
    "FrozenStrictModel",
    # ---- Schemas : market data ----------------------------------------
    "Candle",
    "OrderFlowSnapshot",
    # ---- Schemas : LIT primitives -------------------------------------
    "LiquidityZone",
    "LiquidityZoneKind",
    "InducementEvent",
    "SweepEvent",
    "MarketPhase",
    "PhaseKind",
    "StructureState",
    "ValidatedStructureState",
    # ---- Schemas : filters --------------------------------------------
    "FilterResult",
    # ---- Schemas : regime ---------------------------------------------
    "MarketRegime",
    "VolatilityRegimeLabel",
    "LiquidityRegimeLabel",
    "SessionLabel",
    "MacroRegimeLabel",
    # ---- Schemas : sub-strategies -------------------------------------
    "SubStrategyKind",
    "SubStrategyDecision",
    # ---- Schemas : adaptive -------------------------------------------
    "ConfidenceState",
    # ---- Schemas : features / models ---------------------------------
    "FeatureVector",
    "SignalModelOutput",
    # ---- Schemas : signal ---------------------------------------------
    "Signal",
    # ---- Schemas : risk -----------------------------------------------
    "RiskDecision",
    "ApprovedRiskDecision",
    # ---- Schemas : execution ------------------------------------------
    "Order",
    "OrderKind",
    "OrderSide",
    "Position",
    "PositionStatus",
    # ---- Schemas : position management --------------------------------
    "ExitReason",
    "ExitDecision",
    "PositionUpdate",
    # ---- Schemas : bus events -----------------------------------------
    "TradeClosedEvent",
    # ---- Schemas : typed failure payloads -----------------------------
    "BrokerRejection",
    "ExecutionFailure",
    "DataFeedInterruption",
    # ---- Schemas : validation -----------------------------------------
    "WalkForwardResult",
    "MonteCarloResult",
    "RegimeStabilityReport",
    "OverfittingReport",
    "ValidationReport",
    # ---- Schemas : governance -----------------------------------------
    "ComponentType",
    "ComputeCost",
    "OutOfSamplePerformance",
    "ComponentManifest",
    "ProductionModule",
    "PromotionRequest",
    "DependencyViolation",
    "ComplexityReport",
]
