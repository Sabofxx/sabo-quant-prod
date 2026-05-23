"""
lit/ — the LIT GATE.

Hosts implementations of:

* ``LiquidityMapper``           — maps inferred stop-liquidity zones.
* ``InducementPatternDetector`` — implements ``InducementDetector``; the
                                  ONLY place where ``InducementEvent``
                                  (with ``confirmed=True``) is constructed.
* ``RuleBasedSweepDetector``    — implements ``SweepDetector``; the ONLY
                                  place where ``SweepEvent`` is constructed.
* ``PhaseClassifier``           — phase 1 (inducement) / phase 2 (mitigation).
* ``RuleBasedStructureValidator`` — implements ``StructureValidator``;
                                  ONLY producer of ``ValidatedStructureState``.

This module is binary in its outputs. Nothing in here computes scores. May
import from ``sabo_lit.core``, ``sabo_lit.data`` and ``sabo_lit.microstructure``.

Phase 1 status:
    * ``LiquidityMapper`` — implemented.
    * ``InducementPatternDetector`` — implemented.
    * ``RuleBasedSweepDetector`` — implemented (Phase 1.4).
    * ``PhaseClassifier`` — implemented.
    * ``RuleBasedStructureValidator`` — implemented.
"""
from sabo_lit.lit.inducement_detector import (
    InducementDetectorConfig,
    InducementPatternDetector,
)
from sabo_lit.lit.liquidity_mapper import LiquidityMapper, LiquidityMapperConfig
from sabo_lit.lit.phase_classifier import PhaseClassifier, PhaseClassifierConfig
from sabo_lit.lit.structure_validator import (
    RuleBasedStructureValidator,
    StructureValidatorConfig,
)
from sabo_lit.lit.sweep_detector import (
    RuleBasedSweepDetector,
    SweepDetectorConfig,
)

__all__ = [
    "InducementDetectorConfig",
    "InducementPatternDetector",
    "LiquidityMapper",
    "LiquidityMapperConfig",
    "PhaseClassifier",
    "PhaseClassifierConfig",
    "RuleBasedStructureValidator",
    "RuleBasedSweepDetector",
    "StructureValidatorConfig",
    "SweepDetectorConfig",
]
