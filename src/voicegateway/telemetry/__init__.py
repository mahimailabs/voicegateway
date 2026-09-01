"""Internal, dependency-free telemetry contracts (Wave 0).

Nothing in this package is wired into request handling, storage, or an
OpenTelemetry SDK yet.  It freezes the vocabulary those later waves share.
"""

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
from voicegateway.telemetry.trace_schema import (
    AnyValue,
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

__all__ = [
    "AnyValue",
    "Attribute",
    "InstrumentationScope",
    "SpanAttributes",
    "SpanContext",
    "SpanEvent",
    "SpanKind",
    "SpanLink",
    "SpanRecord",
    "SpanStatus",
    "SpanStatusCode",
    "TraceContextError",
    "current_trace_context",
    "format_traceparent",
    "new_span_id",
    "new_trace_id",
    "parse_traceparent",
    "parse_tracestate",
    "reset_trace_context",
    "set_trace_context",
]
