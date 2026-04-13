"""Tests for fallback chains."""

import pytest

from voicegateway.middleware.fallback import FallbackChain, FallbackError


def test_primary_succeeds():
    calls = []

    def resolver(model_id, modality, **kwargs):
        calls.append(model_id)
        return f"instance_{model_id}"

    chain = FallbackChain(
        chain=["primary/model", "backup/model"],
        resolver=resolver,
        modality="stt",
    )
    result = chain.resolve()
    assert result == "instance_primary/model"
    assert calls == ["primary/model"]


def test_fallback_on_error():
    calls = []

    def resolver(model_id, modality, **kwargs):
        calls.append(model_id)
        if model_id == "primary/model":
            raise ConnectionError("primary down")
        return f"instance_{model_id}"

    chain = FallbackChain(
        chain=["primary/model", "backup/model"],
        resolver=resolver,
        modality="stt",
    )
    result = chain.resolve()
    assert result == "instance_backup/model"
    assert calls == ["primary/model", "backup/model"]


def test_fallback_on_timeout():
    def resolver(model_id, modality, **kwargs):
        if model_id == "primary/model":
            raise TimeoutError("timeout")
        return f"instance_{model_id}"

    chain = FallbackChain(
        chain=["primary/model", "backup/model"],
        resolver=resolver,
        modality="stt",
    )
    result = chain.resolve()
    assert result == "instance_backup/model"


def test_all_fail_raises_error():
    def resolver(model_id, modality, **kwargs):
        raise RuntimeError(f"{model_id} failed")

    chain = FallbackChain(
        chain=["a/1", "b/2", "c/3"],
        resolver=resolver,
        modality="llm",
    )
    with pytest.raises(FallbackError) as exc_info:
        chain.resolve()

    assert len(exc_info.value.errors) == 3


def test_fallback_callback():
    events = []

    def resolver(model_id, modality, **kwargs):
        if model_id == "primary/model":
            raise RuntimeError("fail")
        return "success"

    def on_fallback(original, fallback, reason):
        events.append((original, fallback, reason))

    chain = FallbackChain(
        chain=["primary/model", "backup/model"],
        resolver=resolver,
        modality="stt",
        on_fallback=on_fallback,
    )
    chain.resolve()
    assert len(events) == 1
    assert events[0][0] == "primary/model"
    assert events[0][1] == "backup/model"
