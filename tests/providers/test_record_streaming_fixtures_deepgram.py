"""End-to-end tests for the Deepgram STT batch recorder.

Mocks Deepgram's pre-recorded HTTP endpoint with respx, exercises
the batch path, and asserts the produced fixture validates against
StreamingFixture and carries the right ``audio_seconds`` ground
truth plus a catalog-matched ``expected_cost_usd``.

The recorder reads a bundled WAV under
``tests/fixtures/audio/test_sample.wav``. Tests verify the file
exists in the repo so this dependency is committed alongside the
script.
"""

from __future__ import annotations

import importlib.util
import json as _json
import sys
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import respx

from tests.fixtures.streaming._loader import load_fixture

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RECORDER_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "streaming" / "record_streaming_fixtures.py"
)
AUDIO_SAMPLE = REPO_ROOT / "tests" / "fixtures" / "audio" / "test_sample.wav"


def _import_recorder() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_phase3_recorder_under_test_dg", RECORDER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_phase3_recorder_under_test_dg"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def recorder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    mod = _import_recorder()
    monkeypatch.setattr(mod, "FIXTURES_DIR", tmp_path)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "fake-deepgram-key-for-tests")
    return mod


def _deepgram_batch_response(duration_seconds: float = 3.0) -> dict:
    """A realistic Deepgram pre-recorded API JSON response."""
    return {
        "metadata": {
            "transaction_key": "deprecated",
            "request_id": "fixture-test-request",
            "sha256": "0" * 64,
            "created": "2026-05-04T14:32:11.000Z",
            "duration": duration_seconds,
            "channels": 1,
            "models": ["fixture-test-model"],
            "model_info": {},
        },
        "results": {
            "channels": [
                {
                    "alternatives": [
                        {
                            "transcript": "",
                            "confidence": 0.0,
                            "words": [],
                        }
                    ]
                }
            ]
        },
    }


# ---------- repo-state guards -----------------------------------------------


def test_audio_sample_is_committed() -> None:
    """The bundled WAV is the recorder's audio source; missing it breaks 3.4."""
    assert AUDIO_SAMPLE.exists(), (
        f"Expected {AUDIO_SAMPLE} to be committed; without it the "
        "Deepgram recorder cannot run."
    )
    # Sanity: 3 seconds at 8 kHz mono 16-bit PCM has a known size,
    # plus a 44-byte WAV header.
    expected_size = 3 * 8000 * 2 + 44
    assert AUDIO_SAMPLE.stat().st_size == expected_size, (
        f"test_sample.wav size {AUDIO_SAMPLE.stat().st_size} does not "
        f"match the documented 3s @ 8 kHz mono 16-bit shape ({expected_size} bytes)."
    )


def test_audio_sample_is_valid_riff_wav() -> None:
    """Header check so a corrupted file does not silently break recording."""
    header = AUDIO_SAMPLE.read_bytes()[:12]
    assert header[:4] == b"RIFF"
    assert header[8:12] == b"WAVE"


# ---------- batch happy path ------------------------------------------------


async def test_deepgram_batch_recording_writes_valid_fixture(
    recorder: ModuleType, tmp_path: Path
) -> None:
    response_payload = _deepgram_batch_response(duration_seconds=3.0)
    async with respx.mock(assert_all_called=True) as router:
        router.post("https://api.deepgram.com/v1/listen").mock(
            return_value=httpx.Response(200, json=response_payload)
        )
        fixture_path = await recorder._run("deepgram", "stt", "nova-3", "batch")

    assert fixture_path.parent == tmp_path
    assert fixture_path.name.startswith("deepgram_nova-3_stt_batch_")
    assert fixture_path.name.endswith(".json")

    fixture = load_fixture(fixture_path)
    assert fixture.metadata.provider == "deepgram"
    assert fixture.metadata.model == "nova-3"
    assert fixture.metadata.modality == "stt"
    assert fixture.metadata.mode == "batch"

    usage = fixture.provider_reported_usage
    assert usage == {"audio_seconds": 3.0}

    assert len(fixture.response_stream) == 1
    chunk = fixture.response_stream[0]
    assert chunk.chunk_index == 0
    assert chunk.received_at_ms == 0


async def test_deepgram_batch_expected_cost_matches_catalog(
    recorder: ModuleType,
) -> None:
    """expected_cost_usd is computed at recording time via the STT catalog."""
    from voicegateway.pricing.catalog import calculate_cost

    response_payload = _deepgram_batch_response(duration_seconds=3.0)
    async with respx.mock(assert_all_called=True) as router:
        router.post("https://api.deepgram.com/v1/listen").mock(
            return_value=httpx.Response(200, json=response_payload)
        )
        fixture_path = await recorder._run("deepgram", "stt", "nova-3", "batch")

    fixture = load_fixture(fixture_path)
    expected = calculate_cost("stt", "deepgram/nova-3", audio_seconds=3.0)
    assert expected is not None
    quantized = expected.quantize(Decimal("0.00000001"))
    assert fixture.expected_cost_usd == quantized


async def test_deepgram_batch_request_carries_model_query_param(
    recorder: ModuleType,
) -> None:
    """The recorder must select the model via the ?model= query string."""
    response_payload = _deepgram_batch_response()
    async with respx.mock(assert_all_called=True) as router:
        route = router.post("https://api.deepgram.com/v1/listen").mock(
            return_value=httpx.Response(200, json=response_payload)
        )
        await recorder._run("deepgram", "stt", "nova-3", "batch")

    request = route.calls.last.request
    assert "model=nova-3" in str(request.url)
    assert request.headers["authorization"].startswith("Token ")
    assert request.headers["content-type"] == "audio/wav"


async def test_deepgram_batch_request_uploads_audio_bytes(
    recorder: ModuleType,
) -> None:
    """POST body is the WAV bytes verbatim, not a wrapping JSON."""
    response_payload = _deepgram_batch_response()
    async with respx.mock(assert_all_called=True) as router:
        route = router.post("https://api.deepgram.com/v1/listen").mock(
            return_value=httpx.Response(200, json=response_payload)
        )
        await recorder._run("deepgram", "stt", "nova-3", "batch")

    sent_body = route.calls.last.request.content
    expected_bytes = AUDIO_SAMPLE.read_bytes()
    assert sent_body == expected_bytes


async def test_deepgram_batch_records_audio_path_in_request_block(
    recorder: ModuleType,
) -> None:
    """Fixture's request block points at the audio source so replay tests can find it."""
    response_payload = _deepgram_batch_response(duration_seconds=3.0)
    async with respx.mock(assert_all_called=True) as router:
        router.post("https://api.deepgram.com/v1/listen").mock(
            return_value=httpx.Response(200, json=response_payload)
        )
        fixture_path = await recorder._run("deepgram", "stt", "nova-3", "batch")

    fixture = load_fixture(fixture_path)
    assert fixture.request["model"] == "nova-3"
    assert fixture.request["audio_path"] == "tests/fixtures/audio/test_sample.wav"
    assert fixture.request["audio_format"] == "wav"
    assert fixture.request["audio_size_bytes"] == AUDIO_SAMPLE.stat().st_size


# ---------- batch refusal paths --------------------------------------------


async def test_deepgram_batch_refuses_when_duration_missing(
    recorder: ModuleType,
) -> None:
    """A response without metadata.duration is unanchored; refuse to write."""
    response_payload = _deepgram_batch_response(duration_seconds=3.0)
    response_payload["metadata"].pop("duration")
    async with respx.mock(assert_all_called=True) as router:
        router.post("https://api.deepgram.com/v1/listen").mock(
            return_value=httpx.Response(200, json=response_payload)
        )
        with pytest.raises(RuntimeError, match="duration"):
            await recorder._run("deepgram", "stt", "nova-3", "batch")


async def test_deepgram_batch_propagates_http_error(
    recorder: ModuleType,
) -> None:
    """A 4xx/5xx from Deepgram surfaces as an httpx HTTPStatusError."""
    async with respx.mock(assert_all_called=True) as router:
        router.post("https://api.deepgram.com/v1/listen").mock(
            return_value=httpx.Response(401, json={"error": "unauthorized"})
        )
        with pytest.raises(httpx.HTTPStatusError):
            await recorder._run("deepgram", "stt", "nova-3", "batch")


async def test_deepgram_recorder_requires_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _import_recorder()
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPGRAM_API_KEY"):
        await mod._record_deepgram_stt("nova-3", "batch")


async def test_deepgram_recorder_requires_audio_sample(
    recorder: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the bundled WAV is missing, the recorder refuses to run."""
    monkeypatch.setattr(recorder, "AUDIO_SAMPLE_PATH", tmp_path / "missing.wav")
    with pytest.raises(RuntimeError, match="missing"):
        await recorder._record_deepgram_stt("nova-3", "batch")


# ---------- stream happy path ----------------------------------------------


class _FakeDeepgramWS:
    """In-process fake of a Deepgram live WebSocket.

    Records every frame the recorder sends, yields a pre-canned set
    of JSON text messages when iterated, and lets tests drive the
    full producer / consumer flow without any real network.
    """

    def __init__(self, responses: list[dict[str, Any]]):
        self.sent: list[Any] = []
        self._responses = list(responses)

    async def send(self, frame: Any) -> None:
        self.sent.append(frame)

    def __aiter__(self) -> Any:
        return self._messages()

    async def _messages(self) -> Any:
        for msg in self._responses:
            yield _json.dumps(msg)

    @property
    def binary_frames(self) -> list[bytes]:
        return [f for f in self.sent if isinstance(f, bytes | bytearray)]

    @property
    def text_frames(self) -> list[dict[str, Any]]:
        return [_json.loads(f) for f in self.sent if isinstance(f, str)]


def _patch_deepgram_ws(
    recorder: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    fake: _FakeDeepgramWS,
) -> dict[str, Any]:
    """Replace _open_deepgram_websocket so the recorder uses the fake.

    Captures the (url, api_key) arguments for assertion.
    """
    captured: dict[str, Any] = {}

    @asynccontextmanager
    async def _fake_cm() -> Any:
        yield fake

    def _factory(url: str, api_key: str) -> Any:
        captured["url"] = url
        captured["api_key"] = api_key
        return _fake_cm()

    monkeypatch.setattr(recorder, "_open_deepgram_websocket", _factory)
    return captured


def _stream_responses(duration_seconds: float = 3.0) -> list[dict[str, Any]]:
    """Build the canonical Deepgram live stream message sequence."""
    return [
        {
            "type": "SpeechStarted",
            "channel": [0],
            "timestamp": 0.0,
        },
        {
            "type": "Results",
            "channel_index": [0, 1],
            "duration": duration_seconds,
            "start": 0.0,
            "is_final": True,
            "channel": {
                "alternatives": [{"transcript": "", "confidence": 0.0, "words": []}]
            },
        },
        {
            "type": "UtteranceEnd",
            "channel": [0],
            "last_word_end": duration_seconds,
        },
        {
            "type": "Metadata",
            "request_id": "deepgram-stream-fixture",
            "duration": duration_seconds,
            "channels": 1,
        },
    ]


async def test_deepgram_stream_recording_writes_valid_fixture(
    recorder: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeDeepgramWS(_stream_responses(duration_seconds=3.0))
    captured = _patch_deepgram_ws(recorder, monkeypatch, fake)

    fixture_path = await recorder._run("deepgram", "stt", "nova-3", "stream")

    # WebSocket URL carries model + PCM params per design.
    assert captured["url"].startswith("wss://api.deepgram.com/v1/listen?")
    assert "model=nova-3" in captured["url"]
    assert "encoding=linear16" in captured["url"]
    assert "sample_rate=8000" in captured["url"]
    assert "channels=1" in captured["url"]
    assert captured["api_key"] == "fake-deepgram-key-for-tests"

    fixture = load_fixture(fixture_path)
    assert fixture.metadata.provider == "deepgram"
    assert fixture.metadata.model == "nova-3"
    assert fixture.metadata.modality == "stt"
    assert fixture.metadata.mode == "stream"

    # 4 messages canned -> 4 chunks captured.
    assert len(fixture.response_stream) == 4
    indices = [c.chunk_index for c in fixture.response_stream]
    assert indices == [0, 1, 2, 3]
    types = [c.data["type"] for c in fixture.response_stream]
    assert types == ["SpeechStarted", "Results", "UtteranceEnd", "Metadata"]

    assert fixture.provider_reported_usage == {"audio_seconds": 3.0}


async def test_deepgram_stream_sends_pcm_audio_in_100ms_chunks(
    recorder: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audio is sent as raw PCM (no 44-byte WAV header), 100ms chunks at 8 kHz."""
    fake = _FakeDeepgramWS(_stream_responses())
    _patch_deepgram_ws(recorder, monkeypatch, fake)

    await recorder._record_deepgram_stt("nova-3", "stream")

    binary = fake.binary_frames
    # 3 seconds of 8 kHz 16-bit mono = 48000 bytes PCM.
    # Chunk size is (8000 * 2) / 10 = 1600 bytes.
    assert sum(len(b) for b in binary) == 48000
    # All but the last frame should be exactly one 100ms chunk.
    expected_chunks = 48000 // 1600
    assert len(binary) == expected_chunks
    assert all(len(b) == 1600 for b in binary)


async def test_deepgram_stream_sends_finalize_then_close(
    recorder: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recorder must signal Finalize before reading + CloseStream after Metadata."""
    fake = _FakeDeepgramWS(_stream_responses())
    _patch_deepgram_ws(recorder, monkeypatch, fake)

    await recorder._record_deepgram_stt("nova-3", "stream")

    text_msgs = fake.text_frames
    types_in_order = [m.get("type") for m in text_msgs]
    assert types_in_order == ["Finalize", "CloseStream"], (
        f"Expected Finalize then CloseStream control frames, got {types_in_order!r}"
    )


async def test_deepgram_stream_expected_cost_matches_catalog(
    recorder: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    from voicegateway.pricing.catalog import calculate_cost

    fake = _FakeDeepgramWS(_stream_responses(duration_seconds=3.0))
    _patch_deepgram_ws(recorder, monkeypatch, fake)

    fixture_path = await recorder._run("deepgram", "stt", "nova-3", "stream")
    fixture = load_fixture(fixture_path)
    expected = calculate_cost("stt", "deepgram/nova-3", audio_seconds=3.0)
    assert expected is not None
    quantized = expected.quantize(Decimal("0.00000001"))
    assert fixture.expected_cost_usd == quantized


# ---------- stream refusal paths -------------------------------------------


async def test_deepgram_stream_refuses_when_metadata_never_arrives(
    recorder: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No Metadata message -> RuntimeError, no fixture written."""
    truncated = [r for r in _stream_responses() if r["type"] != "Metadata"]
    fake = _FakeDeepgramWS(truncated)
    _patch_deepgram_ws(recorder, monkeypatch, fake)

    with pytest.raises(RuntimeError, match="Metadata"):
        await recorder._record_deepgram_stt("nova-3", "stream")


async def test_deepgram_stream_refuses_when_duration_missing(
    recorder: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Metadata without duration -> RuntimeError."""
    responses = _stream_responses()
    responses[-1].pop("duration")
    fake = _FakeDeepgramWS(responses)
    _patch_deepgram_ws(recorder, monkeypatch, fake)

    with pytest.raises(RuntimeError, match="Metadata"):
        await recorder._record_deepgram_stt("nova-3", "stream")


async def test_deepgram_stream_refuses_when_no_messages_received(
    recorder: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty server stream -> RuntimeError."""
    fake = _FakeDeepgramWS([])
    _patch_deepgram_ws(recorder, monkeypatch, fake)

    with pytest.raises(RuntimeError):
        await recorder._record_deepgram_stt("nova-3", "stream")
