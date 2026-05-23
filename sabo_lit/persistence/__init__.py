"""
persistence/ — PostgreSQL + Redis adapters.

Stores trades, signals, manifests, validation reports, promotion requests
and experiment metadata in Postgres. Uses Redis for cache and as the dev
event-bus transport.

URLs come from environment variables (``DATABASE_URL``, ``REDIS_URL``).
"""
