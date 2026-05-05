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
        fixture_path = await recorder._run(
            "deepgram", "stt", "nova-3", "batch"
        )

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
        fixture_path = await recorder._run(
            "deepgram", "stt", "nova-3", "batch"
        )

    fixture = load_fixture(fixture_path)
    expected = calculate_cost(
        "stt", "deepgram/nova-3", audio_seconds=3.0
    )
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
        fixture_path = await recorder._run(
            "deepgram", "stt", "nova-3", "batch"
        )

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
    monkeypatch.setattr(
        recorder, "AUDIO_SAMPLE_PATH", tmp_path / "missing.wav"
    )
    with pytest.raises(RuntimeError, match="missing"):
        await recorder._record_deepgram_stt("nova-3", "batch")


async def test_deepgram_stream_mode_raises_pending_3_3_5(
    recorder: ModuleType,
) -> None:
    """Stream-mode recording lands in 3.3 #5; 3.3 #4 only ships batch."""
    with pytest.raises(NotImplementedError, match="3.3 #5"):
        await recorder._record_deepgram_stt("nova-3", "stream")
