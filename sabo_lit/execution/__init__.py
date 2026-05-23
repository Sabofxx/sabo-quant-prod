"""
execution/ — broker adapters, order management, paper and live execution.

Implementations of ``ExecutionBroker``. ``mode`` (``paper`` / ``live``) is
frozen at construction from config; no method mutates it. ``execute``
REQUIRES an ``ApprovedRiskDecision``.

Broker credentials are read from environment variables — never from YAML,
never hard-coded.
"""
