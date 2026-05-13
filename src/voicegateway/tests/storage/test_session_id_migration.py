"""Migration tests for the v0.0.5 ``session_id`` column on ``requests``.

The release notes promise a clean upgrade from v0.0.4 databases: the new
``session_id TEXT`` column lands as nullable, existing rows survive with
``NULL`` (correct semantics — those requests pre-date session
correlation), and the index is created. These tests pin that contract
down so a future schema refactor cannot silently drop it.

The "v0.0.4 snapshot" we test against is the pre-session_id schema
shape: the requests table from prior releases has 16 columns, no
session_id, no idx_requests_session_id index. We seed a small
representative dataset, then re-open the DB through SQLiteStorage and
assert the migration ran cleanly.
"""

from __future__ import annotations

import sqlite3
import time
import uuid

from voicegateway.models.request import RequestRecord
from voicegateway.storage.sqlite import SQLiteStorage

# v0.0.4 schema fragment for the requests table — verbatim from before
# the v0.0.5 session_id column was added. Used to seed a "pre-migration"
# database that SQLiteStorage then upgrades when opened.
_V004_REQUESTS_DDL = """
CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY,
    timestamp REAL NOT NULL,
    project TEXT NOT NULL DEFAULT 'default',
    modality TEXT NOT NULL,
    model_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    input_units REAL DEFAULT 0,
    output_units REAL DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    pricing_source TEXT NOT NULL DEFAULT '',
    ttfb_ms REAL,
    total_latency_ms REAL,
    status TEXT DEFAULT 'success',
    fallback_from TEXT,
    error_message TEXT,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_requests_timestamp ON requests(timestamp);
"""


def _seed_v004_database(db_path: str) -> list[dict]:
    """Build a v0.0.4-shaped sqlite file with a few request rows.

    Uses the synchronous sqlite3 module to seed the fixture: this is a
    one-off setup step in a test, and avoiding aiosqlite here sidesteps
    any event-loop interactions while the assert phase is still async.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_V004_REQUESTS_DDL)
        now = time.time()
        seeds = [
            {
                "id": str(uuid.uuid4()),
                "timestamp": now - 60,
                "project": "tony-pizza",
                "modality": "stt",
                "model_id": "deepgram/nova-3",
                "provider": "deepgram",
                "input_units": 1.0,
                "output_units": 0.0,
                "cost_usd": 0.0043,
                "pricing_source": "",
                "ttfb_ms": 120.0,
                "total_latency_ms": 250.0,
                "status": "success",
                "fallback_from": None,
                "error_message": None,
                "metadata": None,
            },
            {
                "id": str(uuid.uuid4()),
                "timestamp": now - 30,
                "project": "default",
                "modality": "llm",
                "model_id": "openai/gpt-4o-mini",
                "provider": "openai",
                "input_units": 100,
                "output_units": 50,
                "cost_usd": 0.015,
                "pricing_source": "genai-prices@0.0.57",
                "ttfb_ms": 200.0,
                "total_latency_ms": 800.0,
                "status": "success",
                "fallback_from": None,
                "error_message": None,
                "metadata": None,
            },
        ]
        for row in seeds:
            conn.execute(
                """INSERT INTO requests
                   (id, timestamp, project, modality, model_id, provider,
                    input_units, output_units, cost_usd, pricing_source,
                    ttfb_ms, total_latency_ms, status, fallback_from,
                    error_message, metadata)
                   VALUES (:id, :timestamp, :project, :modality, :model_id,
                    :provider, :input_units, :output_units, :cost_usd,
                    :pricing_source, :ttfb_ms, :total_latency_ms, :status,
                    :fallback_from, :error_message, :metadata)""",
                row,
            )
        conn.commit()
    finally:
        conn.close()
    return seeds


def _column_names(db_path: str, table: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(f"PRAGMA table_info({table})")
        return [row[1] for row in cursor.fetchall()]
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


async def test_migration_adds_session_id_column(tmp_path):
    db_path = str(tmp_path / "v004.db")
    _seed_v004_database(db_path)

    pre_cols = _column_names(db_path, "requests")
    assert "session_id" not in pre_cols, "fixture must start without session_id"

    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()

    post_cols = _column_names(db_path, "requests")
    assert "session_id" in post_cols


async def test_migration_creates_session_id_index(tmp_path):
    db_path = str(tmp_path / "v004.db")
    _seed_v004_database(db_path)

    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()

    indexes = _index_names(db_path, "requests")
    assert "idx_requests_session_id" in indexes


async def test_migration_preserves_existing_rows(tmp_path):
    db_path = str(tmp_path / "v004.db")
    seeds = _seed_v004_database(db_path)

    storage = SQLiteStorage(db_path)
    rows = await storage.get_recent_requests(limit=10)

    assert len(rows) == len(seeds)
    survived_ids = {r["id"] for r in rows}
    assert survived_ids == {s["id"] for s in seeds}


async def test_migration_fills_pre_existing_session_id_with_null(tmp_path):
    db_path = str(tmp_path / "v004.db")
    seeds = _seed_v004_database(db_path)

    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT id, session_id FROM requests ORDER BY id")
        rows = cursor.fetchall()
    finally:
        conn.close()

    assert len(rows) == len(seeds)
    for _id, session_id in rows:
        # Pre-v0.0.5 rows MUST be NULL — they pre-date the feature.
        # A non-NULL would mean we accidentally backfilled history
        # with synthetic IDs, which would corrupt session-level metrics.
        assert session_id is None


async def test_new_inserts_can_carry_session_id(tmp_path):
    db_path = str(tmp_path / "v004.db")
    _seed_v004_database(db_path)

    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()

    # Direct SQL insert with a session_id — verifies the column is
    # writable post-migration. (RequestRecord plumbing for session_id
    # lands in 5.6 #3, so we drop down to raw SQL here.)
    new_id = str(uuid.uuid4())
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO requests
               (id, timestamp, project, modality, model_id, provider,
                cost_usd, session_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id,
                time.time(),
                "test",
                "stt",
                "deepgram/nova-3",
                "deepgram",
                0.001,
                "vg-test-session",
            ),
        )
        conn.commit()

        cursor = conn.execute("SELECT session_id FROM requests WHERE id = ?", (new_id,))
        row = cursor.fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0] == "vg-test-session"


async def test_fresh_database_has_session_id_in_initial_schema(tmp_path):
    """A clean install (not a migration) must already include the column.

    Otherwise the migration check is the only thing creating the column
    in fresh databases too — fragile coupling.
    """
    db_path = str(tmp_path / "fresh.db")
    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()

    cols = _column_names(db_path, "requests")
    assert "session_id" in cols

    indexes = _index_names(db_path, "requests")
    assert "idx_requests_session_id" in indexes


async def test_migration_is_idempotent(tmp_path):
    """Re-initializing an already-migrated DB does not error.

    SQLiteStorage opens the same file repeatedly across the runtime;
    the migration check must short-circuit when the column is already
    present.
    """
    db_path = str(tmp_path / "v004.db")
    seeds = _seed_v004_database(db_path)

    s1 = SQLiteStorage(db_path)
    await s1._ensure_initialized()

    s2 = SQLiteStorage(db_path)
    await s2._ensure_initialized()  # would raise OperationalError if not idempotent

    rows = await s2.get_recent_requests(limit=10)
    assert len(rows) == len(seeds)


async def test_request_record_log_after_migration_does_not_break(tmp_path):
    """Logging a fresh RequestRecord on a migrated DB still works.

    RequestRecord hasn't been extended with session_id yet (5.6 #3
    territory); the existing INSERT path must keep working without
    populating the new column.
    """
    db_path = str(tmp_path / "v004.db")
    _seed_v004_database(db_path)

    storage = SQLiteStorage(db_path)
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=time.time(),
            modality="stt",
            model_id="deepgram/nova-3",
            provider="deepgram",
            project="default",
            cost_usd=0.001,
        )
    )
    rows = await storage.get_recent_requests(limit=10)
    # Pre-existing 2 + the new 1 = 3
    assert len(rows) == 3
