"""
risk/ — risk management. PRIORITY ABSOLUE.

Implementations of ``RiskManager``. The sole producer of
``ApprovedRiskDecision``. Exposes ``current_limits`` for live observability
of the global stop-loss and daily / weekly loss budgets.

``ExecutionBroker.execute`` REQUIRES ``ApprovedRiskDecision``: there is no
code path from a ``Signal`` to an executed order that does not pass through
this module.
"""
