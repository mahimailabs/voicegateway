"""End-to-end tests for the Cartesia TTS batch recorder.

Mocks Cartesia's /tts/bytes HTTP endpoint with respx, drives the
batch path, and asserts the produced fixture validates against
StreamingFixture and carries the right ``character_count`` ground
truth (len(DEFAULT_TTS_TEXT)) plus a catalog-matched
``expected_cost_usd``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import httpx
import pytest
import respx

from tests.fixtures.streaming._loader import load_fixture

REPO_ROOT = Path(__file__).resolve().parent.parent
RECORDER_PATH = REPO_ROOT / "scripts" / "record-streaming-fixtures.py"


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
        fixture_path = await recorder._run(
            "cartesia", "tts", "sonic-3", "batch"
        )

    assert fixture_path.parent == tmp_path
    assert fixture_path.name.startswith("cartesia_sonic-3_tts_batch_")
    assert fixture_path.name.endswith(".json")

    fixture = load_fixture(fixture_path)
    assert fixture.metadata.provider == "cartesia"
    assert fixture.metadata.model == "sonic-3"
    assert fixture.metadata.modality == "tts"
    assert fixture.metadata.mode == "batch"

    expected_chars = len(recorder.DEFAULT_TTS_TEXT)
    assert fixture.provider_reported_usage == {
        "character_count": expected_chars
    }

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
        fixture_path = await recorder._run(
            "cartesia", "tts", "sonic-3", "batch"
        )

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
        fixture_path = await recorder._run(
            "cartesia", "tts", "sonic-3", "batch"
        )

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


async def test_cartesia_stream_mode_raises_pending_3_3_7(
    recorder: ModuleType,
) -> None:
    with pytest.raises(NotImplementedError, match="3.3 #7"):
        await recorder._record_cartesia_tts("sonic-3", "stream")


# ---------- prompt stability guard -----------------------------------------


def test_default_tts_text_is_stable(recorder: ModuleType) -> None:
    """Changing this string changes character_count; re-record fixtures if you do."""
    assert recorder.DEFAULT_TTS_TEXT == (
        "Hello, this is the canonical voicegateway phase 3 fixture."
    )
    # Snapshot the length so accidental whitespace changes get caught.
    assert len(recorder.DEFAULT_TTS_TEXT) == 58
