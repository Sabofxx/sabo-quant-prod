"""
Confidence engine — rolling performance windows, dynamic weighting,
probabilistic decay, online adaptation.

Implements ``ConfidenceEngine``. State is per-(sub_strategy, symbol). The
engine updates after each realised PnL and exposes a current
``ConfidenceState`` to the selector and arbiter.

Phase 0: placeholder. Implementation deferred to Phase 3.
"""
