"""Contract tests for voicegateway.repository.replay (T05 of v0.3.0)."""

from __future__ import annotations

from voicegateway.middleware.replay_capture_middleware import ReplayEvent
from voicegateway.repository import replay_repository as replay
from voicegateway.services.storage_service import StorageService


def stt(session_id: str, t_ms: int, text: str) -> ReplayEvent:
    return ReplayEvent(
        session_id=session_id,
        modality="stt",
        t_ms=t_ms,
        payload={"text": text, "is_final": True, "alternatives": []},
        provider="deepgram",
        cost_usd=0.0001,
    )


def llm(session_id: str, t_ms: int, token: str) -> ReplayEvent:
    return ReplayEvent(
        session_id=session_id,
        modality="llm",
        t_ms=t_ms,
        payload={
            "token_text": token,
            "role": "assistant",
            "is_tool_invoke": False,
            "tool_args_partial": None,
        },
        provider="openai",
        cost_usd=0.00002,
    )


def _state(session_id: str, t_ms: int) -> ReplayEvent:
    return ReplayEvent(
        session_id=session_id,
        modality="state",
        t_ms=t_ms,
        payload={
            "system_prompt": "be helpful",
            "message_history": [],
            "tool_call_in_flight": None,
            "structured_output_collected": None,
        },
        provider="",
        cost_usd=None,
    )


async def _fresh_storage(tmp_path) -> StorageService:
    storage = StorageService(str(tmp_path / "replay.db"))
    await storage._ensure_initialized()
    return storage


async def test_bulk_write_round_trip(tmp_path) -> None:
    storage = await _fresh_storage(tmp_path)
    async with storage._conn.session() as db:
        events = [
            stt("s1", 100, "hello"),
            llm("s1", 500, "hi"),
            _state("s1", 510),
        ]
        n = await replay.bulk_write_events(db, events)
        assert n == 3

        listed = await replay.read_full_replay(db, "s1")
        assert len(listed) == 3
        assert [e.t_ms for e in listed] == [100, 500, 510]
        assert listed[0].modality == "stt"
        assert listed[1].modality == "llm"
        assert listed[2].modality == "state"


async def test_bulk_write_empty_is_noop(tmp_path) -> None:
    storage = await _fresh_storage(tmp_path)
    async with storage._conn.session() as db:
        n = await replay.bulk_write_events(db, [])
        assert n == 0


async def test_read_full_replay_unknown_session_empty(tmp_path) -> None:
    storage = await _fresh_storage(tmp_path)
    async with storage._conn.session() as db:
        assert await replay.read_full_replay(db, "ghost") == []


async def test_delete_replay_cascades_all_four_tables(tmp_path) -> None:
    storage = await _fresh_storage(tmp_path)
    async with storage._conn.session() as db:
        events = [
            stt("s1", 0, "a"),
            llm("s1", 100, "b"),
            _state("s1", 200),
        ]
        await replay.bulk_write_events(db, events)
        await replay.bulk_write_events(
            db,
            [
                ReplayEvent(
                    session_id="s1",
                    modality="tts",
                    t_ms=150,
                    payload={
                        "frame_duration_ms": 20,
                        "underrun": False,
                        "voice_id": "v1",
                    },
                    provider="cartesia",
                    cost_usd=0.00001,
                )
            ],
        )

        deleted = await replay.delete_replay(db, "s1")
        assert deleted == 4

        remaining = await replay.read_full_replay(db, "s1")
        assert remaining == []


async def test_aggregate_storage_per_session(tmp_path) -> None:
    storage = await _fresh_storage(tmp_path)
    async with storage._conn.session() as db:
        await replay.bulk_write_events(db, [stt("s1", 0, "hello world")])
        size = await replay.aggregate_storage_per_session(db, "s1")
        assert 30 <= size <= 500
