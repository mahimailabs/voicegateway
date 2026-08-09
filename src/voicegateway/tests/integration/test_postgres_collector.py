"""Collector paths exercised against a real Postgres backend.

This is the regression gate for SQLite-vs-Postgres dialect drift: every bug that
broke the fleet collector on Postgres (ambiguous ``ON CONFLICT`` columns,
``GROUP_CONCAT``, ``datetime('now')``) would fail here.

The test only runs when ``VOICEGW_DB_URL`` points at Postgres; CI sets it to the
``postgres`` service. It is skipped in the default SQLite test run.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.server import build_app

pytestmark = pytest.mark.skipif(
    not os.environ.get("VOICEGW_DB_URL", "").startswith("postgresql"),
    reason="requires VOICEGW_DB_URL=postgresql+asyncpg://... (CI postgres service)",
)

_KEY = "pgtestkey"


def _config(tmp_path) -> str:
    path = tmp_path / "voicegw.yaml"
    path.write_text(
        yaml.safe_dump(
            {"auth": {"api_keys": [{"token": _KEY, "name": "t", "scopes": ["write"]}]}}
        )
    )
    return str(path)


async def test_collector_init_ingest_costs_on_postgres(tmp_path) -> None:
    """Init (migrations + project upsert), ingest (session upsert), and the cost
    summary (string aggregate) all succeed against Postgres, and the read
    endpoints never 500."""
    gw = Gateway(config_path=_config(tmp_path))  # migrations + _PROJECT_UPSERT
    app = build_app(gw)
    headers = {"Authorization": f"Bearer {_KEY}"}
    record = {
        "id": f"pg-{time.time()}",
        "timestamp": time.time(),
        "modality": "llm",
        "model_id": "openai/gpt-4o",
        "provider": "openai",
        "project": "default",
        "input_units": 1000,
        "output_units": 500,
        "cost_usd": 0.05,
        "session_id": "pg-sess",
        "agent_id": "pg-agent",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        ingest = await client.post("/v1/ingest", headers=headers, json=[record])
        assert ingest.status_code == 200, ingest.text
        assert ingest.json()["accepted"] == 1  # session upsert ran on Postgres

        costs = await client.get("/v1/costs", headers=headers)
        assert costs.status_code == 200, costs.text  # GROUP_CONCAT -> STRING_AGG
        assert costs.json()["total"] >= 0.05

        for path in (
            "/v1/status",
            "/v1/models",
            "/v1/projects",
            "/v1/logs",
            "/v1/metrics",
            "/v1/api-keys",
            "/api/overview",
            "/api/status",
        ):
            resp = await client.get(path, headers=headers)
            assert resp.status_code < 500, f"{path} -> {resp.status_code}: {resp.text}"


async def test_turn_ingest_survives_a_real_millisecond_timestamp(tmp_path) -> None:
    """The gate for the int32 overflow, on the backend that enforces widths.

    ``POST /v1/ingest/turns`` answered 503 for every turn, because ``Turn``
    declared its five ``_ms`` columns as plain ``int`` (INTEGER, int32) while
    the values are millisecond timestamps:

        DataError: invalid input for query argument $3 in element #0 of
        executemany() sequence: 6886684364 (value out of int32 range)

    so the table stayed empty and ``e2e_ms`` stayed null.

    This has to run on Postgres to mean anything. SQLite's dynamic typing stores
    an oversized value in an INTEGER column without complaint, so the same
    request succeeds there with the schema still broken, which is how this
    shipped. ``storage/test_turn_ms_width.py`` carries the type-level guard that
    does work on SQLite.

    Epoch milliseconds are used rather than a synthetic small number for the
    same reason: a test with toy timestamps passes today and keeps passing while
    production fails.
    """
    app = build_app(Gateway(config_path=_config(tmp_path)))
    headers = {"Authorization": f"Bearer {_KEY}"}
    now_ms = int(time.time() * 1000)
    assert now_ms > 2147483647, "epoch ms must exceed int32 for this to be a gate"
    # Unique per run: CI gets a fresh database, a developer re-running against a
    # persistent one does not, and a fixed id would accumulate rows and fail on
    # the count rather than on anything real.
    session_id = f"pg-turn-{uuid.uuid4()}"

    rows = [
        {
            "session_id": session_id,
            "turn_index": 0,
            "caller_speak_start_ms": now_ms,
            "caller_speak_end_ms": now_ms + 500,
            "agent_speak_start_ms": now_ms + 900,
            "agent_speak_end_ms": now_ms + 2000,
            "response_speed_ms": 400,
        }
    ]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        posted = await client.post(
            "/v1/ingest/turns", headers=headers, json=rows
        )
        # The reported symptom was 503 here, from the DataError the INTEGER
        # column raised on a millisecond timestamp.
        assert posted.status_code == 200, posted.text
        assert posted.json()["accepted"] == 1

    # Read back through the repository rather than the dashboard endpoint: the
    # ingest key carries only the write scope, and this test is about column
    # width, not authorization.
    from voicegateway.repository import turns_repository as turns
    from voicegateway.services.storage_service import StorageService

    storage = StorageService(str(tmp_path / "unused.db"))
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        stored = await turns.list_turns_by_session(db, session_id)
    await storage.aclose()

    assert len(stored) == 1
    assert stored[0].caller_speak_start_ms == now_ms, (
        "the timestamp did not survive the round trip intact"
    )
    assert stored[0].agent_speak_end_ms == now_ms + 2000
