"""``Sink.log_turns``: the seam that lets turns leave the agent process.

Binding a TurnTracker only gets turns as far as the sink. Without this the
RemoteCollectorSink had nowhere to put them, so a collector-mode agent captured
turns and dropped them, which is the co-located-only limitation
``/v1/rooms/{room}/latency`` was built to escape.
"""

from __future__ import annotations

from typing import Any

from voicegateway.middleware.dead_air_detector_middleware import DeadAirEvent
from voicegateway.middleware.turn_tracker_middleware import TurnRow
from voicegateway.services.sinks import LocalSqliteSink, RemoteCollectorSink
from voicegateway.services.storage_service import StorageService


class _Resp:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {}


class _Client:
    """Records every POST so the URL a batch went to can be asserted."""

    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.posts: list[tuple[str, Any]] = []

    async def post(self, url: str, json: Any = None, headers: Any = None) -> _Resp:
        self.posts.append((url, json))
        return _Resp(self.status_code)

    async def aclose(self) -> None:
        return None


def _turn(index: int, session_id: str = "s-1") -> TurnRow:
    return TurnRow(
        session_id=session_id,
        turn_index=index,
        caller_speak_start_ms=1000 + index,
        caller_speak_end_ms=1500 + index,
        agent_speak_start_ms=1900 + index,
        agent_speak_end_ms=3000 + index,
        response_speed_ms=400,
    )


async def test_local_sink_writes_turns_through_storage(tmp_path) -> None:
    from voicegateway.repository import turns_repository as turns

    storage = StorageService(str(tmp_path / "t.db"))
    sink = LocalSqliteSink(storage)

    await sink.log_turns([_turn(0), _turn(1)])

    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        rows = await turns.list_turns_by_session(db, "s-1")
    assert len(rows) == 2
    await storage.aclose()


async def test_remote_sink_posts_turns_to_their_own_route() -> None:
    """Not /v1/ingest. That handler would count them malformed and drop them."""
    client = _Client()
    sink = RemoteCollectorSink(
        "http://collector", "k", batch_size=2, flush_interval=None, client=client
    )

    await sink.log_turns([_turn(0), _turn(1)])

    assert len(client.posts) == 1
    url, body = client.posts[0]
    assert url == "http://collector/v1/ingest/turns"
    assert [row["turn_index"] for row in body] == [0, 1]


async def test_turns_do_not_leave_until_the_batch_fills_or_a_flush() -> None:
    client = _Client()
    sink = RemoteCollectorSink(
        "http://collector", "k", batch_size=10, flush_interval=None, client=client
    )

    await sink.log_turns([_turn(0)])
    assert client.posts == [], "a single turn should still be batching"

    await sink.flush()
    assert len(client.posts) == 1
    assert client.posts[0][0].endswith("/v1/ingest/turns")


async def test_turns_and_requests_go_to_different_routes() -> None:
    """One flush, two destinations. Mixing them would drop the turns."""
    from voicegateway.models.request_model import RequestRecord

    client = _Client()
    sink = RemoteCollectorSink(
        "http://collector", "k", batch_size=50, flush_interval=None, client=client
    )

    await sink.log_request(
        RequestRecord(
            id="r1",
            timestamp=1.0,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
        )
    )
    await sink.log_turns([_turn(0)])
    await sink.flush()

    urls = sorted(url for url, _ in client.posts)
    assert urls == ["http://collector/v1/ingest", "http://collector/v1/ingest/turns"]


async def test_a_failed_turn_post_is_requeued_not_dropped() -> None:
    """A collector blip must not cost the turns; the next flush retries."""
    client = _Client(status_code=500)
    sink = RemoteCollectorSink(
        "http://collector",
        "k",
        batch_size=1,
        flush_interval=None,
        max_retries=0,
        backoff=0,
        client=client,
    )

    await sink.log_turns([_turn(0)])
    assert len(client.posts) == 1  # tried once, failed

    client.status_code = 200
    await sink.flush()
    assert len(client.posts) == 2, "the failed batch was not retried"
    assert client.posts[1][1][0]["turn_index"] == 0


async def test_aclose_flushes_pending_turns() -> None:
    """A graceful shutdown must not silently lose the last partial batch."""
    client = _Client()
    sink = RemoteCollectorSink(
        "http://collector", "k", batch_size=100, flush_interval=None, client=client
    )

    await sink.log_turns([_turn(0)])
    assert client.posts == []

    await sink.aclose()
    assert len(client.posts) == 1
    assert client.posts[0][0].endswith("/v1/ingest/turns")


async def test_an_empty_batch_makes_no_request() -> None:
    client = _Client()
    sink = RemoteCollectorSink(
        "http://collector", "k", flush_interval=None, client=client
    )
    await sink.log_turns([])
    await sink.flush()
    assert client.posts == []


async def test_a_full_ingest_url_does_not_double_up_the_turns_path() -> None:
    """``_normalize_ingest_url`` accepts a base host or a full ingest URL, so
    the turns route has to be derived from the normalized value, not appended
    to whatever the user typed."""
    client = _Client()
    sink = RemoteCollectorSink(
        "http://collector/v1/ingest",
        "k",
        batch_size=1,
        flush_interval=None,
        client=client,
    )
    await sink.log_turns([_turn(0)])
    assert client.posts[0][0] == "http://collector/v1/ingest/turns"



# --- dead air ------------------------------------------------------------


def _event(session_id: str = "s-1") -> DeadAirEvent:
    return DeadAirEvent(
        session_id=session_id,
        started_at_ms=10_000,
        duration_ms=4200,
        threshold_used_ms=3000,
    )


async def test_local_sink_writes_dead_air_through_storage(tmp_path) -> None:
    from voicegateway.repository import dead_air_repository as dead_air

    storage = StorageService(str(tmp_path / "da.db"))
    sink = LocalSqliteSink(storage)

    await sink.log_dead_air([_event()])

    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        rows = await dead_air.list_events_by_session(db, "s-1")
    assert len(rows) == 1
    await storage.aclose()


async def test_remote_sink_posts_dead_air_to_its_own_route() -> None:
    client = _Client()
    sink = RemoteCollectorSink(
        "http://collector", "k", batch_size=1, flush_interval=None, client=client
    )

    await sink.log_dead_air([_event()])

    assert len(client.posts) == 1
    url, body = client.posts[0]
    assert url == "http://collector/v1/ingest/dead-air"
    assert body[0]["duration_ms"] == 4200


async def test_dead_air_and_turns_go_to_different_routes() -> None:
    """Three destinations off one flush, none of them mixed."""
    client = _Client()
    sink = RemoteCollectorSink(
        "http://collector", "k", batch_size=50, flush_interval=None, client=client
    )

    await sink.log_turns([_turn(0)])
    await sink.log_dead_air([_event()])
    await sink.flush()

    urls = sorted(url for url, _ in client.posts)
    assert urls == [
        "http://collector/v1/ingest/dead-air",
        "http://collector/v1/ingest/turns",
    ]


async def test_a_failed_dead_air_post_is_requeued_not_dropped() -> None:
    client = _Client(status_code=500)
    sink = RemoteCollectorSink(
        "http://collector",
        "k",
        batch_size=1,
        flush_interval=None,
        max_retries=0,
        backoff=0,
        client=client,
    )

    await sink.log_dead_air([_event()])
    assert len(client.posts) == 1

    client.status_code = 200
    await sink.flush()
    assert len(client.posts) == 2, "the failed batch was not retried"


async def test_aclose_flushes_pending_dead_air() -> None:
    client = _Client()
    sink = RemoteCollectorSink(
        "http://collector", "k", batch_size=100, flush_interval=None, client=client
    )
    await sink.log_dead_air([_event()])
    assert client.posts == []
    await sink.aclose()
    assert len(client.posts) == 1
    assert client.posts[0][0].endswith("/v1/ingest/dead-air")


async def test_an_empty_dead_air_batch_makes_no_request() -> None:
    client = _Client()
    sink = RemoteCollectorSink(
        "http://collector", "k", flush_interval=None, client=client
    )
    await sink.log_dead_air([])
    await sink.flush()
    assert client.posts == []
