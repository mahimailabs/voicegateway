"""Tests for voicegateway/services/sinks.py (the write-sink seam)."""

from __future__ import annotations

import time
import uuid

from voicegateway.models.request_model import RequestRecord
from voicegateway.services.sinks import LocalSqliteSink, RemoteCollectorSink
from voicegateway.services.storage_service import StorageService


class _FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


class _RecordingClient:
    """Stand-in for httpx.AsyncClient that records POST calls."""

    def __init__(self, status_code: int = 200) -> None:
        self.calls: list[dict] = []
        self._status = status_code
        self.closed = False

    async def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse(self._status)

    async def aclose(self) -> None:
        self.closed = True


def _record(**overrides) -> RequestRecord:
    base: dict = {
        "id": str(uuid.uuid4()),
        "timestamp": time.time(),
        "modality": "llm",
        "model_id": "openai/gpt-4o-mini",
        "provider": "openai",
        "project": "fleet",
        "cost_usd": 0.01,
        "agent_id": "agent-7",
    }
    base.update(overrides)
    return RequestRecord(**base)


def test_remote_sink_url_base_gets_ingest_path():
    sink = RemoteCollectorSink("https://collector.example.com", "k")
    assert sink._ingest_url == "https://collector.example.com/v1/ingest"


def test_remote_sink_url_trailing_slash_normalized():
    sink = RemoteCollectorSink("https://collector.example.com/", "k")
    assert sink._ingest_url == "https://collector.example.com/v1/ingest"


def test_remote_sink_url_full_ingest_path_not_doubled():
    """Docs long told users to include /v1/ingest; accept it without doubling.

    Regression: url + "/v1/ingest" produced ".../v1/ingest/v1/ingest" -> 404,
    silently breaking fleet ingest for anyone who followed the docs.
    """
    sink = RemoteCollectorSink("https://collector.example.com/v1/ingest", "k")
    assert sink._ingest_url == "https://collector.example.com/v1/ingest"


def test_remote_sink_url_full_ingest_path_trailing_slash():
    sink = RemoteCollectorSink("https://collector.example.com/v1/ingest/", "k")
    assert sink._ingest_url == "https://collector.example.com/v1/ingest"


async def test_local_sqlite_sink_round_trips_record(tmp_path):
    """LocalSqliteSink.log_request writes through to the wrapped storage."""
    storage = StorageService(str(tmp_path / "sink.db"))
    sink = LocalSqliteSink(storage)
    await sink.log_request(_record())
    rows = await storage.get_recent_requests(limit=10)
    assert len(rows) == 1
    assert rows[0]["agent_id"] == "agent-7"


async def test_local_sqlite_sink_flush_is_noop(tmp_path):
    """flush() succeeds without touching storage (SQLite commits per write)."""
    storage = StorageService(str(tmp_path / "sink.db"))
    sink = LocalSqliteSink(storage)
    await sink.flush()  # must not raise


async def test_local_sqlite_sink_finalize_delegates(tmp_path):
    """finalize_* hooks delegate so CostTracker.close_session still resolves."""
    storage = StorageService(str(tmp_path / "sink.db"))
    sink = LocalSqliteSink(storage)
    rec = _record(session_id="sess-1")
    await sink.log_request(rec)
    # Delegated finalize must run without error on a real session row.
    await sink.finalize_session_metrics("sess-1")
    await sink.finalize_session_replay("sess-1")


# --- RemoteCollectorSink -------------------------------------------------


async def test_remote_sink_batches_until_size_then_posts():
    client = _RecordingClient()
    sink = RemoteCollectorSink(
        "http://collector", "vk_abc", batch_size=2, flush_interval=None, client=client
    )
    await sink.log_request(_record(id="r1"))
    assert client.calls == []  # buffered, not yet at batch_size
    await sink.log_request(_record(id="r2"))
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"] == "http://collector/v1/ingest"
    assert call["headers"]["Authorization"] == "Bearer vk_abc"
    assert [r["id"] for r in call["json"]] == ["r1", "r2"]
    assert call["json"][0]["agent_id"] == "agent-7"


async def test_remote_sink_flush_sends_partial_batch():
    client = _RecordingClient()
    sink = RemoteCollectorSink(
        "http://collector/", "vk_x", batch_size=10, flush_interval=None, client=client
    )
    await sink.log_request(_record(id="r1"))
    assert client.calls == []
    await sink.flush()
    assert len(client.calls) == 1
    assert [r["id"] for r in client.calls[0]["json"]] == ["r1"]


async def test_remote_sink_never_raises_on_error_status():
    client = _RecordingClient(status_code=500)
    sink = RemoteCollectorSink(
        "http://collector",
        "vk_x",
        batch_size=1,
        flush_interval=None,
        max_retries=0,
        client=client,
    )
    # batch_size=1 -> flush on first append; a 500 must be swallowed.
    await sink.log_request(_record(id="r1"))
    assert len(client.calls) == 1


async def test_remote_sink_aclose_flushes_without_closing_injected_client():
    client = _RecordingClient()
    sink = RemoteCollectorSink(
        "http://c", "vk", batch_size=10, flush_interval=None, client=client
    )
    await sink.log_request(_record(id="r1"))
    await sink.aclose()
    assert len(client.calls) == 1  # flushed on close
    assert client.closed is False  # injected client is not owned -> not closed


async def test_remote_sink_drops_oldest_past_max_buffer():
    client = _RecordingClient()
    sink = RemoteCollectorSink(
        "http://c",
        "vk",
        batch_size=100,  # never auto-flushes during this test
        flush_interval=None,
        max_buffer=2,
        client=client,
    )
    await sink.log_request(_record(id="r1"))
    await sink.log_request(_record(id="r2"))
    await sink.log_request(_record(id="r3"))  # overflow -> drop oldest (r1)
    await sink.flush()
    assert len(client.calls) == 1
    assert [r["id"] for r in client.calls[0]["json"]] == ["r2", "r3"]


async def test_remote_sink_omits_auth_header_without_key():
    client = _RecordingClient()
    sink = RemoteCollectorSink(
        "http://c", None, batch_size=1, flush_interval=None, client=client
    )
    await sink.log_request(_record(id="r1"))
    assert "Authorization" not in client.calls[0]["headers"]


class _FlakyClient:
    """Returns the next queued status code per POST, then 200 once drained."""

    def __init__(self, statuses: list[int]) -> None:
        self.statuses = list(statuses)
        self.calls = 0

    async def post(self, url, json=None, headers=None):
        self.calls += 1
        code = self.statuses.pop(0) if self.statuses else 200
        return _FakeResponse(code)

    async def aclose(self) -> None:
        pass


async def test_remote_sink_retries_then_succeeds():
    client = _FlakyClient([500, 200])  # fail once, then succeed
    sink = RemoteCollectorSink(
        "http://c",
        "vk",
        batch_size=1,
        flush_interval=None,
        max_retries=2,
        backoff=0.001,
        client=client,
    )
    await sink.log_request(_record(id="r1"))  # flush -> 500 -> retry -> 200
    assert client.calls == 2


async def test_remote_sink_creates_default_httpx_client():
    import httpx

    sink = RemoteCollectorSink("http://c", "vk", flush_interval=None)
    created = sink._ensure_client()
    assert isinstance(created, httpx.AsyncClient)
    await created.aclose()


async def test_remote_sink_starts_periodic_flusher_and_aclose_cancels_it():
    client = _RecordingClient()
    sink = RemoteCollectorSink(
        "http://c", "vk", batch_size=100, flush_interval=0.01, client=client
    )
    await sink.log_request(_record(id="r1"))  # never hits batch_size
    assert sink._flusher is not None  # periodic flusher started
    await sink.aclose()  # cancels the flusher and flushes the buffer
    assert len(client.calls) >= 1
