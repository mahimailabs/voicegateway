"""Tests for ``voicegateway.middleware.router`` (REQ-VG-ROUTE-002)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from voicegateway.core.config import ProjectConfig, RoutingConfig
from voicegateway.middleware import router
from voicegateway.middleware.router import BudgetExceeded
from voicegateway.services.storage_service import StorageService


@pytest.fixture
async def storage(tmp_path):
    yield StorageService(db_path=str(tmp_path / "router.db"))


_INSERT_OBS = text(
    "INSERT INTO latency_observations "
    "(project_id, provider, modality, p50_ms, p95_ms, sample_count, "
    " window_start, window_end) "
    "VALUES (:project_id, :provider, :modality, :p50, :p95, :sc, :ws, :we)"
)


async def _seed_observations(storage, rows):
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        payload = [
            {
                "project_id": p,
                "provider": prov,
                "modality": mod,
                "p50": p50,
                "p95": p50 + 100,
                "sc": 50,
                "ws": "2026-05-11T00:00",
                "we": "2026-05-12T00:00",
            }
            for p, prov, mod, p50 in rows
        ]
        if payload:
            await db.execute(_INSERT_OBS, payload)
            await db.commit()


def project(budget_ms=600, fallback=True):
    return ProjectConfig(
        id="default",
        name="Default",
        routing=RoutingConfig(
            budget_ms=budget_ms,
            rosters={
                "stt": ["deepgram", "assemblyai"],
                "llm": ["openai", "groq"],
                "tts": ["cartesia", "elevenlabs"],
            },
            fallback_to_fastest=fallback,
        ),
    )


async def _route(storage, **kwargs):
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        return await router.route_session(db, **kwargs)


async def test_picks_lowest_under_budget(storage) -> None:
    await _seed_observations(
        storage,
        [
            ("default", "deepgram", "stt", 180),
            ("default", "assemblyai", "stt", 300),
            ("default", "openai", "llm", 200),
            ("default", "groq", "llm", 80),
            ("default", "cartesia", "tts", 150),
            ("default", "elevenlabs", "tts", 400),
        ],
    )
    triple = await _route(
        storage, project_id="default", project_config=project(budget_ms=600)
    )
    assert triple.stt == "deepgram"
    assert triple.llm == "groq"
    assert triple.tts == "cartesia"
    assert triple.predicted_ms == 410
    assert triple.budget_overrun is False


async def test_picks_fastest_when_nothing_fits(storage) -> None:
    await _seed_observations(
        storage,
        [
            ("default", "deepgram", "stt", 180),
            ("default", "openai", "llm", 200),
            ("default", "cartesia", "tts", 150),
        ],
    )
    pc = ProjectConfig(
        id="default",
        name="Default",
        routing=RoutingConfig(
            budget_ms=300,
            rosters={
                "stt": ["deepgram"],
                "llm": ["openai"],
                "tts": ["cartesia"],
            },
            fallback_to_fastest=True,
        ),
    )
    triple = await _route(storage, project_id="default", project_config=pc)
    assert triple.budget_overrun is True
    assert triple.predicted_ms == 530


async def test_raises_when_fallback_disabled(storage) -> None:
    await _seed_observations(
        storage,
        [
            ("default", "deepgram", "stt", 180),
            ("default", "openai", "llm", 200),
            ("default", "cartesia", "tts", 150),
        ],
    )
    pc = ProjectConfig(
        id="default",
        name="Default",
        routing=RoutingConfig(
            budget_ms=100,
            rosters={
                "stt": ["deepgram"],
                "llm": ["openai"],
                "tts": ["cartesia"],
            },
            fallback_to_fastest=False,
        ),
    )
    with pytest.raises(BudgetExceeded):
        await _route(storage, project_id="default", project_config=pc)


async def test_caller_overrides_pin_modalities(storage) -> None:
    await _seed_observations(
        storage,
        [
            ("default", "deepgram", "stt", 180),
            ("default", "openai", "llm", 200),
            ("default", "groq", "llm", 80),
            ("default", "cartesia", "tts", 150),
        ],
    )
    triple = await _route(
        storage,
        project_id="default",
        project_config=project(budget_ms=600),
        caller_overrides={"llm": "openai"},
    )
    assert triple.llm == "openai"
    assert triple.stt == "deepgram"
    assert triple.tts == "cartesia"


async def test_falls_back_to_baselines_when_no_observations(storage) -> None:
    """No observations seeded; router should use provider_baselines.json."""
    triple = await _route(
        storage, project_id="default", project_config=project(budget_ms=600)
    )
    assert triple.stt == "deepgram"
    assert triple.llm == "groq"
    assert triple.tts == "cartesia"
    assert triple.budget_overrun is False


async def test_unknown_override_modality_raises(storage) -> None:
    with pytest.raises(ValueError):
        await _route(
            storage,
            project_id="default",
            project_config=project(budget_ms=600),
            caller_overrides={"audio": "deepgram"},
        )


async def test_empty_roster_raises(storage) -> None:
    pc = ProjectConfig(
        id="default",
        name="Default",
        routing=RoutingConfig(
            budget_ms=600,
            rosters={"stt": [], "llm": [], "tts": []},
            fallback_to_fastest=True,
        ),
    )
    with pytest.raises(ValueError):
        await _route(storage, project_id="default", project_config=pc)


async def test_observed_p50_beats_baseline(storage) -> None:
    """When both observed + baseline exist, observed wins."""
    await _seed_observations(
        storage,
        [
            ("default", "deepgram", "stt", 100),
            ("default", "openai", "llm", 200),
            ("default", "groq", "llm", 500),
            ("default", "cartesia", "tts", 150),
        ],
    )
    triple = await _route(
        storage, project_id="default", project_config=project(budget_ms=600)
    )
    assert triple.llm == "openai"
