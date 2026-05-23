"""
Event bus interface for cross-module communication.

This is an INTERFACE only. The concrete implementation (in-memory for tests,
Redis Streams in dev/paper, NATS or similar in live) is selected from config.

Why a typed bus rather than free-form messaging:

* Each topic carries exactly one Pydantic event type (enforced by ``T``),
  preventing the silent drift of payload shapes between publisher and
  consumer.
* Cross-module communication MUST go through this bus or through a typed
  function call — never through ``dict`` blobs or untyped messages. This
  is a CONVENTIONS.md rule and a precondition for the dependency policy
  scanner to reason about module coupling.

Topic / event bindings
----------------------
Topic name constants live in ``core/schemas.py`` next to the event schema
they carry — e.g. ``TRADE_CLOSED_TOPIC`` and ``TradeClosedEvent`` are
defined together. Producers and subscribers MUST import the constant
rather than reusing a string literal: a stray string typo would route
silently to nowhere.

The known typed channels in Phase 0 are:

* ``TRADE_CLOSED_TOPIC`` carrying ``TradeClosedEvent`` — published by
  ``ExecutionBroker.close``, consumed by ``RiskManager`` (and any other
  interested subscriber such as the dashboard or the audit log).

Phases that need new channels add the topic constant + the event schema
side by side in ``core/schemas.py``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class EventBus(ABC, Generic[T]):
    """Typed publish / subscribe event bus.

    A single bus instance is bound to a single Pydantic event type ``T``.
    Subscribers receive validated events; publishers cannot inject non-
    Pydantic payloads.
    """

    @abstractmethod
    async def publish(self, topic: str, event: T) -> None:
        """Publish ``event`` on ``topic``. Implementations must persist or
        forward the event according to their configured delivery semantics.

        Callers SHALL pass a topic constant imported from
        ``core/schemas.py`` rather than a string literal.
        """
        ...

    @abstractmethod
    async def subscribe(self, topic: str) -> AsyncIterator[T]:
        """Async iterator over events received on ``topic``."""
        ...

    @abstractmethod
    def on(
        self, topic: str, handler: Callable[[T], Awaitable[None]]
    ) -> None:
        """Register an awaitable handler for ``topic``. Handlers are invoked
        in arrival order; back-pressure semantics are implementation defined.
        """
        ...
