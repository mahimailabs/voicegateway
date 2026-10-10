"""The StreamingFixture schema accepts recorded fixtures and rejects bad ones."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from voicegateway.tests.fixtures.streaming._schema import StreamingFixture


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
        "request": {"prompt": "Hello, how are you?", "max_tokens": 100, "stream": True},
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
    payload = _llm_fixture()
    payload["metadata"]["future_field"] = "ok"  # extra metadata is allowed
    payload["response_stream"][0]["data"] = "base64-bytes-here=="  # binary payloads
    fixture = StreamingFixture.model_validate(payload)
    assert fixture.metadata.modality == "llm"
    assert len(fixture.response_stream) == 2
    # Recorded as a JSON string, parsed exactly: money never goes through float.
    assert fixture.expected_cost_usd == Decimal("0.00001235")


def test_zero_cost_fixture_valid_for_local_providers() -> None:
    """Zero is a real cost (local/whisper, local/kokoro) and must pass."""
    payload = _llm_fixture()
    payload["metadata"].update(provider="local", model="whisper-base", modality="stt")
    payload["expected_cost_usd"] = "0"
    assert StreamingFixture.model_validate(payload).expected_cost_usd == Decimal("0")


def _set(path: tuple, value: Any) -> Callable[[dict], None]:
    def mutate(payload: dict) -> None:
        target = payload
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


def _swap_chunks(payload: dict) -> None:
    payload["response_stream"][0]["chunk_index"] = 1
    payload["response_stream"][1]["chunk_index"] = 0


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (_set(("metadata", "modality"), "embedding"), "modality"),
        (_set(("metadata", "mode"), "websocket"), "mode"),
        (_set(("metadata", "recorded_at"), "not-a-real-timestamp"), "recorded_at"),
        (lambda p: p["metadata"].pop("recorded_by"), "recorded_by"),
        (_set(("response_stream", 1, "chunk_index"), 5), "expected 1"),
        (_swap_chunks, "chunk_index"),
        (_set(("response_stream", 0, "received_at_ms"), -1), "received_at_ms"),
        (_set(("unexpected",), "fail-fast"), "unexpected"),
        (_set(("expected_cost_usd",), "-0.001"), "non-negative"),
    ],
    ids=[
        "modality",
        "mode",
        "recorded_at",
        "missing-recorded_by",
        "chunk-gap",
        "chunk-order",
        "negative-ms",
        "extra-top-level",
        "negative-cost",
    ],
)
def test_invalid_fixture_rejected(mutate, match) -> None:
    payload = _llm_fixture()
    mutate(payload)
    with pytest.raises(ValidationError, match=match):
        StreamingFixture.model_validate(payload)
