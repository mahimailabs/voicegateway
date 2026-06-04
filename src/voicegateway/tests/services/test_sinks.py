"""Tests for voicegateway/services/sinks.py (the write-sink seam)."""

from __future__ import annotations

import time
import uuid

from voicegateway.models.request_model import RequestRecord
from voicegateway.services.sinks import LocalSqliteSink
from voicegateway.services.storage_service import StorageService


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
