"""
meta_strategy/ — specialised sub-strategies plus the selector.

Each sub-strategy implements ``SubStrategy`` and consumes a
``ValidatedStructureState`` + ``MarketRegime``. The selector implements
``StrategySelector`` and chooses among them based on regime and confidence.

Sub-strategies CANNOT emit ``Signal`` directly — that is the arbiter's role.
"""
