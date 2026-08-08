"""``metrics.talk_over_min_overlap_ms`` and ``turn_buffer_flush_size`` are read.

Both were declared in the schema and parsed by the config loader, and neither
had a consumer. Setting either in ``voicegw.yaml`` validated and did nothing,
which is worse than not offering the knob: it reads as configured behaviour.

Both hang off ProjectConfig, not GatewayConfig, so both are per project.
"""

from __future__ import annotations

import importlib

import yaml

from voicegateway.core.config import GatewayConfig
from voicegateway.repository import turns_repository as turns
from voicegateway.schemas.config_schema import MetricsConfig as SchemaMetrics


def _config(tmp_path, *, overlap_ms: int, flush_size: int) -> str:
    path = tmp_path / "voicegw.yaml"
    path.write_text(
        yaml.dump(
            {
                "providers": {"openai": {"api_key": "k"}},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "fallbacks": {"stt": [], "llm": [], "tts": []},
                "cost_tracking": {"enabled": True},
                "projects": {
                    "tuned": {
                        "name": "Tuned",
                        "metrics": {
                            "talk_over_min_overlap_ms": overlap_ms,
                            "turn_buffer_flush_size": flush_size,
                        },
                    }
                },
            }
        )
    )
    return str(path)


def test_the_repository_default_matches_the_schema_default() -> None:
    """The repository restates the default rather than importing config.

    Restating is deliberate (the repository layer should not depend on config),
    but two copies of a number drift, so this is the thing that keeps them one.
    """
    assert (
        turns.DEFAULT_TALK_OVER_MIN_OVERLAP_MS
        == SchemaMetrics().talk_over_min_overlap_ms
    )


def test_the_flush_size_default_matches_the_schema_default() -> None:
    attach_mod = importlib.import_module("voicegateway.inference.session.attach")
    assert attach_mod._DEFAULT_TURN_FLUSH_SIZE == SchemaMetrics().turn_buffer_flush_size


def test_turn_buffer_flush_size_is_read_from_the_project(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOICEGW_CONFIG", _config(tmp_path, overlap_ms=250, flush_size=7))
    attach_mod = importlib.import_module("voicegateway.inference.session.attach")
    attach_mod._turn_flush_size_cache.clear()

    assert attach_mod._turn_flush_size("tuned") == 7
    # A project with no metrics block, and an unknown project, both fall back.
    assert attach_mod._turn_flush_size("nope") == attach_mod._DEFAULT_TURN_FLUSH_SIZE
    attach_mod._turn_flush_size_cache.clear()


def test_the_flush_size_reaches_the_tracker(tmp_path, monkeypatch) -> None:
    """Reading the number is not the same as using it."""
    monkeypatch.setenv("VOICEGW_CONFIG", _config(tmp_path, overlap_ms=250, flush_size=3))
    attach_mod = importlib.import_module("voicegateway.inference.session.attach")
    attach_mod._turn_flush_size_cache.clear()

    class _Sink:
        async def log_turns(self, rows):  # noqa: ANN001, ANN202
            return None

    tracker = attach_mod._build_turn_tracker(_Sink(), "tuned")
    assert tracker._flush_size == 3
    attach_mod._turn_flush_size_cache.clear()


def test_the_project_metrics_block_parses(tmp_path) -> None:
    cfg = GatewayConfig.load(_config(tmp_path, overlap_ms=250, flush_size=7))
    metrics = cfg.projects["tuned"].metrics
    assert metrics.talk_over_min_overlap_ms == 250
    assert metrics.turn_buffer_flush_size == 7


async def test_talk_over_threshold_changes_the_overlap_count(tmp_path) -> None:
    """The knob has to change the answer, not just reach the query.

    Two turns whose overlap is 50ms: counted at the 25ms threshold, not counted
    at 100ms. Under the old query, which counted any overlap at all, both cases
    returned 1.
    """
    from voicegateway.middleware.turn_tracker_middleware import TurnRow
    from voicegateway.services.storage_service import StorageService

    storage = StorageService(str(tmp_path / "overlap.db"))
    await storage._ensure_initialized()

    rows = [
        TurnRow(
            session_id="s",
            turn_index=0,
            caller_speak_start_ms=0,
            caller_speak_end_ms=500,
            agent_speak_start_ms=600,
            agent_speak_end_ms=1000,
        ),
        # Caller starts 50ms before the agent finished: a 50ms overlap.
        TurnRow(
            session_id="s",
            turn_index=1,
            caller_speak_start_ms=950,
            caller_speak_end_ms=1500,
            agent_speak_start_ms=1600,
            agent_speak_end_ms=2000,
        ),
    ]
    async with storage._conn.session() as db:
        await turns.create_turns_bulk(db, rows)

    async with storage._conn.session() as db:
        assert await turns.count_overlap_turns(db, "s", min_overlap_ms=25) == 1
        assert await turns.count_overlap_turns(db, "s", min_overlap_ms=100) == 0

    await storage.aclose()
