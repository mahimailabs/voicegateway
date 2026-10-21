"""The flush task's retry, refusal and limits, without a session."""

from __future__ import annotations

from typing import Any

from conftest import Endpoint
from jsonschema import Draft202012Validator

from voicegateway._delivery import Delivery, rfc3339

CALL: dict[str, str | None] = {"jobId": "AJ_1", "roomName": None, "roomSid": "RM_1"}


def delivery(url: str, **kw: Any) -> Delivery:
    kw.setdefault("backoff", 0.01)
    return Delivery(url=url, token=_token, call=CALL, framework_version="1.8.8", **kw)


def _token() -> str:
    from conftest import KEY, SECRET
    from livekit import api

    return (
        api.AccessToken(KEY, SECRET)
        .with_observability_grants(api.ObservabilityGrants(write=True))
        .to_jwt()
    )


def close_event() -> tuple[str, str, dict[str, Any]]:
    return "close", rfc3339(), {"reason": None}


async def test_retry_resends_the_same_batch_id(endpoint: Endpoint) -> None:
    endpoint.statuses = [503, 429]
    d = delivery(endpoint.url)
    d.start()
    d.put(*close_event())
    await d.aclose()
    assert endpoint.attempts == 3
    assert len(endpoint.batches) == 1
    assert d.dropped == 0


async def test_4xx_drops_the_batch_without_retry(endpoint: Endpoint) -> None:
    endpoint.statuses = [400]
    d = delivery(endpoint.url)
    d.start()
    d.put(*close_event())
    await d.aclose()
    assert endpoint.attempts == 1
    assert d.dropped == 1


async def test_full_queue_drops_and_counts(endpoint: Endpoint) -> None:
    d = delivery(endpoint.url, max_queue=5, batch_size=100)
    for _ in range(8):
        d.put(*close_event())
    assert d.dropped == 3
    d.start()
    await d.aclose()
    assert len(endpoint.events()) == 5


async def test_flushes_at_batch_size_into_valid_batches(
    endpoint: Endpoint, validator: Draft202012Validator
) -> None:
    d = delivery(endpoint.url, interval=60.0)
    d.start()
    for i in range(250):
        d.put("state", rfc3339(), {"actor": "agent", "state": f"s{i % 3}"})
    await d.aclose()
    assert [len(b["events"]) for b in endpoint.batches] == [100, 100, 50]
    for batch in endpoint.batches:
        validator.validate(batch)


async def test_close_gives_up_within_the_drain_timeout(closed_url: str) -> None:
    import time

    d = delivery(closed_url, drain_timeout=0.3, backoff=1.0)
    d.start()
    d.put(*close_event())
    start = time.perf_counter()
    await d.aclose()
    assert time.perf_counter() - start < 1.0
    assert d.dropped == 1
