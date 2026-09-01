"""Tests for W3C parsing and task-local trace context."""

from __future__ import annotations

import asyncio
import contextvars
import re

import pytest

from voicegateway.telemetry.context import (
    TraceContextError,
    current_trace_context,
    format_traceparent,
    new_span_id,
    new_trace_id,
    parse_traceparent,
    parse_tracestate,
    reset_trace_context,
    set_trace_context,
)
from voicegateway.telemetry.trace_schema import SpanContext

_TRACE_ID = "0123456789abcdef0123456789abcdef"
_SPAN_ID = "0123456789abcdef"


def test_traceparent_v00_parses_as_remote_context():
    context = parse_traceparent(f"00-{_TRACE_ID}-{_SPAN_ID}-01")

    assert context.version == "00"
    assert context.trace_id == _TRACE_ID
    assert context.span_id == _SPAN_ID
    assert context.sampled is True
    assert context.is_remote is True


def test_higher_traceparent_version_keeps_core_fields():
    context = parse_traceparent(f"01-{_TRACE_ID}-{_SPAN_ID}-83-vendor-extension")

    assert context.version == "01"
    assert context.trace_flags == 0x83
    assert context.sampled is True


@pytest.mark.parametrize(
    "value",
    [
        f"00-{'0' * 32}-{_SPAN_ID}-01",
        f"00-{_TRACE_ID}-{'0' * 16}-01",
        f"00-{_TRACE_ID.upper()}-{_SPAN_ID}-01",
        f"ff-{_TRACE_ID}-{_SPAN_ID}-01",
        f"00-{_TRACE_ID}-{_SPAN_ID}-01-extra",
        f"00-{_TRACE_ID}-{_SPAN_ID}-1",
        "not-a-traceparent",
    ],
)
def test_invalid_traceparent_is_rejected(value: str):
    with pytest.raises(TraceContextError):
        parse_traceparent(value)


def test_tracestate_preserves_valid_raw_value():
    value = "vendor=one, tenant@system=two"

    assert parse_tracestate(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "vendor=one,vendor=two",
        "Vendor=one",
        "vendor=",
        "vendor=one=two",
    ],
)
def test_invalid_tracestate_is_rejected(value: str):
    with pytest.raises(TraceContextError):
        parse_tracestate(value)


def test_tracestate_rejects_more_than_32_members():
    value = ",".join(f"k{index}=v" for index in range(33))

    with pytest.raises(TraceContextError):
        parse_tracestate(value)


def test_format_traceparent_downgrades_to_v00_and_masks_unknown_flags():
    context = SpanContext(
        version="01",
        trace_id=_TRACE_ID,
        span_id=_SPAN_ID,
        trace_flags=0x83,
    )

    assert format_traceparent(context, span_id="fedcba9876543210") == (
        f"00-{_TRACE_ID}-fedcba9876543210-01"
    )


def test_generated_ids_have_w3c_shape():
    assert re.fullmatch(r"[0-9a-f]{32}", new_trace_id())
    assert re.fullmatch(r"[0-9a-f]{16}", new_span_id())


def test_nested_context_reset_restores_the_exact_previous_context():
    first = SpanContext(trace_id=_TRACE_ID, span_id=_SPAN_ID)
    second = SpanContext(trace_id=_TRACE_ID, span_id="fedcba9876543210")

    outer = set_trace_context(first)
    inner = set_trace_context(second)
    assert current_trace_context() == second

    reset_trace_context(inner)
    assert current_trace_context() == first
    reset_trace_context(outer)
    assert current_trace_context() is None


async def test_context_propagates_to_awaited_coroutines_and_copied_tasks():
    context = SpanContext(trace_id=_TRACE_ID, span_id=_SPAN_ID)

    async def observed() -> SpanContext | None:
        await asyncio.sleep(0)
        return current_trace_context()

    token = set_trace_context(context)
    try:
        assert await observed() == context
        task = asyncio.create_task(observed(), context=contextvars.copy_context())
        assert await task == context
    finally:
        reset_trace_context(token)
