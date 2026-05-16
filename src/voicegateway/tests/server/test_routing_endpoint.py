"""Tests for /api/routing/observations (REQ-VG-ROUTE-003).

After the dashboard fold-in, this endpoint lives in
:mod:`voicegateway.server.api.dashboard.routing` and is reached
through the daemon's ``build_app(...)``. The test now uses a real
Gateway built from ``temp_config`` to match the pattern every other
dashboard endpoint test uses.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from voicegateway.core.gateway import Gateway
from voicegateway.server.main import build_app


@pytest.fixture
async def client(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "routing.db"))
    gw = Gateway(config_path=temp_config)
    app = build_app(gw, enable_mcp_sse=False, enable_dashboard=True)
    yield gw, TestClient(app)


async def _seed(storage, rows):
    from sqlalchemy import text

    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        for project_id, provider, modality, p50 in rows:
            await db.execute(
                text(
                    "INSERT INTO latency_observations "
                    "(project_id, provider, modality, p50_ms, p95_ms, "
                    " sample_count, window_start, window_end) "
                    "VALUES (:project_id, :provider, :modality, :p50, :p95, "
                    " :sc, :ws, :we)"
                ),
                {
                    "project_id": project_id,
                    "provider": provider,
                    "modality": modality,
                    "p50": p50,
                    "p95": p50 + 100,
                    "sc": 10,
                    "ws": "x",
                    "we": "y",
                },
            )
        await db.commit()


async def test_returns_all_observations_when_no_filter(client) -> None:
    gw, c = client
    await _seed(
        gw.storage,
        [
            ("acme", "deepgram", "stt", 180),
            ("acme", "openai", "llm", 300),
            ("beta", "groq", "llm", 80),
        ],
    )
    r = c.get("/api/routing/observations")
    assert r.status_code == 200
    data = r.json()
    assert len(data["observations"]) == 3
    assert data["filter"]["project"] is None


async def test_filters_by_project(client) -> None:
    gw, c = client
    await _seed(
        gw.storage,
        [
            ("acme", "deepgram", "stt", 180),
            ("beta", "groq", "llm", 80),
        ],
    )
    r = c.get("/api/routing/observations?project=acme")
    assert r.status_code == 200
    data = r.json()
    assert len(data["observations"]) == 1
    assert data["observations"][0]["project_id"] == "acme"
    assert data["filter"]["project"] == "acme"


async def test_empty_returns_empty_list(client) -> None:
    _, c = client
    r = c.get("/api/routing/observations")
    assert r.status_code == 200
    assert r.json()["observations"] == []
