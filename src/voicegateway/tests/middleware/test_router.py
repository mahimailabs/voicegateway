"""Tests for ``voicegateway.middleware.router`` (REQ-VG-ROUTE-002)."""

from __future__ import annotations

import pytest

from voicegateway.core.config import ProjectConfig, RoutingConfig
from voicegateway.middleware import router
from voicegateway.middleware.router import BudgetExceeded
from voicegateway.storage.sqlite import SQLiteStorage


@pytest.fixture
async def storage(tmp_path):
    yield SQLiteStorage(db_path=str(tmp_path / "router.db"))


async def _seed_observations(storage, rows):
    db = await storage._ensure_initialized()
    for project_id, provider, modality, p50 in rows:
        await db.execute(
            "INSERT INTO latency_observations "
            "(project_id, provider, modality, p50_ms, p95_ms, sample_count, "
            " window_start, window_end) VALUES (?,?,?,?,?,?,?,?)",
            (
                project_id,
                provider,
                modality,
                p50,
                p50 + 100,
                50,
                "2026-05-11T00:00",
                "2026-05-12T00:00",
            ),
        )
    await db.commit()


def _project(budget_ms=600, fallback=True):
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
    db = await storage._ensure_initialized()
    triple = await router.route_session(
        db, project_id="default", project_config=_project(budget_ms=600)
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
    db = await storage._ensure_initialized()
    pc = ProjectConfig(
        id="default",
        name="Default",
        routing=RoutingConfig(
            budget_ms=300,  # 180+200+150 = 530 > 300
            rosters={
                "stt": ["deepgram"],
                "llm": ["openai"],
                "tts": ["cartesia"],
            },
            fallback_to_fastest=True,
        ),
    )
    triple = await router.route_session(db, project_id="default", project_config=pc)
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
    db = await storage._ensure_initialized()
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
        await router.route_session(db, project_id="default", project_config=pc)


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
    db = await storage._ensure_initialized()
    triple = await router.route_session(
        db,
        project_id="default",
        project_config=_project(budget_ms=600),
        caller_overrides={"llm": "openai"},
    )
    assert triple.llm == "openai"
    # The router still picks STT and TTS from the rosters.
    assert triple.stt == "deepgram"
    assert triple.tts == "cartesia"


async def test_falls_back_to_baselines_when_no_observations(storage) -> None:
    """No observations seeded; router should use provider_baselines.json."""
    db = await storage._ensure_initialized()
    triple = await router.route_session(
        db, project_id="default", project_config=_project(budget_ms=600)
    )
    # provider_baselines.json: deepgram stt 250 + groq llm 80 + cartesia tts 150 = 480 (under 600)
    assert triple.stt == "deepgram"
    assert triple.llm == "groq"
    assert triple.tts == "cartesia"
    assert triple.budget_overrun is False


async def test_unknown_override_modality_raises(storage) -> None:
    db = await storage._ensure_initialized()
    with pytest.raises(ValueError):
        await router.route_session(
            db,
            project_id="default",
            project_config=_project(budget_ms=600),
            caller_overrides={"audio": "deepgram"},
        )


async def test_empty_roster_raises(storage) -> None:
    db = await storage._ensure_initialized()
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
        await router.route_session(db, project_id="default", project_config=pc)


async def test_observed_p50_beats_baseline(storage) -> None:
    """When both observed + baseline exist, observed wins."""
    # Baseline says groq llm 80 ms. Override with observed 500 ms.
    await _seed_observations(
        storage,
        [
            ("default", "deepgram", "stt", 100),
            ("default", "openai", "llm", 200),  # observed beats baseline 300
            ("default", "groq", "llm", 500),  # observed worse than baseline 80
            ("default", "cartesia", "tts", 150),
        ],
    )
    db = await storage._ensure_initialized()
    triple = await router.route_session(
        db, project_id="default", project_config=_project(budget_ms=600)
    )
    # With observed values, openai (200) beats groq (500): 100+200+150=450.
    assert triple.llm == "openai"
