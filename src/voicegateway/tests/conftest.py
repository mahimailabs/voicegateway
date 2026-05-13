"""Shared pytest fixtures."""

import asyncio
import time
import uuid
from typing import Any

import pytest
import yaml

from voicegateway.models.request import RequestRecord


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    """Set fake API keys for all tests."""
    for key in [
        "OPENAI_API_KEY",
        "DEEPGRAM_API_KEY",
        "CARTESIA_API_KEY",
        "ANTHROPIC_API_KEY",
        "GROQ_API_KEY",
        "ELEVENLABS_API_KEY",
        "ASSEMBLYAI_API_KEY",
    ]:
        monkeypatch.setenv(key, "test-key-value")
    yield


@pytest.fixture(autouse=True)
def _close_aiosqlite_connections(monkeypatch):
    """Close raw SQLite handles returned by tests through _ensure_initialized.

    SQLiteStorage opens a fresh aiosqlite connection for each call. Most
    production paths close those handles in finally blocks, but a number of
    tests call the internal initializer only to apply migrations, then discard
    the returned connection. Unclosed aiosqlite worker threads can finish after
    pytest closes the event loop and emit PytestUnhandledThreadExceptionWarning.
    """
    from voicegateway.storage.sqlite import SQLiteStorage

    opened: list[Any] = []
    original_ensure_initialized = SQLiteStorage._ensure_initialized

    async def _tracked_ensure_initialized(self):
        db = await original_ensure_initialized(self)
        opened.append(db)
        return db

    monkeypatch.setattr(
        SQLiteStorage, "_ensure_initialized", _tracked_ensure_initialized
    )
    yield

    if not opened:
        return

    async def _close_all() -> None:
        for db in reversed(opened):
            try:
                await db.close()
            except Exception:
                pass

    asyncio.run(_close_all())


@pytest.fixture
def example_config_path(tmp_path):
    """Write the bundled example config to a tmp file and return its path.

    Sources the file from ``voicegateway.data`` (where it actually ships
    inside the wheel) rather than the repo root. Writing to ``tmp_path``
    keeps tests free to mutate without affecting the canonical copy.
    """
    from importlib import resources

    src = resources.files("voicegateway.data").joinpath("voicegw.example.yaml")
    target = tmp_path / "voicegw.example.yaml"
    target.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return str(target)


_MINIMAL_CONFIG = {
    "providers": {
        "openai": {"api_key": "test-key"},
        "deepgram": {"api_key": "test-key"},
    },
    "models": {
        "stt": {
            "deepgram/nova-3": {"provider": "deepgram", "model": "nova-3"},
        },
        "llm": {
            "openai/gpt-4o-mini": {"provider": "openai", "model": "gpt-4o-mini"},
        },
        "tts": {},
    },
    "stacks": {
        "default": {
            "stt": "deepgram/nova-3",
            "llm": "openai/gpt-4o-mini",
        },
    },
    "projects": {
        "test-project": {
            "name": "Test Project",
            "description": "For testing",
            "daily_budget": 10.0,
            "budget_action": "warn",
            "tags": ["testing"],
        },
        "blocked-project": {
            "name": "Blocked Project",
            "daily_budget": 0.01,
            "budget_action": "block",
            "tags": ["testing"],
        },
    },
    "fallbacks": {"stt": [], "llm": [], "tts": []},
    "cost_tracking": {"enabled": True},
    "observability": {
        "latency_tracking": True,
        "cost_tracking": True,
        "request_logging": True,
    },
}


@pytest.fixture
def temp_config(tmp_path):
    """Write a minimal voicegw.yaml to a temp dir and return the path."""
    config_path = tmp_path / "voicegw.yaml"
    with open(config_path, "w") as f:
        yaml.dump(_MINIMAL_CONFIG, f)
    return str(config_path)


@pytest.fixture
async def seeded_storage(tmp_path):
    """Create a SQLiteStorage with sample request records."""
    from voicegateway.storage.sqlite import SQLiteStorage

    db_path = str(tmp_path / "test.db")
    storage = SQLiteStorage(db_path)

    now = time.time()
    records = [
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=now - 60,
            modality="stt",
            model_id="deepgram/nova-3",
            provider="deepgram",
            project="test-project",
            input_units=1.0,
            cost_usd=0.0043,
            ttfb_ms=120.0,
            total_latency_ms=250.0,
        ),
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=now - 30,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            project="test-project",
            input_units=100,
            output_units=50,
            cost_usd=0.015,
            ttfb_ms=200.0,
            total_latency_ms=800.0,
        ),
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=now - 10,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            project="default",
            input_units=50,
            output_units=25,
            cost_usd=0.008,
            ttfb_ms=180.0,
            total_latency_ms=600.0,
        ),
    ]
    for r in records:
        await storage.log_request(r)
    return storage
