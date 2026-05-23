"""
microstructure/ — order flow, book imbalance, volume profile, delta / CVD.

Implementations of ``MicrostructureProvider``. Outputs are
``OrderFlowSnapshot`` only — never raw exchange messages. May import from
``sabo_lit.core`` and from ``sabo_lit.data`` (for typed candle context).
"""
