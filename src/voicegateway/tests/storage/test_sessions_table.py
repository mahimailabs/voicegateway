"""Tests for the v0.0.5 ``sessions`` table."""

from __future__ import annotations

import sqlite3
import time

from voicegateway.services.storage_service import StorageService


def _normalise_type(declared: str) -> str:
    """Map a declared SQLite column type to its storage-class affinity."""
    upper = declared.upper()
    if "INT" in upper:
        return "INTEGER"
    if "CHAR" in upper or "CLOB" in upper or "TEXT" in upper:
        return "TEXT"
    if "BLOB" in upper or not upper:
        return "BLOB"
    if "REAL" in upper or "FLOA" in upper or "DOUB" in upper:
        return "REAL"
    return "NUMERIC"


def _column_info(db_path: str, table: str) -> dict[str, dict]:
    """Return {col_name: {type, notnull, default, pk}} for the given table."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(f"PRAGMA table_info({table})")
        result = {}
        for row in cursor.fetchall():
            # PRAGMA table_info: cid, name, type, notnull, dflt_value, pk
            result[row[1]] = {
                "type": _normalise_type(row[2]),
                "notnull": bool(row[3]),
                "default": row[4],
                "pk": bool(row[5]),
            }
        return result
    finally:
        conn.close()


def _index_names(db_path: str, table: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
            (table,),
        )
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_fresh_install_has_sessions_table(tmp_path):
    db_path = str(tmp_path / "fresh.db")
    storage = StorageService(db_path)
    await storage._ensure_initialized()

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
        )
        row = cursor.fetchone()
    finally:
        conn.close()
    assert row is not None, "sessions table missing on fresh install"


async def test_sessions_columns_match_design(tmp_path):
    """Columns and types match design.md section 3.2."""
    db_path = str(tmp_path / "fresh.db")
    storage = StorageService(db_path)
    await storage._ensure_initialized()

    cols = _column_info(db_path, "sessions")
    assert set(cols) == {
        "id",
        "project",
        "started_at",
        "ended_at",
        "modalities",
        "total_cost_usd",
        "request_count",
        # v0.2.0 aggregate columns (added by migration 0003).
        "talk_time_seconds",
        "per_minute_cost_usd",
        "response_speed_p50_ms",
        "response_speed_p95_ms",
        "talk_over_rate",
        # v0.3.0 replay storage column (added by migration 0004).
        "replay_size_bytes",
        # v0.4.0 tenant attribution column (added by migration 0005).
        "tenant_id",
        # Phase 2 fleet per-agent attribution (migration c4f1a9e2b8d7).
        "agent_id",
        # v0.5.0 routing columns (added by migration 0006).
        "budget_ms",
        "budget_ms_used",
        "budget_overrun",
        "routed_llm",
        "routed_tts",
        # calls correlation, nullable + forward-only (migration e4a7c2f9b1d3).
        "room_name",
        "call_id",
    }

    # Required keys (NOT NULL).
    assert cols["id"]["pk"] is True
    assert cols["id"]["type"] == "TEXT"
    assert cols["project"]["notnull"] is True
    assert cols["project"]["type"] == "TEXT"
    assert cols["started_at"]["notnull"] is True
    assert cols["started_at"]["type"] == "TEXT"

    # Nullable end-of-session marker.
    assert cols["ended_at"]["notnull"] is False
    assert cols["ended_at"]["type"] == "TEXT"

    # Cost / count have safe defaults so partial inserts behave.
    assert cols["total_cost_usd"]["type"] == "REAL"
    assert cols["request_count"]["type"] == "INTEGER"


async def test_sessions_indexes_exist(tmp_path):
    db_path = str(tmp_path / "fresh.db")
    storage = StorageService(db_path)
    await storage._ensure_initialized()

    indexes = _index_names(db_path, "sessions")
    assert "idx_sessions_project" in indexes
    assert "idx_sessions_started_at" in indexes


async def test_sessions_insert_round_trip(tmp_path):
    """A direct INSERT + SELECT works against the new schema."""
    db_path = str(tmp_path / "fresh.db")
    storage = StorageService(db_path)
    await storage._ensure_initialized()

    sid = "vg-test-session-id"
    started = "2026-05-06T19:50:00Z"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO sessions
               (id, project, started_at, modalities, total_cost_usd, request_count)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (sid, "tony-pizza", started, "stt,llm,tts", 0.0123, 3),
        )
        conn.commit()

        cursor = conn.execute(
            "SELECT id, project, started_at, ended_at, modalities, "
            "total_cost_usd, request_count FROM sessions WHERE id = ?",
            (sid,),
        )
        row = cursor.fetchone()
    finally:
        conn.close()

    assert row == (sid, "tony-pizza", started, None, "stt,llm,tts", 0.0123, 3)


async def test_sessions_defaults_apply_on_partial_insert(tmp_path):
    """Inserting only required columns leaves optional numerics NULL."""
    db_path = str(tmp_path / "fresh.db")
    storage = StorageService(db_path)
    await storage._ensure_initialized()

    sid = "vg-test-partial"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO sessions (id, project, started_at)
               VALUES (?, ?, ?)""",
            (sid, "default", "2026-05-06T19:55:00Z"),
        )
        conn.commit()

        cursor = conn.execute(
            "SELECT modalities, total_cost_usd, request_count, ended_at "
            "FROM sessions WHERE id = ?",
            (sid,),
        )
        row = cursor.fetchone()
    finally:
        conn.close()

    # ``modalities`` has a server default of ''; ``total_cost_usd`` and
    # ``request_count`` are nullable per the SQLModel definition.
    assert row == ("", None, None, None)


async def test_sessions_table_is_idempotent_on_reinit(tmp_path):
    """Re-opening the storage doesn't error or drop existing rows."""
    db_path = str(tmp_path / "fresh.db")
    s1 = StorageService(db_path)
    await s1._ensure_initialized()

    sid = "vg-test-keep"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO sessions (id, project, started_at) VALUES (?, ?, ?)",
            (sid, "default", str(time.time())),
        )
        conn.commit()
    finally:
        conn.close()

    s2 = StorageService(db_path)
    await s2._ensure_initialized()

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT id FROM sessions WHERE id = ?", (sid,))
        row = cursor.fetchone()
    finally:
        conn.close()
    assert row == (sid,)
