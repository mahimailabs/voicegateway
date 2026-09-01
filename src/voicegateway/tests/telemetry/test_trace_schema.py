"""Tests for immutable trace contract models."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from voicegateway.telemetry.trace_schema import (
    Attribute,
    InstrumentationScope,
    SpanAttributes,
    SpanContext,
    SpanEvent,
    SpanKind,
    SpanLink,
    SpanRecord,
    SpanStatus,
    SpanStatusCode,
)

_TRACE_ID = "0123456789abcdef0123456789abcdef"
_SPAN_ID = "0123456789abcdef"


def _scope() -> InstrumentationScope:
    return InstrumentationScope(name="voicegateway.contract")


def _record(**changes) -> SpanRecord:
    fields = {
        "name": "voice.turn",
        "context": SpanContext(trace_id=_TRACE_ID, span_id=_SPAN_ID),
        "kind": SpanKind.SERVER,
        "start_time_unix_nano": 100,
        "end_time_unix_nano": 200,
        "instrumentation_scope": _scope(),
    }
    fields.update(changes)
    return SpanRecord(**fields)


def test_record_carries_explicit_correlation_and_recursive_attributes():
    record = _record(
        correlation=SpanAttributes(
            tenant_id="tenant-a",
            project_id="project-a",
            session_id="session-a",
            turn_index=2,
            agent_revision="r1",
            workflow_version="w1",
        ),
        attributes=(
            Attribute(
                key="nested",
                value={"items": [1, True, None], "blob": b"bytes"},
            ),
        ),
    )

    encoded = record.model_dump(mode="json")
    assert encoded["attributes"][0]["value"]["blob"] == "Ynl0ZXM="
    assert encoded["correlation"]["tenant_id"] == "tenant-a"
    assert json.loads(json.dumps(encoded))["name"] == "voice.turn"


def test_models_are_frozen_and_reject_unknown_fields():
    record = _record()

    with pytest.raises(ValidationError):
        record.name = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        SpanContext(trace_id=_TRACE_ID, span_id=_SPAN_ID, unexpected=True)


@pytest.mark.parametrize(
    "changes",
    [
        {"end_time_unix_nano": 99},
        {"parent_span_id": _SPAN_ID},
        {
            "attributes": (
                Attribute(key="duplicate", value=1),
                Attribute(key="duplicate", value=2),
            )
        },
    ],
)
def test_invalid_record_shapes_are_rejected(changes):
    with pytest.raises(ValidationError):
        _record(**changes)


def test_status_description_is_limited_to_errors():
    with pytest.raises(ValidationError):
        SpanStatus(code=SpanStatusCode.OK, message="not allowed")

    assert SpanStatus(code=SpanStatusCode.ERROR, message="provider timeout").message


def test_events_and_links_represent_timing_and_asynchronous_work():
    record = _record(
        events=(SpanEvent(name="retry", timestamp_unix_nano=150),),
        links=(
            SpanLink(
                context=SpanContext(
                    trace_id="abcdef0123456789abcdef0123456789",
                    span_id="fedcba9876543210",
                    is_remote=True,
                )
            ),
        ),
    )

    assert record.events[0].name == "retry"
    assert record.links[0].context.is_remote is True


@pytest.mark.parametrize(
    "value",
    [2**63, {"": "not allowed"}, object()],
)
def test_any_value_rejects_values_outside_the_otlp_model(value):
    with pytest.raises(ValidationError):
        Attribute(key="value", value=value)
