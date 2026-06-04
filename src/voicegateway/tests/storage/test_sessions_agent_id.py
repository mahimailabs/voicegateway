"""Phase 2: agent_id on the sessions table + the list_sessions agent filter."""

from __future__ import annotations

import sqlite3
import time
import uuid

from voicegateway.models.request_model import RequestRecord
from voicegateway.services.storage_service import StorageService


def _record(
    session_id: str,
    agent_id: str,
    *,
    project: str = "fleet",
    modality: str = "llm",
    cost: float = 0.01,
) -> RequestRecord:
    return RequestRecord(
        id=str(uuid.uuid4()),
        timestamp=time.time(),
        modality=modality,
        model_id="openai/gpt-4o-mini",
        provider="openai",
        project=project,
        cost_usd=cost,
        session_id=session_id,
        agent_id=agent_id,
    )


def _session_agent_id(db_path: str, sid: str) -> str | None:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT agent_id FROM sessions WHERE id = ?", (sid,)
        ).fetchone()
    finally:
        conn.close()
    return None if row is None else row[0]


async def test_session_row_carries_agent_id(tmp_path):
    db_path = str(tmp_path / "sess_agent.db")
    storage = StorageService(db_path)
    await storage.log_request(_record("vg-a", "agent-x"))
    assert _session_agent_id(db_path, "vg-a") == "agent-x"


async def test_session_agent_id_coalesce_preserved_across_requests(tmp_path):
    """The first request's agent_id is preserved across the session's UPSERTs."""
    db_path = str(tmp_path / "sess_agent2.db")
    storage = StorageService(db_path)
    await storage.log_request(_record("vg-a", "agent-x", modality="stt"))
    await storage.log_request(_record("vg-a", "agent-x", modality="llm"))
    assert _session_agent_id(db_path, "vg-a") == "agent-x"


async def test_list_sessions_filters_by_agent(tmp_path):
    storage = StorageService(str(tmp_path / "sess_agent3.db"))
    await storage.log_request(_record("vg-a", "agent-x"))
    await storage.log_request(_record("vg-b", "agent-y"))

    only_x = await storage.list_sessions(agent="agent-x")
    assert {s["id"] for s in only_x} == {"vg-a"}

    only_y = await storage.list_sessions(agent="agent-y")
    assert {s["id"] for s in only_y} == {"vg-b"}
