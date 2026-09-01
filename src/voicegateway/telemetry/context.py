"""W3C Trace Context parsing and task-local propagation helpers."""

from __future__ import annotations

import re
import secrets
from contextvars import ContextVar, Token

from voicegateway.telemetry.trace_schema import SpanContext

_TRACEPARENT_RE = re.compile(
    r"^(?P<version>[0-9a-f]{2})-"
    r"(?P<trace_id>[0-9a-f]{32})-"
    r"(?P<span_id>[0-9a-f]{16})-"
    r"(?P<flags>[0-9a-f]{2})(?P<extra>(?:-.*)?)$"
)
_TRACESTATE_KEY_RE = re.compile(
    r"(?:[a-z][a-z0-9_\-*/]{0,255}|"
    r"[a-z0-9][a-z0-9_\-*/]{0,240}@[a-z][a-z0-9_\-*/]{0,13})$"
)


class TraceContextError(ValueError):
    """Raised by strict W3C header parsers.

    Transport adapters decide whether to ignore this error and begin a fresh
    trace; the low-level parser never silently changes incoming correlation.
    """


_current_trace_context: ContextVar[SpanContext | None] = ContextVar(
    "voicegateway_trace_context", default=None
)


def new_trace_id() -> str:
    """Return a non-zero, 16-byte lowercase W3C trace ID."""
    while True:
        value = secrets.token_hex(16)
        if value != "0" * 32:
            return value


def new_span_id() -> str:
    """Return a non-zero, 8-byte lowercase W3C span ID."""
    while True:
        value = secrets.token_hex(8)
        if value != "0" * 16:
            return value


def parse_traceparent(value: str) -> SpanContext:
    """Strictly parse a W3C ``traceparent`` value.

    Version ``00`` must have exactly four fields.  Higher versions retain the
    core fields and ignore their unknown extension fields, as required by W3C.
    """
    if not isinstance(value, str):
        raise TraceContextError("traceparent must be a string")
    match = _TRACEPARENT_RE.fullmatch(value)
    if match is None:
        raise TraceContextError("traceparent has an invalid format")

    version = match.group("version")
    extra = match.group("extra")
    if version == "ff":
        raise TraceContextError("traceparent version ff is forbidden")
    if version == "00" and extra:
        raise TraceContextError("traceparent version 00 must not contain extensions")

    try:
        return SpanContext(
            version=version,
            trace_id=match.group("trace_id"),
            span_id=match.group("span_id"),
            trace_flags=int(match.group("flags"), 16),
            is_remote=True,
        )
    except ValueError as exc:
        raise TraceContextError(str(exc)) from exc


def parse_tracestate(value: str) -> str:
    """Validate and return an opaque W3C ``tracestate`` value unchanged."""
    if not isinstance(value, str):
        raise TraceContextError("tracestate must be a string")
    if not value.strip():
        return value

    members = value.split(",")
    nonempty_members = [
        member.strip(" \t") for member in members if member.strip(" \t")
    ]
    if len(nonempty_members) > 32:
        raise TraceContextError("tracestate has more than 32 list members")

    keys: set[str] = set()
    for member in nonempty_members:
        if member.count("=") != 1:
            raise TraceContextError("tracestate member must contain one equals sign")
        key, raw_value = member.split("=", 1)
        if not _TRACESTATE_KEY_RE.fullmatch(key):
            raise TraceContextError("tracestate member has an invalid key")
        if key in keys:
            raise TraceContextError("tracestate contains duplicate keys")
        keys.add(key)
        if not raw_value or len(raw_value) > 256:
            raise TraceContextError("tracestate member has an invalid value length")
        if raw_value[-1] in " \t" or any(
            ord(char) < 0x20 or ord(char) > 0x7E for char in raw_value
        ):
            raise TraceContextError("tracestate member has an invalid value")
        if "," in raw_value or "=" in raw_value:
            raise TraceContextError("tracestate member has an invalid value")
    return value


def format_traceparent(context: SpanContext, *, span_id: str | None = None) -> str:
    """Format a participating outbound ``traceparent`` using known version 00.

    A received higher-version header is downgraded to version 00 when this
    process creates a child span.  Version 00 only emits the sampled bit.
    """
    child_span_id = span_id if span_id is not None else new_span_id()
    try:
        child = SpanContext(
            trace_id=context.trace_id,
            span_id=child_span_id,
            trace_flags=context.trace_flags,
        )
    except ValueError as exc:
        raise TraceContextError(str(exc)) from exc
    return f"00-{child.trace_id}-{child.span_id}-{child.trace_flags & 0x01:02x}"


def current_trace_context() -> SpanContext | None:
    """Return the active trace context without creating a trace."""
    return _current_trace_context.get()


def set_trace_context(context: SpanContext | None) -> Token[SpanContext | None]:
    """Set the active context and return the token required for exact reset."""
    return _current_trace_context.set(context)


def reset_trace_context(token: Token[SpanContext | None]) -> None:
    """Restore the context that preceded :func:`set_trace_context`."""
    _current_trace_context.reset(token)


__all__ = [
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
