"""Storage-cost smoke test for v0.3.0 conversation replay."""

from __future__ import annotations

from voicegateway.middleware.replay_capture import ReplayCapture, ReplayEvent
from voicegateway.repository import replay_repository as replay
from voicegateway.storage.sqlite import SQLiteStorage

# Generous upper bound. The actual measured size for the synthetic
# conversation below sits well under this; the test fails loudly if
# storage explodes (e.g. payload schema regresses to dump huge JSON,
# index overhead spikes).
_MAX_REPLAY_BYTES_PER_MINUTE = 600 * 1024  # 600 KB


async def _synthesize_one_minute(capture: ReplayCapture, session_id: str) -> None:
    """Push a realistic 60-second conversation through ReplayCapture."""
    base_ts = 0
    for i in range(40):
        await capture.record_stt_chunk(
            text=f"transcript chunk number {i} hello there",
            is_final=(i % 4 == 3),
            alternatives=[],
            provider="deepgram",
            cost_usd=0.0001,
            session_id=session_id,
            at_ms=base_ts + i * 1500,
        )
    for i in range(400):
        await capture.record_llm_token(
            token_text=f"tok{i:03d}",
            role="assistant",
            is_tool_invoke=False,
            tool_args_partial=None,
            provider="openai",
            cost_usd=0.00002,
            session_id=session_id,
            at_ms=base_ts + i * 150,
        )
    for i in range(1200):
        await capture.record_tts_frame(
            frame_duration_ms=50,
            underrun=False,
            voice_id="sonic-3",
            provider="cartesia",
            cost_usd=0.000005,
            session_id=session_id,
            at_ms=base_ts + i * 50,
        )
    for i in range(60):
        await capture.record_state_snapshot(
            {
                "system_prompt": "be a helpful agent",
                "message_history": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello, how can I help?"},
                ],
                "tool_call_in_flight": None,
                "structured_output_collected": None,
            },
            session_id=session_id,
            at_ms=base_ts + i * 1000,
        )


async def test_synthetic_one_minute_under_600kb(tmp_path) -> None:
    """End-to-end: ReplayCapture -> replay -> on-disk footprint."""
    db_path = str(tmp_path / "smoke.db")
    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()

    captured: list[ReplayEvent] = []

    async def flush(events: list[ReplayEvent]) -> None:
        captured.extend(events)

    capture = ReplayCapture(
        flush_callback=flush,
        flush_size_events=10000,
        buffer_size_events=20000,
    )

    await _synthesize_one_minute(capture, "smoke-session")
    await capture.close_session("smoke-session")

    # Persist the captured events via the real repo.
    import aiosqlite

    async with aiosqlite.connect(db_path) as db:
        n = await replay.bulk_write_events(db, captured)
        assert n == len(captured)

        # Sum the payload bytes (the dominant cost term).
        size = await replay.aggregate_storage_per_session(db, "smoke-session")

    # 60-second synthetic conversation should land under 600 KB/min.
    # Documented target in docs/storage/replay-storage-costs.md.
    assert size < _MAX_REPLAY_BYTES_PER_MINUTE, (
        f"Replay storage footprint exploded: {size} bytes for 60s of "
        f"conversation (> 600 KB). If this fails consistently, OQ1's "
        f"fallback is documented in T07's ProjectConfig "
        f"replay.enabled toggle."
    )


async def test_storage_size_reported_via_aggregate(tmp_path) -> None:
    """The aggregate function reports the same byte sum the smoke uses."""
    db_path = str(tmp_path / "smoke2.db")
    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()

    captured: list[ReplayEvent] = []

    async def flush(events: list[ReplayEvent]) -> None:
        captured.extend(events)

    capture = ReplayCapture(
        flush_callback=flush,
        flush_size_events=10000,
        buffer_size_events=20000,
    )
    # Smaller synthetic: just 10 STT chunks.
    for i in range(10):
        await capture.record_stt_chunk(
            text=f"chunk-{i}",
            is_final=True,
            provider="deepgram",
            cost_usd=0.0001,
            session_id="tiny",
            at_ms=i * 100,
        )
    await capture.close_session("tiny")

    import aiosqlite

    async with aiosqlite.connect(db_path) as db:
        await replay.bulk_write_events(db, captured)
        size = await replay.aggregate_storage_per_session(db, "tiny")

    # 10 small STT chunks: each payload roughly 50 bytes JSON-encoded,
    # so 500 bytes total floor, a few KB ceiling.
    assert 100 < size < 5000
