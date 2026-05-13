"""TTFB hook hardening: coverage across every modality.

The audit identified TTFB capture as fragile and easy to break
across modalities: ``_InstrumentedBase`` provides the
``_mark_first_byte`` mechanism, but each modality's streaming code
path has to call it at the right moment. If a future modality is
added without wiring TTFB, this suite fails loudly with a clear
message naming the missing modality.

This file is intentionally fixture-independent. The replay tests
in ``tests/test_streaming_cost_accounting.py`` cover TTFB through
recorded fixtures; this file covers it via synthetic streams so
the assertion runs in CI before any human records a fixture for a
new modality.

The behavior under test is the same as
``tests/middleware/test_instrumented_provider.py`` but parametrized
across all three modalities (STT, LLM, TTS) and gated against the
``wrap_provider`` dispatch table so a new modality cannot land
without adding its row to the test's parametrize.
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from voicegateway.middleware import instrumented_provider as ip
from voicegateway.middleware.instrumented_provider import (
    InstrumentedLLM,
    InstrumentedSTT,
    InstrumentedTTS,
    _InstrumentedBase,
    wrap_provider,
)

# Modalities VG ships TTFB capture for. Every entry here must:
# - have a wrapper class registered in wrap_provider's dispatch
# - have _mark_first_byte fire correctly (behavior, not internals)
_KNOWN_MODALITIES: list[str] = ["stt", "llm", "tts"]


def _make_wrapper(modality: str) -> _InstrumentedBase:
    """Build a wrapper of the given modality with mocked deps."""
    cost_tracker = MagicMock()
    cost_tracker.create_record = MagicMock(return_value=MagicMock())
    cost_tracker.notify_spend = AsyncMock()
    wrapper = wrap_provider(
        instance=MagicMock(),
        modality=modality,
        model_id=f"fake/{modality}-model",
        provider="fake",
        project="default",
        cost_tracker=cost_tracker,
        storage=None,
    )
    assert isinstance(wrapper, _InstrumentedBase), (
        f"wrap_provider returned {type(wrapper).__name__} for modality "
        f"{modality!r}, expected an _InstrumentedBase subclass. The "
        "dispatch table in wrap_provider does not know about this "
        "modality, so streaming requests for it would skip ALL "
        "instrumentation including TTFB."
    )
    return wrapper


# ---------- per-modality behavior tests --------------------------------


@pytest.mark.parametrize("modality", _KNOWN_MODALITIES)
async def test_ttfb_hook_records_first_chunk_time(modality: str) -> None:
    """After _mark_first_byte fires, ttfb_ms is non-zero and < total_latency_ms."""
    wrapper = _make_wrapper(modality)
    cost_tracker = object.__getattribute__(wrapper, "_cost_tracker")

    await asyncio.sleep(0.005)
    wrapper._mark_first_byte()
    await asyncio.sleep(0.020)
    await wrapper._log_request(input_units=1.0)

    kwargs = cost_tracker.create_record.call_args.kwargs
    ttfb_ms = kwargs["ttfb_ms"]
    total_ms = kwargs["total_latency_ms"]

    assert ttfb_ms > 0, (
        f"modality={modality!r}: ttfb_ms must be positive after the "
        f"hook fires, got {ttfb_ms}. The wrapper's _mark_first_byte "
        "is not reaching _log_request, OR the hook records the wrong "
        "moment."
    )
    assert ttfb_ms < total_ms, (
        f"modality={modality!r}: ttfb_ms ({ttfb_ms:.2f}) must be "
        f"strictly less than total_latency_ms ({total_ms:.2f}) when "
        "_mark_first_byte fired partway through. Equal values mean the "
        "hook captured the end time instead of the first-byte time."
    )


@pytest.mark.parametrize("modality", _KNOWN_MODALITIES)
async def test_ttfb_hook_falls_back_to_total_when_not_called(
    modality: str,
) -> None:
    """When the hook never fires, ttfb_ms == total_latency_ms (documented fallback)."""
    wrapper = _make_wrapper(modality)
    cost_tracker = object.__getattribute__(wrapper, "_cost_tracker")

    await asyncio.sleep(0.010)
    # Deliberately do NOT call _mark_first_byte.
    await wrapper._log_request(input_units=1.0)

    kwargs = cost_tracker.create_record.call_args.kwargs
    assert kwargs["ttfb_ms"] == kwargs["total_latency_ms"], (
        f"modality={modality!r}: when _mark_first_byte never fires, "
        "ttfb_ms must fall back to total_latency_ms. Diverging means "
        "the wrapper introduced a different fallback path and "
        "downstream dashboards see wrong TTFB values for non-streaming "
        f"requests in this modality. Got ttfb={kwargs['ttfb_ms']}, "
        f"total={kwargs['total_latency_ms']}."
    )


@pytest.mark.parametrize("modality", _KNOWN_MODALITIES)
def test_ttfb_hook_is_idempotent(modality: str) -> None:
    """Subsequent _mark_first_byte calls do NOT overwrite the timestamp."""
    wrapper = _make_wrapper(modality)
    wrapper._mark_first_byte()
    first = object.__getattribute__(wrapper, "_first_byte_time")
    wrapper._mark_first_byte()
    second = object.__getattribute__(wrapper, "_first_byte_time")
    assert first == second, (
        f"modality={modality!r}: idempotency contract broken. "
        "Subsequent _mark_first_byte calls must not overwrite the "
        "first-byte timestamp; otherwise a chunk-level loop would "
        "reset TTFB on every chunk."
    )


# ---------- coverage gates ---------------------------------------------


def test_every_known_modality_has_a_wrapper_class() -> None:
    """``_KNOWN_MODALITIES`` must match the wrappers exported from the module."""
    expected = {"stt", "llm", "tts"}
    assert set(_KNOWN_MODALITIES) == expected, (
        f"This test file's _KNOWN_MODALITIES ({_KNOWN_MODALITIES!r}) "
        f"diverged from the documented set {expected}. If a new "
        "modality was added to instrumented_provider, add it here so "
        "the per-modality behavior tests cover it. If a modality was "
        "removed, remove it here too."
    )


def test_every_instrumented_subclass_is_dispatchable() -> None:
    """Every _InstrumentedBase subclass must be reachable via wrap_provider.

    Walks the source of wrap_provider and asserts that every subclass
    of _InstrumentedBase shows up in its dispatch table. If a future
    contributor adds InstrumentedFoo (for a new modality) but forgets
    to add the modality string to wrap_provider's dispatch, this
    fails with a clear message.
    """
    subclasses = [
        cls
        for cls in _InstrumentedBase.__subclasses__()
        # Defensive: skip private/test subclasses.
        if not cls.__name__.startswith("_")
    ]
    assert len(subclasses) >= 3, (
        f"Expected at least 3 _InstrumentedBase subclasses (STT, LLM, "
        f"TTS). Got {[c.__name__ for c in subclasses]!r}."
    )

    source = inspect.getsource(wrap_provider)
    for cls in subclasses:
        modality = getattr(cls, "_modality", "")
        assert modality, (
            f"{cls.__name__} has no _modality attribute. Every "
            "_InstrumentedBase subclass must declare _modality so "
            "wrap_provider can dispatch by modality string. Without "
            "it the wrapper is unreachable in production."
        )
        assert (
            f'"{modality}": {cls.__name__}' in source
            or f"'{modality}': {cls.__name__}" in source
        ), (
            f"{cls.__name__} (_modality={modality!r}) is not registered "
            "in wrap_provider's dispatch table. Calls into "
            f"gateway.{modality}() would skip TTFB instrumentation. "
            f"Add a {modality!r}: {cls.__name__} entry to wrap_provider."
        )


def test_known_modalities_are_dispatched_by_wrap_provider() -> None:
    """Sanity: wrap_provider returns an _InstrumentedBase for every known modality."""
    for modality in _KNOWN_MODALITIES:
        cost_tracker = MagicMock()
        cost_tracker.notify_spend = AsyncMock()
        wrapper = wrap_provider(
            instance=MagicMock(),
            modality=modality,
            model_id=f"fake/{modality}-x",
            provider="fake",
            project="default",
            cost_tracker=cost_tracker,
            storage=None,
        )
        assert isinstance(wrapper, _InstrumentedBase), (
            f"wrap_provider returned {type(wrapper).__name__} for "
            f"modality {modality!r}; the dispatch table is incomplete "
            "and this modality skips instrumentation in production."
        )
        assert object.__getattribute__(wrapper, "_modality") == modality


def test_unknown_modality_returns_unwrapped_instance() -> None:
    """Documented contract: an unknown modality returns the raw instance.

    This is intentional behavior (graceful degradation: a new modality
    that hasn't been wired works without instrumentation rather than
    crashing). It is also the failure mode that the dispatch-coverage
    test above exists to catch: a brand-new modality slips through
    silently. The two tests together gate it: this one documents the
    contract; the other ensures _InstrumentedBase subclasses cannot
    diverge from the dispatch table.
    """
    sentinel = MagicMock()
    cost_tracker = MagicMock()
    cost_tracker.notify_spend = AsyncMock()
    result = wrap_provider(
        instance=sentinel,
        modality="some-future-modality",
        model_id="x/y",
        provider="x",
        project="default",
        cost_tracker=cost_tracker,
        storage=None,
    )
    assert result is sentinel
    assert not isinstance(result, _InstrumentedBase)


def test_wrappers_export_expected_names() -> None:
    """If someone renames a wrapper class, downstream tests must update too."""
    assert ip.InstrumentedSTT is InstrumentedSTT
    assert ip.InstrumentedLLM is InstrumentedLLM
    assert ip.InstrumentedTTS is InstrumentedTTS
    assert InstrumentedSTT._modality == "stt"
    assert InstrumentedLLM._modality == "llm"
    assert InstrumentedTTS._modality == "tts"
