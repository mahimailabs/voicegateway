"""Storage-cost smoke test for v0.3.0 conversation replay."""

from __future__ import annotations

from voicegateway.middleware.replay_capture_middleware import ReplayCapture, ReplayEvent
from voicegateway.repository import replay_repository as replay
from voicegateway.services.storage_service import StorageService

# Generous upper bound. The actual measured size for the synthetic
# conversation below sits well under this; the test fails loudly if
# storage explodes (e.g. payload schema regresses to dump huge JSON,
# index overhead spikes).
_MAX_REPLAY_BYTES_PER_MINUTE = 600 * 1024  # 600 KB


async def _synthesize_one_minute(capture: ReplayCapture, session_id: str) -> None:
    """Push a realistic 60-second conversation through ReplayCapture."""
    base_ts = 0
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
    storage = StorageService(db_path)
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
    async with storage._conn.session() as db:
        n = await replay.bulk_write_events(db, captured, tenant_id=None)
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
