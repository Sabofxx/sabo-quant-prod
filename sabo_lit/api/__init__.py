"""
api/ — FastAPI: dashboard, alerts, monitoring.

Read-only over running state by default. Write endpoints (e.g. emergency
stop) are explicitly enumerated and require authentication. Surfaces:

* execution mode (``paper`` / ``live``);
* global stop-loss state and daily / weekly loss usage;
* ``ComplexityReport`` snapshots;
* active components per registry.
"""
