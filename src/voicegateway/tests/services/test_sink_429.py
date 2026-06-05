"""Phase 3, Step 2: RemoteCollectorSink treats 429 as backpressure.

A 429 is retried (honoring a clamped Retry-After) without counting toward
``max_retries``, so the in-flight batch is never dropped on rate limiting.
Non-429 errors keep the existing drop-after-max_retries behavior.
"""

from __future__ import annotations

from voicegateway.services.sinks import RemoteCollectorSink


class _Resp:
    def __init__(self, status_code: int, headers: dict | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}


class _Client:
    def __init__(self, responses: list[_Resp]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def post(self, url, json, headers):  # noqa: ANN001
        self.calls += 1
        return self._responses.pop(0) if self._responses else _Resp(200)

    async def aclose(self) -> None:
        return None


def _sink(client, sleeps: list[float], **kw) -> RemoteCollectorSink:
    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return RemoteCollectorSink(
        "http://collector",
        "vk_test",
        client=client,
        sleep=fake_sleep,
        flush_interval=None,
        **kw,
    )


async def test_429_then_200_delivers_and_honors_retry_after() -> None:
    sleeps: list[float] = []
    client = _Client([_Resp(429, {"retry-after": "5"}), _Resp(200)])
    sink = _sink(client, sleeps)
    await sink._post([{"id": "1"}])
    assert client.calls == 2
    assert sleeps == [5.0]


async def test_retry_after_clamped_to_60() -> None:
    sleeps: list[float] = []
    client = _Client([_Resp(429, {"retry-after": "120"}), _Resp(200)])
    sink = _sink(client, sleeps)
    await sink._post([{"id": "1"}])
    assert sleeps == [60.0]


async def test_429_without_header_falls_back_to_backoff() -> None:
    sleeps: list[float] = []
    client = _Client([_Resp(429), _Resp(200)])
    sink = _sink(client, sleeps, backoff=0.2)
    await sink._post([{"id": "1"}])
    assert sleeps == [0.2]


async def test_429_not_counted_toward_max_retries() -> None:
    # Three consecutive 429s with max_retries=2 would drop if counted; instead
    # the batch is delivered on the fourth call (never dropped).
    sleeps: list[float] = []
    client = _Client([_Resp(429), _Resp(429), _Resp(429), _Resp(200)])
    sink = _sink(client, sleeps, max_retries=2, backoff=0.2)
    await sink._post([{"id": "1"}])
    assert client.calls == 4


async def test_non_429_error_still_drops_after_max_retries() -> None:
    sleeps: list[float] = []
    client = _Client([_Resp(500), _Resp(500), _Resp(500), _Resp(500)])
    sink = _sink(client, sleeps, max_retries=2, backoff=0.2)
    await sink._post([{"id": "1"}])
    # attempts 0,1,2 then drop: 3 calls, backoff sleeps 0.2 and 0.4.
    assert client.calls == 3
    assert sleeps == [0.2, 0.4]
