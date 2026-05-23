"""
strategy/ — composition of LIT gate + probabilistic arbiter into a Strategy.

Implementations of ``Strategy``. A Strategy wires the canonical pipeline
documented in ``core.interfaces.Strategy``. It is the only place that
combines the gate output with the arbiter output to emit a ``Signal``.

Implementations of ``ProbabilisticArbiter`` also live here; they consume a
``ValidatedStructureState`` and may only REJECT or WEIGHT.
"""
