"""Phase 2b: read a probe room's request rows back by ``metadata.room``.

``voicegw livekit latency`` correlates a throwaway probe room with the STT /
LLM / TTS + turn-detection rows an instrumented agent wrote. There is no room
column, so the repo pre-filters on the serialized ``metadata`` JSON with a
``LIKE`` and then rejects incidental substring hits with an exact parsed match.
These round-trip through real SQLite to prove both halves.
"""

from __future__ import annotations

import time
import uuid

from voicegateway.livekit_diag.latency import aggregate_components
from voicegateway.models.request_model import RequestRecord
from voicegateway.services.storage_service import StorageService


def _rec(modality: str, *, room: str | None, ttfb: float | None = None, eou=None):
    metadata: dict = {}
    if room is not None:
        metadata["room"] = room
    if eou is not None:
        metadata["eou"] = eou
    return RequestRecord(
        id=str(uuid.uuid4()),
        timestamp=time.time(),
        modality=modality,
        model_id="openai/gpt-4o-mini" if modality == "llm" else "",
        provider="openai" if modality == "llm" else "",
        project="fleet",
        ttfb_ms=ttfb,
        metadata=metadata,
        session_id=f"vg-{uuid.uuid4()}",
    )


async def test_get_requests_for_room_returns_only_that_room(tmp_path):
    storage = StorageService(str(tmp_path / "room.db"))
    await storage.log_request(
        _rec(
            "eou",
            room="vg-probe-a",
            eou={"end_of_utterance_delay": 0.30, "transcription_delay": 0.08},
        )
    )
    await storage.log_request(_rec("stt", room="vg-probe-a", ttfb=120.0))
    await storage.log_request(_rec("llm", room="vg-probe-a", ttfb=450.0))
    await storage.log_request(_rec("tts", room="vg-probe-a", ttfb=90.0))
    await storage.log_request(_rec("llm", room="vg-probe-b", ttfb=999.0))
    await storage.log_request(_rec("llm", room=None, ttfb=111.0))

    rows = await storage.get_requests_for_room("vg-probe-a")
    assert len(rows) == 4
    assert all(r["metadata"]["room"] == "vg-probe-a" for r in rows)

    # Full read-back: the split aggregates to the expected seconds. The eou row's
    # transcription_delay surfaces as the STT tail alongside the per-call ttfb.
    assert aggregate_components(rows) == {
        "eou": 0.30,
        "stt": 0.12,
        "stt_transcription_delay": 0.08,
        "llm_ttft": 0.45,
        "tts": 0.09,
    }


async def test_get_requests_for_room_exact_match_rejects_like_wildcard(tmp_path):
    """A ``_`` in the queried room is a LIKE wildcard; the exact parsed match
    must still reject a stored room that only matches via that wildcard."""
    storage = StorageService(str(tmp_path / "wild.db"))
    await storage.log_request(_rec("llm", room="vgXprobe", ttfb=100.0))

    # 'vg_probe' -> LIKE '%"room": "vg_probe"%' matches 'vgXprobe' (_ = any char)
    rows = await storage.get_requests_for_room("vg_probe")
    assert rows == []  # exact match rejects the wildcard hit


async def test_get_requests_for_room_empty_when_absent(tmp_path):
    storage = StorageService(str(tmp_path / "none.db"))
    await storage.log_request(_rec("llm", room="vg-probe-a", ttfb=100.0))
    assert await storage.get_requests_for_room("nope") == []
