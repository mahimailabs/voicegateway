"""EventBus / Subscription: fan-out delivery and drop-to-resync overflow."""

from __future__ import annotations

import asyncio

from voicegateway.server.api.openorca.bus import EventBus


async def test_subscriber_receives_published_event() -> None:
    bus = EventBus()
    sub = bus.subscribe()

    await bus.publish({"type": "hello"})

    event = await asyncio.wait_for(sub.get(), timeout=1.0)
    assert event == {"type": "hello"}
    await sub.aclose()


async def test_overflowing_small_queue_yields_resync() -> None:
    bus = EventBus(maxsize=2)
    sub = bus.subscribe()

    # Fill to capacity (a, b) then overflow (c): the backlog is dropped and
    # replaced by a single __resync__ marker.
    await bus.publish({"type": "a"})
    await bus.publish({"type": "b"})
    await bus.publish({"type": "c"})

    event = await asyncio.wait_for(sub.get(), timeout=1.0)
    assert event == {"type": "__resync__"}
    await sub.aclose()


async def test_closed_subscriber_is_dropped_on_publish() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    await sub.aclose()

    # Publishing to a closed subscriber must not raise and must prune it.
    await bus.publish({"type": "ignored"})
    assert sub.closed is True


async def test_unsubscribe_evicts_and_stops_delivery() -> None:
    bus = EventBus()
    sub = bus.subscribe()

    bus.unsubscribe(sub)

    # The subscriber is gone from the fan-out set and gets no further events.
    assert sub not in bus._subs
    await bus.publish({"type": "after-unsub"})
    assert sub._queue.empty()
