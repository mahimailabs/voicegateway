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
