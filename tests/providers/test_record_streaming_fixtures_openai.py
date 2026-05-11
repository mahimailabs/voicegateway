"""End-to-end tests for the OpenAI LLM batch recorder.

These tests load the recording script as a module, monkey-patch its
FIXTURES_DIR to a tmp path, mock the OpenAI HTTP endpoint with
respx, and drive the batch recording path. The assertions cover
the full happy path: a fixture file is written, validates through
the StreamingFixture schema, and carries a `expected_cost_usd`
that matches what `voicegateway.pricing.catalog.calculate_cost`
would compute for the same usage.

The script lives at scripts/record_streaming_fixtures.py and is
not in an importable package, so importlib.util loads it from its
absolute file path.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import httpx
import pytest
import respx

from tests.fixtures.streaming._loader import load_fixture

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RECORDER_PATH = REPO_ROOT / "scripts" / "record_streaming_fixtures.py"


def _import_recorder() -> ModuleType:
    """Load scripts/record_streaming_fixtures.py as an importable module."""
    spec = importlib.util.spec_from_file_location(
        "_phase3_recorder_under_test", RECORDER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_phase3_recorder_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def recorder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Recorder module with FIXTURES_DIR patched to a tmp directory."""
    mod = _import_recorder()
    monkeypatch.setattr(mod, "FIXTURES_DIR", tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-not-real")
    return mod


def _openai_batch_response(input_tokens: int, output_tokens: int) -> dict:
    """Return a real-shaped OpenAI ChatCompletion JSON for respx to serve."""
    return {
        "id": "chatcmpl-fixture-test",
        "object": "chat.completion",
        "created": 1746540000,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "I am doing well, thanks for asking.",
                },
                "logprobs": None,
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "prompt_tokens_details": {
                "cached_tokens": 0,
                "audio_tokens": 0,
            },
            "completion_tokens_details": {
                "reasoning_tokens": 0,
                "audio_tokens": 0,
                "accepted_prediction_tokens": 0,
                "rejected_prediction_tokens": 0,
            },
        },
        "system_fingerprint": "fp_test",
    }


async def test_openai_batch_recording_writes_valid_fixture(
    recorder: ModuleType, tmp_path: Path
) -> None:
    """Happy path: respx mocks the chat completion, fixture lands valid."""
    response_payload = _openai_batch_response(input_tokens=18, output_tokens=8)
    async with respx.mock(assert_all_called=True) as router:
        router.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=response_payload)
        )
        fixture_path = await recorder._run("openai", "llm", "gpt-4o-mini", "batch")

    # Filename matches the convention.
    assert fixture_path.parent == tmp_path
    assert fixture_path.name.startswith("openai_gpt-4o-mini_llm_batch_")
    assert fixture_path.name.endswith(".json")
    assert fixture_path.exists()

    # File parses through the schema.
    fixture = load_fixture(fixture_path)
    assert fixture.metadata.provider == "openai"
    assert fixture.metadata.model == "gpt-4o-mini"
    assert fixture.metadata.modality == "llm"
    assert fixture.metadata.mode == "batch"
    assert fixture.metadata.recorded_by == "scripts/record_streaming_fixtures.py"

    # Recorded usage carries the normalized field names.
    usage = fixture.provider_reported_usage
    assert usage["input_tokens"] == 18
    assert usage["output_tokens"] == 8
    assert usage["total_tokens"] == 26

    # response_stream is a single-chunk wrapper of the full response.
    assert len(fixture.response_stream) == 1
    chunk = fixture.response_stream[0]
    assert chunk.chunk_index == 0
    assert chunk.received_at_ms == 0


async def test_openai_batch_expected_cost_matches_catalog(
    recorder: ModuleType,
) -> None:
    """expected_cost_usd is computed via the pricing catalog at recording time."""
    from voicegateway.pricing.catalog import calculate_cost

    response_payload = _openai_batch_response(input_tokens=18, output_tokens=8)
    async with respx.mock(assert_all_called=True) as router:
        router.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=response_payload)
        )
        fixture_path = await recorder._run("openai", "llm", "gpt-4o-mini", "batch")

    fixture = load_fixture(fixture_path)
    expected = calculate_cost(
        "llm", "openai/gpt-4o-mini", input_tokens=18, output_tokens=8
    )
    assert expected is not None
    quantized = expected.quantize(Decimal("0.00000001"))
    assert fixture.expected_cost_usd == quantized


async def test_openai_batch_uses_canonical_test_prompt(
    recorder: ModuleType,
) -> None:
    """Recorder POSTs the canonical prompt with stream=False and the right model.

    The HTTP body sent to OpenAI must contain ``model=gpt-4o-mini``,
    ``stream=False`` (this is the batch path), and the canonical
    DEFAULT_LLM_PROMPT text on the user message. Asserted via
    ``route.calls.last.request.content`` after respx intercepts the
    call.
    """
    response_payload = _openai_batch_response(input_tokens=18, output_tokens=8)
    async with respx.mock(assert_all_called=True) as router:
        route = router.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=response_payload)
        )
        await recorder._run("openai", "llm", "gpt-4o-mini", "batch")

    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == "gpt-4o-mini"
    assert sent["stream"] is False
    assert sent["messages"][0]["content"] == (
        "Hello, how are you? Please respond in one short sentence."
    )


async def test_openai_batch_refuses_when_usage_missing(
    recorder: ModuleType,
) -> None:
    """Provider responses without usage are unreplayable; refuse to write the fixture."""
    response_payload = _openai_batch_response(input_tokens=1, output_tokens=1)
    response_payload["usage"] = None  # type: ignore[assignment]
    async with respx.mock(assert_all_called=True) as router:
        router.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=response_payload)
        )
        with pytest.raises(RuntimeError, match="usage"):
            await recorder._run("openai", "llm", "gpt-4o-mini", "batch")


def _openai_stream_sse(
    *, content_chunks: list[str], input_tokens: int, output_tokens: int
) -> bytes:
    """Return SSE bytes mimicking an OpenAI chat completion stream.

    Chunk shape matches what the openai python client expects: a
    leading role chunk, one chunk per content piece, a finish chunk,
    and the trailing usage chunk that ``stream_options.include_usage``
    enables.
    """
    base = {
        "id": "chatcmpl-stream-fixture",
        "object": "chat.completion.chunk",
        "created": 1746540001,
        "model": "gpt-4o-mini",
    }

    events: list[dict] = []
    # Role chunk.
    events.append(
        {
            **base,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": ""},
                    "logprobs": None,
                    "finish_reason": None,
                }
            ],
            "usage": None,
        }
    )
    # Content chunks.
    for piece in content_chunks:
        events.append(
            {
                **base,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": piece},
                        "logprobs": None,
                        "finish_reason": None,
                    }
                ],
                "usage": None,
            }
        )
    # Finish chunk.
    events.append(
        {
            **base,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "logprobs": None,
                    "finish_reason": "stop",
                }
            ],
            "usage": None,
        }
    )
    # Usage chunk (only present when include_usage is True).
    events.append(
        {
            **base,
            "choices": [],
            "usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "prompt_tokens_details": {
                    "cached_tokens": 0,
                    "audio_tokens": 0,
                },
                "completion_tokens_details": {
                    "reasoning_tokens": 0,
                    "audio_tokens": 0,
                    "accepted_prediction_tokens": 0,
                    "rejected_prediction_tokens": 0,
                },
            },
        }
    )

    body = b""
    for event in events:
        body += b"data: " + json.dumps(event).encode("utf-8") + b"\n\n"
    body += b"data: [DONE]\n\n"
    return body


async def test_openai_stream_recording_writes_valid_fixture(
    recorder: ModuleType, tmp_path: Path
) -> None:
    """Stream path: chunks captured with received_at_ms, fixture validates."""
    body = _openai_stream_sse(
        content_chunks=["Hi", " there", "!"],
        input_tokens=18,
        output_tokens=3,
    )
    async with respx.mock(assert_all_called=True) as router:
        router.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                content=body,
                headers={"content-type": "text/event-stream"},
            )
        )
        fixture_path = await recorder._run("openai", "llm", "gpt-4o-mini", "stream")

    fixture = load_fixture(fixture_path)
    assert fixture.metadata.mode == "stream"
    # Role chunk + 3 content chunks + finish chunk + usage chunk = 6 chunks.
    assert len(fixture.response_stream) == 6
    indices = [c.chunk_index for c in fixture.response_stream]
    assert indices == [0, 1, 2, 3, 4, 5]
    timestamps = [c.received_at_ms for c in fixture.response_stream]
    assert all(ts >= 0 for ts in timestamps)
    # Timestamps should be monotonically non-decreasing.
    assert timestamps == sorted(timestamps)

    usage = fixture.provider_reported_usage
    assert usage["input_tokens"] == 18
    assert usage["output_tokens"] == 3
    assert usage["total_tokens"] == 21


async def test_openai_stream_request_carries_include_usage(
    recorder: ModuleType,
) -> None:
    """Recorder must pass stream_options.include_usage so usage is on the wire."""
    body = _openai_stream_sse(content_chunks=["x"], input_tokens=1, output_tokens=1)
    async with respx.mock(assert_all_called=True) as router:
        route = router.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                content=body,
                headers={"content-type": "text/event-stream"},
            )
        )
        await recorder._run("openai", "llm", "gpt-4o-mini", "stream")

    sent = json.loads(route.calls.last.request.content)
    assert sent["stream"] is True
    assert sent.get("stream_options", {}).get("include_usage") is True


async def test_openai_stream_refuses_when_usage_chunk_missing(
    recorder: ModuleType,
) -> None:
    """Without a final usage chunk the fixture is unanchored; refuse to write."""
    # Build SSE without the trailing usage chunk.
    base = {
        "id": "chatcmpl-no-usage",
        "object": "chat.completion.chunk",
        "created": 1746540002,
        "model": "gpt-4o-mini",
    }
    events = [
        {
            **base,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": ""},
                    "finish_reason": None,
                }
            ],
            "usage": None,
        },
        {
            **base,
            "choices": [
                {"index": 0, "delta": {"content": "hi"}, "finish_reason": None}
            ],
            "usage": None,
        },
        {
            **base,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": None,
        },
    ]
    body = b""
    for ev in events:
        body += b"data: " + json.dumps(ev).encode("utf-8") + b"\n\n"
    body += b"data: [DONE]\n\n"

    async with respx.mock(assert_all_called=True) as router:
        router.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                content=body,
                headers={"content-type": "text/event-stream"},
            )
        )
        with pytest.raises(RuntimeError, match="usage"):
            await recorder._run("openai", "llm", "gpt-4o-mini", "stream")


async def test_openai_recorder_requires_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing OPENAI_API_KEY surfaces as a RuntimeError, not a network call."""
    mod = _import_recorder()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        await mod._record_openai_llm("gpt-4o-mini", "batch")


def test_compute_expected_cost_usd_unknown_llm_model_raises(
    recorder: ModuleType,
) -> None:
    """An unknown LLM model id has no catalog price; fixture would be unanchored."""
    with pytest.raises(RuntimeError, match="catalog"):
        recorder._compute_expected_cost_usd(
            "openai",
            "definitely-not-a-real-model-id-2026",
            "llm",
            {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        )


def test_compute_expected_cost_usd_quantizes_to_eight_places(
    recorder: ModuleType,
) -> None:
    """Quantization is fixed at 8 decimal places to keep JSON Decimal-stable."""
    cost_str = recorder._compute_expected_cost_usd(
        "openai",
        "gpt-4o-mini",
        "llm",
        {"input_tokens": 18, "output_tokens": 8, "total_tokens": 26},
    )
    # Two pieces: a Decimal that round-trips, and exactly 8 fractional digits.
    parsed = Decimal(cost_str)
    assert parsed >= 0
    assert "." in cost_str
    fraction = cost_str.split(".", 1)[1]
    assert len(fraction) == 8


def test_build_fixture_payload_recorded_at_uses_zulu_suffix(
    recorder: ModuleType,
) -> None:
    """recorded_at uses Z suffix per design §3.1 example, not +00:00."""
    intermediate = {
        "request": {"messages": [{"role": "user", "content": "hi"}]},
        "response_stream": [{"chunk_index": 0, "received_at_ms": 0, "data": {"x": 1}}],
        "provider_reported_usage": {
            "input_tokens": 1,
            "output_tokens": 1,
            "total_tokens": 2,
        },
    }
    payload = recorder._build_fixture_payload(
        "openai",
        "gpt-4o-mini",
        "llm",
        "batch",
        intermediate,
        voicegateway_version="0.1.0",
        recorded_at=datetime(2026, 5, 4, 14, 32, 11, tzinfo=UTC),
    )
    assert payload["metadata"]["recorded_at"] == "2026-05-04T14:32:11Z"
