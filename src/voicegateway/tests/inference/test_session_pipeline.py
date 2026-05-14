"""Tests for the session_id pipeline: ContextVar to requests.session_id."""

from __future__ import annotations

import contextvars
import sqlite3
import time
import uuid
from typing import Any

import pytest

from voicegateway.inference.session.context import (
    get_or_create_session_id,
    reset_session_id,
)
from voicegateway.middleware.cost_tracker import CostTracker
from voicegateway.middleware.instrumented_provider import wrap_provider
from voicegateway.models.request_model import RequestRecord
from voicegateway.services.storage_service import StorageService

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeWrapped:
    """A bare provider-side instance the wrapper can proxy to."""

    def __init__(self, model: str = "test-model") -> None:
        from livekit.agents.stt import STTCapabilities

        self.capabilities = STTCapabilities(streaming=False, interim_results=False)
        self.model = model

    def on(self, event: str, callback: Any) -> None:
        pass

    def aclose(self) -> None:  # pragma: no cover (proxy target)
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_session_state():
    reset_session_id()
    yield
    reset_session_id()


@pytest.fixture
async def storage(tmp_path):
    return StorageService(str(tmp_path / "session.db"))


@pytest.fixture
def cost_tracker(storage):
    return CostTracker(storage=storage)


# ---------------------------------------------------------------------------
# RequestRecord shape
# ---------------------------------------------------------------------------


def test_request_record_has_session_id_field():
    rec = RequestRecord(
        id="r1",
        timestamp=time.time(),
        modality="stt",
        model_id="deepgram/nova-3",
        provider="deepgram",
    )
    # Default is None — pre-v0.0.5 callers don't have to update.
    assert rec.session_id is None


def test_request_record_accepts_session_id():
    rec = RequestRecord(
        id="r1",
        timestamp=time.time(),
        modality="stt",
        model_id="deepgram/nova-3",
        provider="deepgram",
        session_id="vg-test",
    )
    assert rec.session_id == "vg-test"


# ---------------------------------------------------------------------------
# CostTracker
# ---------------------------------------------------------------------------


def test_cost_tracker_threads_session_id_into_record(cost_tracker):
    rec = cost_tracker.create_record(
        model_id="deepgram/nova-3",
        modality="stt",
        provider="deepgram",
        session_id="vg-track-test",
    )
    assert rec.session_id == "vg-track-test"


def test_cost_tracker_session_id_defaults_to_none(cost_tracker):
    rec = cost_tracker.create_record(
        model_id="deepgram/nova-3",
        modality="stt",
        provider="deepgram",
    )
    assert rec.session_id is None


# ---------------------------------------------------------------------------
# Storage round-trip
# ---------------------------------------------------------------------------


async def test_storage_persists_session_id(tmp_path):
    db_path = str(tmp_path / "store.db")
    storage = StorageService(db_path)

    rec = RequestRecord(
        id=str(uuid.uuid4()),
        timestamp=time.time(),
        modality="llm",
        model_id="openai/gpt-4o-mini",
        provider="openai",
        session_id="vg-persist-test",
    )
    await storage.log_request(rec)

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT session_id FROM requests WHERE id = ?", (rec.id,))
        row = cursor.fetchone()
    finally:
        conn.close()
    assert row == ("vg-persist-test",)


async def test_storage_persists_null_session_id_for_uninstrumented_path(tmp_path):
    """Callers that do not set session_id (e.g. direct"""
    db_path = str(tmp_path / "store.db")
    storage = StorageService(db_path)

    rec = RequestRecord(
        id=str(uuid.uuid4()),
        timestamp=time.time(),
        modality="llm",
        model_id="openai/gpt-4o-mini",
        provider="openai",
    )  # session_id omitted on purpose
    await storage.log_request(rec)

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT session_id FROM requests WHERE id = ?", (rec.id,))
        row = cursor.fetchone()
    finally:
        conn.close()
    assert row == (None,)


# ---------------------------------------------------------------------------
# Instrumented wrapper end-to-end
# ---------------------------------------------------------------------------


async def _exercise_wrapper(cost_tracker: CostTracker, storage: StorageService) -> Any:
    """Construct a wrapped instance and drive its _log_request path."""
    wrapped = wrap_provider(
        instance=_FakeWrapped("nova-3"),
        modality="stt",
        model_id="deepgram/nova-3",
        provider="deepgram",
        project="default",
        cost_tracker=cost_tracker,
        storage=storage,
    )
    # _log_request is the seam where session_id is read; drive it
    # directly here so the test does not depend on a real LK plugin.
    await wrapped._log_request(input_units=1.0)
    return wrapped


async def test_wrapper_writes_session_id_when_context_has_one(tmp_path):
    db_path = str(tmp_path / "session.db")
    storage = StorageService(db_path)
    cost_tracker = CostTracker(storage=storage)

    sid_holder = {}

    async def _scenario():
        sid_holder["sid"] = get_or_create_session_id()
        await _exercise_wrapper(cost_tracker, storage)

    ctx = contextvars.copy_context()

    # contextvars.Context.run only accepts sync callables, so wrap the
    # coroutine: build it inside the context, then await outside.
    coro_holder: dict[str, Any] = {}

    def _make_coro():
        coro_holder["coro"] = _scenario()

    ctx.run(_make_coro)
    await coro_holder["coro"]

    rows = await storage.get_recent_requests(limit=10)
    assert len(rows) == 1
    assert rows[0]["session_id"] == sid_holder["sid"]


async def test_wrapper_writes_null_session_id_when_no_context(tmp_path):
    """Outside an inference factory call, get_session_id is None and"""
    db_path = str(tmp_path / "session.db")
    storage = StorageService(db_path)
    cost_tracker = CostTracker(storage=storage)

    # No get_or_create_session_id() in this flow — simulates direct
    # wrapper construction outside an inference factory call.
    await _exercise_wrapper(cost_tracker, storage)

    rows = await storage.get_recent_requests(limit=10)
    assert len(rows) == 1
    assert rows[0]["session_id"] is None


async def test_wrapper_reads_session_id_at_request_time_not_construction(
    tmp_path,
):
    """The session ID is captured when _log_request runs, not when the"""
    db_path = str(tmp_path / "session.db")
    storage = StorageService(db_path)
    cost_tracker = CostTracker(storage=storage)

    # Build the wrapper FIRST, with no session context active.
    wrapped = wrap_provider(
        instance=_FakeWrapped("nova-3"),
        modality="stt",
        model_id="deepgram/nova-3",
        provider="deepgram",
        project="default",
        cost_tracker=cost_tracker,
        storage=storage,
    )
    # NOW open a session context and drive the request.
    sid = get_or_create_session_id()
    await wrapped._log_request(input_units=1.0)

    rows = await storage.get_recent_requests(limit=10)
    assert len(rows) == 1
    assert rows[0]["session_id"] == sid
