"""Unit tests for the StreamingFixture pydantic schema.

These tests cover the schema itself, not real fixture files. They
validate that the structural contract from design doc §3.1 is
enforced on load: required fields, modality / mode literals,
chunk-index ordering, expected_cost_usd type and sign.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from voicegateway.tests.fixtures.streaming._schema import (
    FixtureChunk,
    FixtureMetadata,
    StreamingFixture,
)


def _llm_fixture() -> dict[str, Any]:
    """Return a minimal-but-valid LLM streaming fixture payload."""
    return {
        "metadata": {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "modality": "llm",
            "mode": "stream",
            "recorded_at": "2026-05-04T14:32:11Z",
            "recorded_by": "tests/fixtures/streaming/record_streaming_fixtures.py",
            "voicegateway_version": "0.0.3",
        },
        "request": {
            "prompt": "Hello, how are you?",
            "max_tokens": 100,
            "stream": True,
        },
        "response_stream": [
            {"chunk_index": 0, "received_at_ms": 312, "data": {"text": "Hi"}},
            {"chunk_index": 1, "received_at_ms": 348, "data": {"text": "."}},
        ],
        "provider_reported_usage": {
            "input_tokens": 14,
            "output_tokens": 23,
            "total_tokens": 37,
        },
        "expected_cost_usd": "0.00001235",
    }


def test_valid_fixture_parses_cleanly() -> None:
    fixture = StreamingFixture.model_validate(_llm_fixture())
    assert fixture.metadata.provider == "openai"
    assert fixture.metadata.modality == "llm"
    assert fixture.metadata.mode == "stream"
    assert len(fixture.response_stream) == 2
    assert str(fixture.expected_cost_usd) == "0.00001235"


def test_modality_must_be_stt_llm_or_tts() -> None:
    payload = _llm_fixture()
    payload["metadata"]["modality"] = "embedding"
    with pytest.raises(ValidationError) as exc:
        StreamingFixture.model_validate(payload)
    assert "modality" in str(exc.value)


def test_mode_must_be_batch_or_stream() -> None:
    payload = _llm_fixture()
    payload["metadata"]["mode"] = "websocket"
    with pytest.raises(ValidationError) as exc:
        StreamingFixture.model_validate(payload)
    assert "mode" in str(exc.value)


def test_metadata_recorded_at_must_parse_as_datetime() -> None:
    payload = _llm_fixture()
    payload["metadata"]["recorded_at"] = "not-a-real-timestamp"
    with pytest.raises(ValidationError):
        StreamingFixture.model_validate(payload)


def test_chunk_indices_must_be_zero_based_and_contiguous() -> None:
    payload = _llm_fixture()
    payload["response_stream"][1]["chunk_index"] = 5
    with pytest.raises(ValidationError) as exc:
        StreamingFixture.model_validate(payload)
    msg = str(exc.value)
    assert "chunk_index" in msg, f"expected chunk_index in error, got: {msg}"
    assert "expected 1" in msg or "expected 1." in msg


def test_chunk_indices_out_of_order_rejected() -> None:
    payload = _llm_fixture()
    payload["response_stream"][0]["chunk_index"] = 1
    payload["response_stream"][1]["chunk_index"] = 0
    with pytest.raises(ValidationError):
        StreamingFixture.model_validate(payload)


def test_chunk_received_at_ms_cannot_be_negative() -> None:
    payload = _llm_fixture()
    payload["response_stream"][0]["received_at_ms"] = -1
    with pytest.raises(ValidationError):
        StreamingFixture.model_validate(payload)


def test_chunk_data_accepts_string_for_binary_payloads() -> None:
    payload = _llm_fixture()
    payload["response_stream"][0]["data"] = "base64-bytes-here=="
    fixture = StreamingFixture.model_validate(payload)
    assert fixture.response_stream[0].data == "base64-bytes-here=="


def test_extra_top_level_keys_rejected() -> None:
    payload = _llm_fixture()
    payload["unexpected"] = "fail-fast"
    with pytest.raises(ValidationError):
        StreamingFixture.model_validate(payload)


def test_extra_metadata_keys_allowed() -> None:
    payload = _llm_fixture()
    payload["metadata"]["future_field"] = "ok"
    fixture = StreamingFixture.model_validate(payload)
    assert fixture.metadata.provider == "openai"


def test_expected_cost_usd_string_parses_to_decimal() -> None:
    """Recording script writes Decimals as JSON strings; ensure round-trip works."""
    payload = _llm_fixture()
    payload["expected_cost_usd"] = "0.00006175"
    fixture = StreamingFixture.model_validate(payload)
    from decimal import Decimal

    assert fixture.expected_cost_usd == Decimal("0.00006175")


def test_expected_cost_usd_must_be_non_negative() -> None:
    payload = _llm_fixture()
    payload["expected_cost_usd"] = "-0.001"
    with pytest.raises(ValidationError) as exc:
        StreamingFixture.model_validate(payload)
    assert "non-negative" in str(exc.value).lower()


def test_zero_cost_fixture_valid_for_local_providers() -> None:
    """Zero is a real cost (local/whisper, local/kokoro) and must pass."""
    payload = _llm_fixture()
    payload["metadata"]["provider"] = "local"
    payload["metadata"]["model"] = "whisper-base"
    payload["metadata"]["modality"] = "stt"
    payload["expected_cost_usd"] = "0"
    fixture = StreamingFixture.model_validate(payload)
    from decimal import Decimal

    assert fixture.expected_cost_usd == Decimal("0")


def test_required_metadata_field_missing_rejects() -> None:
    payload = _llm_fixture()
    del payload["metadata"]["recorded_by"]
    with pytest.raises(ValidationError) as exc:
        StreamingFixture.model_validate(payload)
    assert "recorded_by" in str(exc.value)


def test_response_stream_can_be_single_chunk() -> None:
    """Batch-mode fixtures are a single 'chunk' carrying the full response."""
    payload = _llm_fixture()
    payload["metadata"]["mode"] = "batch"
    payload["response_stream"] = [
        {
            "chunk_index": 0,
            "received_at_ms": 0,
            "data": {"choices": [{"message": {"content": "Hello"}}]},
        },
    ]
    fixture = StreamingFixture.model_validate(payload)
    assert len(fixture.response_stream) == 1


def test_fixture_chunk_model_directly() -> None:
    """FixtureChunk is a public-ish dataclass; sanity-check it works alone."""
    chunk = FixtureChunk.model_validate(
        {"chunk_index": 0, "received_at_ms": 100, "data": {"x": 1}}
    )
    assert chunk.chunk_index == 0
    assert chunk.received_at_ms == 100
    assert chunk.data == {"x": 1}


def test_fixture_metadata_model_directly() -> None:
    """FixtureMetadata is a public-ish dataclass; sanity-check it works alone."""
    meta = FixtureMetadata.model_validate(_llm_fixture()["metadata"])
    assert meta.provider == "openai"
    assert meta.model == "gpt-4o-mini"


def test_deepcopy_safe_for_pytest_parameterize_reuse() -> None:
    """The loader will deepcopy when constructing per-test parameters; ensure that's safe."""
    payload = _llm_fixture()
    fixture_a = StreamingFixture.model_validate(payload)
    fixture_b = StreamingFixture.model_validate(deepcopy(payload))
    assert fixture_a == fixture_b
