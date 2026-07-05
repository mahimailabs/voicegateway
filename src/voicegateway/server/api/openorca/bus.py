"""A tiny in-process pub/sub bus for OpenOrca dashboard SSE fan-out.

Each connected ``/openorca/events`` stream owns one :class:`Subscription`
backed by a bounded queue. Producers (the heartbeat endpoint, intervention
resolves) call :meth:`EventBus.publish`, which offers the event to every live
subscription. A subscription that falls behind (its queue fills) is not allowed
to block a producer or grow without bound: on overflow its backlog is dropped
and replaced by a single ``{"type": "__resync__"}`` marker, which the stream
turns into a full snapshot so the client recovers exactly-once rather than
replaying a partial, out-of-order backlog.
"""

from __future__ import annotations

import asyncio
from typing import Any

_RESYNC: dict[str, Any] = {"type": "__resync__"}


class Subscription:
    """One consumer's bounded event queue with drop-to-resync overflow."""

    def __init__(self, maxsize: int) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def _offer(self, event: dict[str, Any]) -> None:
        """Enqueue ``event``; on a full queue drain it and enqueue a resync."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - defensive
                    break
            self._queue.put_nowait(dict(_RESYNC))

    async def get(self) -> dict[str, Any]:
        """Await the next event for this subscriber."""
        return await self._queue.get()

    async def aclose(self) -> None:
        """Mark this subscription closed so the bus stops delivering to it."""
        self._closed = True


class EventBus:
    """Fan-out hub: publish to every live :class:`Subscription`."""

    def __init__(self, maxsize: int = 256) -> None:
        self._maxsize = maxsize
        self._subs: set[Subscription] = set()

    def subscribe(self) -> Subscription:
        """Register and return a fresh subscription."""
        sub = Subscription(self._maxsize)
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        """Evict ``sub`` from the fan-out set so publishes stop reaching it."""
        self._subs.discard(sub)

    async def publish(self, event: dict[str, Any]) -> None:
        """Offer ``event`` to every non-closed subscriber; drop closed ones."""
        dead: list[Subscription] = []
        for sub in list(self._subs):
            if sub.closed:
                dead.append(sub)
                continue
            sub._offer(event)
        for sub in dead:
            self._subs.discard(sub)


__all__ = ["EventBus", "Subscription"]
