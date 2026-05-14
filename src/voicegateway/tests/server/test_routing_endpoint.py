"""Tests for /api/routing/observations (REQ-VG-ROUTE-003)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import dashboard.api.main as api
from voicegateway.services.storage_service import StorageService


class _FakeGateway:
    def __init__(self, path: str):
        self.storage = StorageService(path)

        class _Cfg:
            class auth:
                api_keys = []
                cors_origins = []

            latency: dict = {}
            projects: dict = {}

        self.config = _Cfg()

    def list_projects(self):
        return []


@pytest.fixture
async def client(tmp_path, monkeypatch):
    path = str(tmp_path / "routing.db")
    gw = _FakeGateway(path)
    monkeypatch.setattr(api, "_gateway", gw)
    monkeypatch.setattr(api, "_cors_configured", True)
    yield gw, TestClient(api.app)


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
