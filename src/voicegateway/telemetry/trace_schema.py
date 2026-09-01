"""Immutable Wave 0 trace vocabulary.

The models mirror OpenTelemetry's trace data model without importing an SDK or
OTLP protobuf types.  They are intentionally internal until ingestion and
storage have stable public contracts.
"""

from __future__ import annotations

import base64
import math
import re
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
    model_serializer,
    model_validator,
)

TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SPAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")
VERSION_RE = re.compile(r"^[0-9a-f]{2}$")
MAX_SIGNED_INT64 = 2**63 - 1
MIN_SIGNED_INT64 = -(2**63)


class TelemetryContractError(ValueError):
    """Raised when a value cannot be represented by the telemetry contract."""


def _validate_trace_id(value: str) -> str:
    if not TRACE_ID_RE.fullmatch(value) or value == "0" * 32:
        raise ValueError(
            "trace_id must be 32 lowercase non-zero hexadecimal characters"
        )
    return value


def _validate_span_id(value: str) -> str:
    if not SPAN_ID_RE.fullmatch(value) or value == "0" * 16:
        raise ValueError("span_id must be 16 lowercase non-zero hexadecimal characters")
    return value


def _validate_any_value(value: Any, *, path: str = "value") -> None:
    """Validate the recursive OpenTelemetry ``AnyValue`` subset we persist."""
    if value is None or isinstance(value, (str, bool, bytes)):
        return
    # bool is an int subclass, hence the explicit branch above.
    if isinstance(value, int):
        if not MIN_SIGNED_INT64 <= value <= MAX_SIGNED_INT64:
            raise TelemetryContractError(f"{path} integer is outside signed int64")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TelemetryContractError(f"{path} float must be finite")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _validate_any_value(child, path=f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or not key:
                raise TelemetryContractError(
                    f"{path} map keys must be non-empty strings"
                )
            _validate_any_value(child, path=f"{path}.{key}")
        return
    raise TelemetryContractError(
        f"{path} must be an OpenTelemetry AnyValue, got {type(value).__name__}"
    )


def _freeze_any_value(value: Any) -> Any:
    """Recursively detach mutable caller-owned containers from a contract value."""
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_any_value(child) for child in value)
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_any_value(child) for key, child in value.items()}
        )
    return value


def _json_value(value: Any) -> Any:
    """Return a non-OTLP JSON representation of an ``AnyValue``.

    Byte arrays use base64, as OpenTelemetry recommends for non-OTLP JSON.
    This representation is deliberately lossy about the original byte type;
    OTLP ingestion later keeps the native wire type.
    """
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, tuple):
        return [_json_value(child) for child in value]
    if isinstance(value, list):
        return [_json_value(child) for child in value]
    if isinstance(value, Mapping):
        return {key: _json_value(child) for key, child in value.items()}
    return value


class AnyValue(RootModel[Any]):
    """A validated recursive OpenTelemetry value used by attributes and events."""

    model_config = ConfigDict(frozen=True)

    @field_validator("root")
    @classmethod
    def _is_any_value(cls, value: Any) -> Any:
        _validate_any_value(value)
        return _freeze_any_value(value)

    @model_serializer(mode="plain")
    def _serialize(self) -> Any:
        return _json_value(self.root)


class Attribute(BaseModel):
    """One OpenTelemetry attribute, represented as an OTLP-style key/value pair."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1)
    value: AnyValue

    @field_validator("key")
    @classmethod
    def _key_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("attribute key must not be blank")
        return value


def _unique_attribute_keys(
    attributes: tuple[Attribute, ...], *, field: str
) -> tuple[Attribute, ...]:
    keys = [attribute.key for attribute in attributes]
    if len(keys) != len(set(keys)):
        raise ValueError(f"{field} contains duplicate attribute keys")
    return attributes


class SpanKind(StrEnum):
    """OpenTelemetry span kinds."""

    INTERNAL = "internal"
    SERVER = "server"
    CLIENT = "client"
    PRODUCER = "producer"
    CONSUMER = "consumer"


class SpanStatusCode(StrEnum):
    """OpenTelemetry's three status values."""

    UNSET = "unset"
    OK = "ok"
    ERROR = "error"


class SpanStatus(BaseModel):
    """Span terminal status; descriptions are reserved for errors."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: SpanStatusCode = SpanStatusCode.UNSET
    message: str | None = None

    @model_validator(mode="after")
    def _message_requires_error(self) -> SpanStatus:
        if self.message is not None and self.code is not SpanStatusCode.ERROR:
            raise ValueError("status message is only valid when code is error")
        return self


class SpanContext(BaseModel):
    """The W3C-compatible identity of one span.

    ``trace_flags`` is preserved as an unsigned byte.  The sampled bit is a
    hint from an upstream caller, not an authorization or storage decision.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = "00"
    trace_id: str
    span_id: str
    trace_flags: int = Field(default=0, ge=0, le=255)
    tracestate: str | None = None
    is_remote: bool = False

    @field_validator("version")
    @classmethod
    def _version_is_valid(cls, value: str) -> str:
        if not VERSION_RE.fullmatch(value) or value == "ff":
            raise ValueError(
                "version must be two lowercase hex characters other than ff"
            )
        return value

    @field_validator("trace_id")
    @classmethod
    def _trace_id_is_valid(cls, value: str) -> str:
        return _validate_trace_id(value)

    @field_validator("span_id")
    @classmethod
    def _span_id_is_valid(cls, value: str) -> str:
        return _validate_span_id(value)

    @property
    def sampled(self) -> bool:
        """Whether the least-significant sampled bit is set."""
        return bool(self.trace_flags & 0x01)


class SpanAttributes(BaseModel):
    """Explicit, non-sensitive correlation fields for a span.

    ``tenant_id`` is an internal field populated by authenticated context at an
    ingestion boundary.  An arbitrary OTLP attribute with this name must never
    overwrite it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str | None = None
    project_id: str | None = None
    session_id: str | None = None
    turn_index: int | None = Field(default=None, ge=0)
    agent_revision: str | None = None
    workflow_version: str | None = None


class InstrumentationScope(BaseModel):
    """The library or component that produced a span."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    version: str | None = None
    schema_url: str | None = None
    attributes: tuple[Attribute, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _attribute_keys_are_unique(self) -> InstrumentationScope:
        _unique_attribute_keys(
            self.attributes, field="instrumentation scope attributes"
        )
        return self


class SpanEvent(BaseModel):
    """A timestamped event within a span."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    timestamp_unix_nano: int = Field(ge=0)
    attributes: tuple[Attribute, ...] = Field(default_factory=tuple)
    dropped_attributes_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _attribute_keys_are_unique(self) -> SpanEvent:
        _unique_attribute_keys(self.attributes, field="event attributes")
        return self


class SpanLink(BaseModel):
    """A causal relationship that is not represented by a second parent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    context: SpanContext
    attributes: tuple[Attribute, ...] = Field(default_factory=tuple)
    dropped_attributes_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _attribute_keys_are_unique(self) -> SpanLink:
        _unique_attribute_keys(self.attributes, field="link attributes")
        return self


class SpanRecord(BaseModel):
    """One complete trace span, independent of storage and transport."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1)
    name: str = Field(min_length=1)
    context: SpanContext
    parent_span_id: str | None = None
    kind: SpanKind = SpanKind.INTERNAL
    status: SpanStatus = Field(default_factory=SpanStatus)
    start_time_unix_nano: int = Field(ge=0)
    end_time_unix_nano: int = Field(ge=0)
    resource_attributes: tuple[Attribute, ...] = Field(default_factory=tuple)
    instrumentation_scope: InstrumentationScope
    correlation: SpanAttributes = Field(default_factory=SpanAttributes)
    attributes: tuple[Attribute, ...] = Field(default_factory=tuple)
    events: tuple[SpanEvent, ...] = Field(default_factory=tuple)
    links: tuple[SpanLink, ...] = Field(default_factory=tuple)
    dropped_attributes_count: int = Field(default=0, ge=0)
    dropped_events_count: int = Field(default=0, ge=0)
    dropped_links_count: int = Field(default=0, ge=0)

    @field_validator("parent_span_id")
    @classmethod
    def _parent_span_id_is_valid(cls, value: str | None) -> str | None:
        return None if value is None else _validate_span_id(value)

    @model_validator(mode="after")
    def _shape_is_consistent(self) -> SpanRecord:
        if self.parent_span_id == self.context.span_id:
            raise ValueError("parent_span_id must not equal span_id")
        if self.end_time_unix_nano < self.start_time_unix_nano:
            raise ValueError("end_time_unix_nano must not precede start_time_unix_nano")
        _unique_attribute_keys(self.resource_attributes, field="resource attributes")
        _unique_attribute_keys(self.attributes, field="span attributes")
        return self


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
    "TelemetryContractError",
]
