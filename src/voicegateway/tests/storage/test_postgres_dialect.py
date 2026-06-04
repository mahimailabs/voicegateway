"""Postgres dual-dialect collector round-trip.

Guarded: runs only when VOICEGW_TEST_PG_URL points at a reachable Postgres
(e.g. ``postgresql+asyncpg://user:pw@localhost:5433/vgtest``). It exercises the
dialect-branched paths a SQLite run can never reach: the Postgres ``daily_costs``
view DDL and the STRPOS variant of the sessions UPSERT. Skipped otherwise.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest
from sqlalchemy import text

from voicegateway.models.request_model import RequestRecord
from voicegateway.services.storage_service import StorageService

_PG_URL = os.environ.get("VOICEGW_TEST_PG_URL")

pytestmark = pytest.mark.skipif(
    not _PG_URL,
    reason="set VOICEGW_TEST_PG_URL to a reachable Postgres to run dialect tests",
)


async def test_collector_round_trip_on_postgres(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_URL", _PG_URL)
    storage = StorageService(str(tmp_path / "unused.db"))
    rid = f"pg-{uuid.uuid4()}"
    sid = f"vg-{uuid.uuid4()}"
    try:
        # Runs alembic head on Postgres -> exercises the PG view DDL branch.
        await storage._ensure_initialized()

        # log_request exercises the requests INSERT + the STRPOS sessions UPSERT.
        await storage.log_request(
            RequestRecord(
                id=rid,
                timestamp=time.time(),
                modality="llm",
                model_id="openai/gpt-4o-mini",
                provider="openai",
                project="fleet",
                cost_usd=0.01,
                agent_id="agent-pg",
                session_id=sid,
            )
        )

        rows = await storage.get_recent_requests(limit=50)
        match = [r for r in rows if r["id"] == rid]
        assert len(match) == 1
        assert match[0]["agent_id"] == "agent-pg"

        # The Postgres daily_costs view must be queryable.
        async with storage._conn.session() as s:
            res = await s.execute(text("SELECT day, total_cost FROM daily_costs"))
            assert res.first() is not None
    finally:
        await storage.aclose()
