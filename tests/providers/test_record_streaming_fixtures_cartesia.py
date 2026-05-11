"""End-to-end tests for the Cartesia TTS batch recorder.

Mocks Cartesia's /tts/bytes HTTP endpoint with respx, drives the
batch path, and asserts the produced fixture validates against
StreamingFixture and carries the right ``character_count`` ground
truth (len(DEFAULT_TTS_TEXT)) plus a catalog-matched
``expected_cost_usd``.
"""

from __future__ import annotations

import base64
import importlib.util
import json
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
RECORDER_PATH = REPO_ROOT / "scripts" / "record_streaming_fixtures.py"


def _import_recorder() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_phase3_recorder_under_test_ct", RECORDER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_phase3_recorder_under_test_ct"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def recorder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    mod = _import_recorder()
    monkeypatch.setattr(mod, "FIXTURES_DIR", tmp_path)
    monkeypatch.setenv("CARTESIA_API_KEY", "fake-cartesia-key-for-tests")
    monkeypatch.delenv("CARTESIA_VOICE_ID", raising=False)
    return mod


def _fake_audio_bytes() -> bytes:
    """Stand-in for Cartesia's PCM audio response (size matters, content does not)."""
    return b"\x00\x01" * 8000  # 16 KB of arbitrary 16-bit samples


# ---------- batch happy path ------------------------------------------------


async def test_cartesia_batch_recording_writes_valid_fixture(
    recorder: ModuleType, tmp_path: Path
) -> None:
    audio = _fake_audio_bytes()
    async with respx.mock(assert_all_called=True) as router:
        router.post("https://api.cartesia.ai/tts/bytes").mock(
            return_value=httpx.Response(
                200,
                content=audio,
                headers={"content-type": "audio/raw"},
            )
        )
        fixture_path = await recorder._run("cartesia", "tts", "sonic-3", "batch")

    assert fixture_path.parent == tmp_path
    assert fixture_path.name.startswith("cartesia_sonic-3_tts_batch_")
    assert fixture_path.name.endswith(".json")

    fixture = load_fixture(fixture_path)
    assert fixture.metadata.provider == "cartesia"
    assert fixture.metadata.model == "sonic-3"
    assert fixture.metadata.modality == "tts"
    assert fixture.metadata.mode == "batch"

    expected_chars = len(recorder.DEFAULT_TTS_TEXT)
    assert fixture.provider_reported_usage == {"character_count": expected_chars}

    assert len(fixture.response_stream) == 1
    chunk = fixture.response_stream[0]
    assert chunk.data["audio_size_bytes"] == len(audio)
    assert chunk.data["encoding"] == "pcm_s16le"
    assert chunk.data["sample_rate"] == 16000


async def test_cartesia_batch_expected_cost_matches_catalog(
    recorder: ModuleType,
) -> None:
    """expected_cost_usd is computed via the TTS catalog at recording time."""
    from voicegateway.pricing.catalog import calculate_cost

    audio = _fake_audio_bytes()
    async with respx.mock(assert_all_called=True) as router:
        router.post("https://api.cartesia.ai/tts/bytes").mock(
            return_value=httpx.Response(200, content=audio)
        )
        fixture_path = await recorder._run("cartesia", "tts", "sonic-3", "batch")

    fixture = load_fixture(fixture_path)
    expected = calculate_cost(
        "tts",
        "cartesia/sonic-3",
        character_count=len(recorder.DEFAULT_TTS_TEXT),
    )
    assert expected is not None
    quantized = expected.quantize(Decimal("0.00000001"))
    assert fixture.expected_cost_usd == quantized


async def test_cartesia_batch_request_carries_correct_headers_and_body(
    recorder: ModuleType,
) -> None:
    """Recorder must send X-API-Key, Cartesia-Version, and the canonical body."""
    async with respx.mock(assert_all_called=True) as router:
        route = router.post("https://api.cartesia.ai/tts/bytes").mock(
            return_value=httpx.Response(200, content=_fake_audio_bytes())
        )
        await recorder._run("cartesia", "tts", "sonic-3", "batch")

    request = route.calls.last.request
    assert request.headers["x-api-key"] == "fake-cartesia-key-for-tests"
    assert request.headers["cartesia-version"] == "2024-06-10"
    assert request.headers["content-type"].startswith("application/json")

    body = json.loads(request.content)
    assert body["model_id"] == "sonic-3"
    assert body["transcript"] == recorder.DEFAULT_TTS_TEXT
    assert body["voice"]["mode"] == "id"
    assert body["output_format"]["encoding"] == "pcm_s16le"
    assert body["output_format"]["sample_rate"] == 16000


async def test_cartesia_batch_uses_voice_id_env_override(
    recorder: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CARTESIA_VOICE_ID env var overrides the script's default voice id."""
    monkeypatch.setenv("CARTESIA_VOICE_ID", "test-voice-id-override")
    async with respx.mock(assert_all_called=True) as router:
        route = router.post("https://api.cartesia.ai/tts/bytes").mock(
            return_value=httpx.Response(200, content=_fake_audio_bytes())
        )
        await recorder._run("cartesia", "tts", "sonic-3", "batch")

    body = json.loads(route.calls.last.request.content)
    assert body["voice"]["id"] == "test-voice-id-override"


async def test_cartesia_batch_uses_default_voice_id_when_env_unset(
    recorder: ModuleType,
) -> None:
    """No CARTESIA_VOICE_ID -> script's hard-coded default lands in the request."""
    async with respx.mock(assert_all_called=True) as router:
        route = router.post("https://api.cartesia.ai/tts/bytes").mock(
            return_value=httpx.Response(200, content=_fake_audio_bytes())
        )
        await recorder._run("cartesia", "tts", "sonic-3", "batch")

    body = json.loads(route.calls.last.request.content)
    assert body["voice"]["id"] == recorder._CARTESIA_DEFAULT_VOICE_ID


async def test_cartesia_batch_records_request_block_in_fixture(
    recorder: ModuleType,
) -> None:
    """Fixture's request block holds the literal payload sent to Cartesia."""
    async with respx.mock(assert_all_called=True) as router:
        router.post("https://api.cartesia.ai/tts/bytes").mock(
            return_value=httpx.Response(200, content=_fake_audio_bytes())
        )
        fixture_path = await recorder._run("cartesia", "tts", "sonic-3", "batch")

    fixture = load_fixture(fixture_path)
    assert fixture.request["model_id"] == "sonic-3"
    assert fixture.request["transcript"] == recorder.DEFAULT_TTS_TEXT
    assert fixture.request["voice"]["mode"] == "id"
    assert fixture.request["output_format"] == recorder._CARTESIA_OUTPUT_FORMAT


# ---------- batch refusal paths --------------------------------------------


async def test_cartesia_batch_refuses_on_empty_audio(
    recorder: ModuleType,
) -> None:
    """An empty audio response is unanchored; refuse to write a fixture."""
    async with respx.mock(assert_all_called=True) as router:
        router.post("https://api.cartesia.ai/tts/bytes").mock(
            return_value=httpx.Response(200, content=b"")
        )
        with pytest.raises(RuntimeError, match="zero audio bytes"):
            await recorder._run("cartesia", "tts", "sonic-3", "batch")


async def test_cartesia_batch_propagates_http_error(
    recorder: ModuleType,
) -> None:
    """4xx/5xx surfaces as httpx.HTTPStatusError, not a silent skip."""
    async with respx.mock(assert_all_called=True) as router:
        router.post("https://api.cartesia.ai/tts/bytes").mock(
            return_value=httpx.Response(401, json={"error": "unauthorized"})
        )
        with pytest.raises(httpx.HTTPStatusError):
            await recorder._run("cartesia", "tts", "sonic-3", "batch")


async def test_cartesia_recorder_requires_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _import_recorder()
    monkeypatch.delenv("CARTESIA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="CARTESIA_API_KEY"):
        await mod._record_cartesia_tts("sonic-3", "batch")


# ---------- stream happy path ----------------------------------------------


class _FakeCartesiaWS:
    """In-process fake of a Cartesia TTS WebSocket.

    Records every JSON request the recorder sends and yields a
    pre-canned set of text messages on iteration. Mirrors the
    Deepgram fake's interface intentionally; same shape, different
    payloads.
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
            yield json.dumps(msg)

    @property
    def text_frames(self) -> list[dict[str, Any]]:
        return [json.loads(f) for f in self.sent if isinstance(f, str)]


def _patch_cartesia_ws(
    recorder: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    fake: _FakeCartesiaWS,
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    @asynccontextmanager
    async def _fake_cm() -> Any:
        yield fake

    def _factory(url: str) -> Any:
        captured["url"] = url
        return _fake_cm()

    monkeypatch.setattr(recorder, "_open_cartesia_websocket", _factory)
    return captured


def _cartesia_chunk(audio_bytes: bytes, step_time: float = 0.0) -> dict[str, Any]:
    return {
        "type": "chunk",
        "data": base64.b64encode(audio_bytes).decode("ascii"),
        "step_time": step_time,
        "context_id": "fixture-context",
    }


def _cartesia_stream_responses() -> list[dict[str, Any]]:
    """Three audio chunks followed by a done message."""
    return [
        _cartesia_chunk(b"\x00\x01" * 1000, step_time=0.5),
        _cartesia_chunk(b"\x02\x03" * 1000, step_time=1.0),
        _cartesia_chunk(b"\x04\x05" * 1000, step_time=1.5),
        {"type": "done", "context_id": "fixture-context"},
    ]


async def test_cartesia_stream_recording_writes_valid_fixture(
    recorder: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeCartesiaWS(_cartesia_stream_responses())
    captured = _patch_cartesia_ws(recorder, monkeypatch, fake)

    fixture_path = await recorder._run("cartesia", "tts", "sonic-3", "stream")

    assert captured["url"].startswith("wss://api.cartesia.ai/tts/websocket?")
    assert "cartesia_version=2024-06-10" in captured["url"]
    assert "api_key=fake-cartesia-key-for-tests" in captured["url"]

    fixture = load_fixture(fixture_path)
    assert fixture.metadata.provider == "cartesia"
    assert fixture.metadata.model == "sonic-3"
    assert fixture.metadata.modality == "tts"
    assert fixture.metadata.mode == "stream"

    # 3 chunks + 1 done message = 4 fixture chunks.
    assert len(fixture.response_stream) == 4
    types = [c.data["type"] for c in fixture.response_stream]
    assert types == ["chunk", "chunk", "chunk", "done"]
    indices = [c.chunk_index for c in fixture.response_stream]
    assert indices == [0, 1, 2, 3]

    assert fixture.provider_reported_usage == {
        "character_count": len(recorder.DEFAULT_TTS_TEXT)
    }


async def test_cartesia_stream_sends_single_request_with_context_id(
    recorder: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recorder sends exactly one request JSON, with a fresh context_id."""
    fake = _FakeCartesiaWS(_cartesia_stream_responses())
    _patch_cartesia_ws(recorder, monkeypatch, fake)

    await recorder._record_cartesia_tts("sonic-3", "stream")

    text_msgs = fake.text_frames
    assert len(text_msgs) == 1, (
        f"Expected one request frame, got {len(text_msgs)}: {text_msgs!r}"
    )
    request = text_msgs[0]
    assert request["model_id"] == "sonic-3"
    assert request["transcript"] == recorder.DEFAULT_TTS_TEXT
    assert request["voice"]["mode"] == "id"
    assert request["output_format"] == recorder._CARTESIA_OUTPUT_FORMAT
    assert request["continue"] is False
    assert isinstance(request["context_id"], str)
    assert len(request["context_id"]) > 0


async def test_cartesia_stream_request_block_in_fixture_carries_context_id(
    recorder: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fixture's request block records the literal request, including context_id."""
    fake = _FakeCartesiaWS(_cartesia_stream_responses())
    _patch_cartesia_ws(recorder, monkeypatch, fake)

    fixture_path = await recorder._run("cartesia", "tts", "sonic-3", "stream")

    fixture = load_fixture(fixture_path)
    assert "context_id" in fixture.request
    assert fixture.request["continue"] is False


async def test_cartesia_stream_expected_cost_matches_catalog(
    recorder: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    from voicegateway.pricing.catalog import calculate_cost

    fake = _FakeCartesiaWS(_cartesia_stream_responses())
    _patch_cartesia_ws(recorder, monkeypatch, fake)

    fixture_path = await recorder._run("cartesia", "tts", "sonic-3", "stream")

    fixture = load_fixture(fixture_path)
    expected = calculate_cost(
        "tts",
        "cartesia/sonic-3",
        character_count=len(recorder.DEFAULT_TTS_TEXT),
    )
    assert expected is not None
    quantized = expected.quantize(Decimal("0.00000001"))
    assert fixture.expected_cost_usd == quantized


async def test_cartesia_stream_refuses_when_done_never_arrives(
    recorder: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No done message means the stream is unterminated; refuse to write."""
    truncated = [m for m in _cartesia_stream_responses() if m.get("type") != "done"]
    fake = _FakeCartesiaWS(truncated)
    _patch_cartesia_ws(recorder, monkeypatch, fake)

    with pytest.raises(RuntimeError, match="done"):
        await recorder._record_cartesia_tts("sonic-3", "stream")


async def test_cartesia_stream_refuses_when_no_messages_received(
    recorder: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty server stream -> RuntimeError, no fixture written."""
    fake = _FakeCartesiaWS([])
    _patch_cartesia_ws(recorder, monkeypatch, fake)

    with pytest.raises(RuntimeError):
        await recorder._record_cartesia_tts("sonic-3", "stream")


# ---------- prompt stability guard -----------------------------------------


def test_default_tts_text_is_stable(recorder: ModuleType) -> None:
    """Changing this string changes character_count; re-record fixtures if you do."""
    assert recorder.DEFAULT_TTS_TEXT == (
        "Hello, this is the canonical voicegateway phase 3 fixture."
    )
    # Snapshot the length so accidental whitespace changes get caught.
    assert len(recorder.DEFAULT_TTS_TEXT) == 58
