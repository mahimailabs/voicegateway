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
