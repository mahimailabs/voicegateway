"""Tests for the sessions-table upsert in StorageService.log_request."""

from __future__ import annotations

import sqlite3
import time
import uuid

from voicegateway.models.request_model import RequestRecord
from voicegateway.services.storage_service import StorageService


def _read_session(db_path: str, sid: str) -> dict | None:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            """SELECT id, project, started_at, ended_at, modalities,
                      total_cost_usd, request_count
               FROM sessions WHERE id = ?""",
            (sid,),
        )
        row = cursor.fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "id": row[0],
        "project": row[1],
        "started_at": row[2],
        "ended_at": row[3],
        "modalities": row[4],
        "total_cost_usd": row[5],
        "request_count": row[6],
    }


def _make_record(
    *,
    session_id: str | None,
    modality: str = "stt",
    cost: float = 0.001,
    project: str = "tony-pizza",
    ts: float | None = None,
) -> RequestRecord:
    return RequestRecord(
        id=str(uuid.uuid4()),
        timestamp=ts if ts is not None else time.time(),
        modality=modality,
        model_id=f"deepgram/{modality}-test",
        provider="deepgram",
        project=project,
        input_units=1.0,
        cost_usd=cost,
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_first_request_inserts_session_row(tmp_path):
    db_path = str(tmp_path / "session.db")
    storage = StorageService(db_path)

    sid = "vg-first-request"
    record = _make_record(session_id=sid, modality="stt", cost=0.0043)
    await storage.log_request(record)

    row = _read_session(db_path, sid)
    assert row is not None
    assert row["id"] == sid
    assert row["project"] == "tony-pizza"
    assert row["started_at"]  # ISO string, not empty
    # ended_at is set to the request timestamp on first insert and
    # bumped on each subsequent request; on a single-request session
    # the two stamps are equal so duration is 0 rather than None.
    assert row["ended_at"] == row["started_at"]
    assert row["modalities"] == "stt"
    assert row["total_cost_usd"] == 0.0043
    assert row["request_count"] == 1


async def test_started_at_is_iso8601_utc(tmp_path):
    """started_at format is ISO 8601 with timezone — required for the"""
    db_path = str(tmp_path / "session.db")
    storage = StorageService(db_path)

    sid = "vg-iso-format"
    fixed_ts = 1746556800.0  # 2026-05-06T19:20:00Z (deterministic)
    record = _make_record(session_id=sid, ts=fixed_ts)
    await storage.log_request(record)

    row = _read_session(db_path, sid)
    assert row is not None
    started = row["started_at"]
    assert "T" in started, f"expected ISO 8601, got {started!r}"
    assert started.endswith("+00:00") or started.endswith("Z"), (
        f"expected UTC marker, got {started!r}"
    )


async def test_second_request_same_session_accumulates_cost(tmp_path):
    db_path = str(tmp_path / "session.db")
    storage = StorageService(db_path)

    sid = "vg-accum-cost"
    await storage.log_request(_make_record(session_id=sid, cost=0.01))
    await storage.log_request(_make_record(session_id=sid, cost=0.02))

    row = _read_session(db_path, sid)
    assert row is not None
    assert row["request_count"] == 2
    # Floating-point safety: store as the sum, not exact.
    assert abs(row["total_cost_usd"] - 0.03) < 1e-9


async def test_modalities_grows_without_duplicates(tmp_path):
    """Three requests across stt/llm/tts on one session: modalities"""
    db_path = str(tmp_path / "session.db")
    storage = StorageService(db_path)

    sid = "vg-modalities"
    await storage.log_request(_make_record(session_id=sid, modality="stt"))
    await storage.log_request(_make_record(session_id=sid, modality="llm"))
    await storage.log_request(_make_record(session_id=sid, modality="tts"))
    await storage.log_request(_make_record(session_id=sid, modality="stt"))  # dup

    row = _read_session(db_path, sid)
    assert row is not None
    assert row["request_count"] == 4
    parts = row["modalities"].split(",")
    assert sorted(parts) == ["llm", "stt", "tts"]


async def test_modalities_dedupe_does_not_substring_match(tmp_path):
    """Adding "tt" to a session that already has "stt" must NOT consider"""
    db_path = str(tmp_path / "session.db")
    storage = StorageService(db_path)

    sid = "vg-substring"
    # We don't actually have a "tt" modality in the real system, but
    # this test guards the implementation against future modality
    # names with overlapping substrings.
    await storage.log_request(_make_record(session_id=sid, modality="stt"))

    rec = _make_record(session_id=sid, modality="stt")
    rec.modality = "tt"  # synthetic for this test only
    await storage.log_request(rec)

    row = _read_session(db_path, sid)
    assert row is not None
    parts = row["modalities"].split(",")
    assert sorted(parts) == ["stt", "tt"]


async def test_started_at_does_not_change_on_subsequent_requests(tmp_path):
    db_path = str(tmp_path / "session.db")
    storage = StorageService(db_path)

    sid = "vg-frozen-start"
    await storage.log_request(_make_record(session_id=sid, ts=1700000000.0))
    await storage.log_request(_make_record(session_id=sid, ts=1700000060.0))

    row = _read_session(db_path, sid)
    assert row is not None
    # The first call's timestamp wins. The second call's later ts
    # would have produced a different ISO string if we'd accidentally
    # written started_at on UPDATE.
    assert "2023-11-14" in row["started_at"]  # 1700000000 ≈ 2023-11-14


async def test_no_session_row_when_session_id_is_none(tmp_path):
    """Callers that do not run inside an inference factory call (no"""
    db_path = str(tmp_path / "session.db")
    storage = StorageService(db_path)

    await storage.log_request(_make_record(session_id=None))

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT COUNT(*) FROM sessions")
        count = cursor.fetchone()[0]
    finally:
        conn.close()
    assert count == 0


async def test_two_sessions_are_independent(tmp_path):
    db_path = str(tmp_path / "session.db")
    storage = StorageService(db_path)

    await storage.log_request(
        _make_record(
            session_id="vg-sess-a", project="tony-pizza", modality="stt", cost=0.01
        )
    )
    await storage.log_request(
        _make_record(
            session_id="vg-sess-b", project="mama-diner", modality="llm", cost=0.05
        )
    )

    a = _read_session(db_path, "vg-sess-a")
    b = _read_session(db_path, "vg-sess-b")
    assert a is not None and b is not None
    assert a["project"] == "tony-pizza"
    assert b["project"] == "mama-diner"
    assert a["modalities"] == "stt"
    assert b["modalities"] == "llm"
    assert a["request_count"] == 1
    assert b["request_count"] == 1


async def test_started_at_takes_minimum_when_requests_arrive_out_of_order(tmp_path):
    """Requests are logged on completion, so a slow STT call started at"""
    db_path = str(tmp_path / "session.db")
    storage = StorageService(db_path)

    sid = "vg-out-of-order"
    # First write: ts = 1700000060 (the LLM that finished first)
    await storage.log_request(_make_record(session_id=sid, ts=1700000060.0))
    # Second write: ts = 1700000000 (the slow STT that actually started
    # 60 seconds earlier but only just finished)
    await storage.log_request(_make_record(session_id=sid, ts=1700000000.0))

    row = _read_session(db_path, sid)
    assert row is not None
    # started_at should now be the EARLIER timestamp.
    assert "2023-11-14T22:13:20" in row["started_at"], (
        f"started_at did not retreat to the minimum, got {row['started_at']!r}"
    )


async def test_zero_cost_request_still_bumps_count_and_modalities(tmp_path):
    """A request with cost_usd=0 (e.g. local provider, free tier) must"""
    db_path = str(tmp_path / "session.db")
    storage = StorageService(db_path)

    sid = "vg-zero-cost"
    await storage.log_request(_make_record(session_id=sid, cost=0.0))
    await storage.log_request(_make_record(session_id=sid, cost=0.0, modality="llm"))

    row = _read_session(db_path, sid)
    assert row is not None
    assert row["request_count"] == 2
    assert row["total_cost_usd"] == 0.0
    assert sorted(row["modalities"].split(",")) == ["llm", "stt"]
