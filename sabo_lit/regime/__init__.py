"""
regime/ — Regime Engine.

Composite ``RegimeClassifier`` plus per-axis classifiers (volatility,
liquidity, session, macro). Outputs ``MarketRegime`` only.

Regime informs sub-strategy selection and confidence weighting; it does
NOT control the LIT gate.
"""
