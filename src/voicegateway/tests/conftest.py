"""Shared pytest fixtures."""

import asyncio
import os
import sqlite3
import threading
import time

# pytest's thread-exception hook imports tracemalloc lazily, from the dying
# thread. When two aiosqlite worker threads die at once (the teardown race the
# filterwarnings entry in pyproject.toml silences) they race that first import,
# the hook itself raises KeyError, and that fails whichever test is running
# because the filter never sees it. Importing it here, first, removes the race.
import tracemalloc  # noqa: F401
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from voicegateway.models.request_model import RequestRecord

# VOICEGW_DB_PATH beats an explicit db_path everywhere it is read, so one test
# that sets it and forgets to unset it redirects every later StorageService,
# Gateway, and migration in the run into a single shared file. That shows up as
# "no such table" on a test's own tmp database and as UNIQUE-constraint
# collisions between tests that never touched each other. Snapshot it per test
# so a leak stops at the test that caused it.
_LEAKY_ENV_VARS = ("VOICEGW_DB_PATH",)


async def wait_until(
    condition: Callable[[], bool], *, timeout: float = 5.0, interval: float = 0.005
) -> None:
    """Poll ``condition`` until it holds; fail after ``timeout`` seconds.

    Replaces fixed sleeps: returns as soon as the condition is true on a quiet
    machine, and still waits long enough on a busy one.
    """
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"condition not met within {timeout}s")
        await asyncio.sleep(interval)


@pytest.fixture(autouse=True)
def _isolate_db_path_env():
    """Restore process-global DB env vars a test may have set directly."""
    saved = {name: os.environ.get(name) for name in _LEAKY_ENV_VARS}
    yield
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


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


@pytest.fixture
def example_config_path(tmp_path):
    """Write the bundled example config to a tmp file and return its path."""
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
def temp_config(tmp_path, monkeypatch):
    """Write a minimal voicegw.yaml + isolate the SQLite path for the test."""
    config_path = tmp_path / "voicegw.yaml"
    with open(config_path, "w") as f:
        yaml.dump(_MINIMAL_CONFIG, f)
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "temp-config.db"))
    return str(config_path)


@pytest.fixture
async def seeded_storage(tmp_path):
    """Create a StorageService with sample request records."""
    from voicegateway.services.storage_service import StorageService

    db_path = str(tmp_path / "test.db")
    storage = StorageService(db_path)

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


_TEMPLATE: dict[str, Path] = {}
_TEMPLATE_LOCK = threading.Lock()


def _has_schema(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    with sqlite3.connect(path) as conn:
        return bool(conn.execute("select count(*) from sqlite_master").fetchone()[0])


def _sqlite_copy(src: Path, dst: Path) -> None:
    with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
        s.backup(d)


@pytest.fixture(autouse=True)
def _migrated_sqlite_template(request, monkeypatch, tmp_path_factory):
    """Seed each fresh SQLite file from one migrated template.

    A fresh ``alembic upgrade head`` costs about a second, and nearly every
    test opens its own database. The real upgrade still runs on every
    file (it is a no-op check at head), so the migration path is unchanged;
    only the repeated table creation is skipped. Migrations themselves are
    covered by the alembic tests.
    """
    name = request.node.path.name
    if (
        "migrat" in name
        or "schema" in name
        or name.startswith("test_database_")
        or request.node.get_closest_marker("real_migrations")
    ):
        # Tests about the migration path itself run the real upgrade.
        yield
        return

    from voicegateway.core import database as db_mod

    original = db_mod.Database._run_alembic_upgrade

    def seeded(self) -> None:
        url = db_mod.resolve_database_url(self.config)
        prefix = "sqlite+aiosqlite:///"
        if not url.startswith(prefix):
            return original(self)
        target = Path(url[len(prefix) :])
        with _TEMPLATE_LOCK:
            template = _TEMPLATE.get("path")
            if template is None:
                if _has_schema(target):
                    # A test that pre-built a legacy schema; never let its
                    # rows become the template.
                    return original(self)
                original(self)
                template = tmp_path_factory.getbasetemp() / "migrated-template.db"
                _sqlite_copy(target, template)
                _TEMPLATE["path"] = template
                return None
            if not _has_schema(target):
                _sqlite_copy(template, target)
        return original(self)

    monkeypatch.setattr(db_mod.Database, "_run_alembic_upgrade", seeded)
    yield
