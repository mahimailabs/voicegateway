"""Data models for the ``requests`` table.

The producer-facing :class:`RequestRecord` stays a plain dataclass so
existing call sites in middleware keep working unchanged. The companion
:class:`Request` SQLModel is the ORM mapping used by the request-log
repository and registered with ``SQLModel.metadata`` so Alembic autogen
picks up the table.

Why two classes: the column name ``metadata`` clashes with SQLAlchemy's
DeclarativeBase reserved attribute. Mapping ``metadata`` requires
:func:`sqlalchemy.Column("metadata", ...)` with a different Python
attribute name, which would break every producer that does
``RequestRecord(metadata={...})``. Keeping the dataclass as the
producer surface sidesteps the clash without changing 5+ call sites.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from sqlalchemy import Column, Index, Text
from sqlmodel import Field, SQLModel


@dataclass
class RequestRecord:
    """A single inference request record (producer-side dataclass)."""

    id: str
    timestamp: float
    modality: str  # 'stt', 'llm', 'tts'
    model_id: str  # 'deepgram/nova-3'
    provider: str  # 'deepgram'
    project: str = "default"  # project ID the request is tagged with
    input_units: float = 0.0  # minutes (stt), tokens (llm), characters (tts)
    output_units: float = 0.0  # tokens (llm)
    cached_input_units: float = 0.0  # cached prompt tokens (llm); 0 for stt/tts
    cost_usd: float = 0.0
    pricing_source: str = (
        ""  # e.g. "genai-prices@0.0.57" or "voicegateway-catalog@2026-05-04"
    )
    ttfb_ms: float | None = None
    total_latency_ms: float | None = None
    status: str = "success"  # 'success', 'error', 'fallback'
    fallback_from: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None  # ContextVar-derived session correlation


class Request(SQLModel, table=True):
    """ORM mapping for the ``requests`` table.

    The Python attribute ``metadata_json`` maps to the SQL column
    ``metadata`` (TEXT). Producers continue to construct
    :class:`RequestRecord` dataclasses; the request-log repository
    converts a record to a :class:`Request` row at write time.
    """

    __tablename__: ClassVar[str] = "requests"
    __table_args__ = (
        Index("idx_requests_timestamp", "timestamp"),
        Index("idx_requests_model", "model_id"),
        Index("idx_requests_modality", "modality"),
        Index("idx_requests_project", "project"),
        Index("idx_requests_project_timestamp", "project", "timestamp"),
        Index("idx_requests_session_id", "session_id"),
    )

    id: str = Field(primary_key=True)
    timestamp: float
    project: str = Field(
        default="default", sa_column_kwargs={"server_default": "default"}
    )
    modality: str
    model_id: str
    provider: str
    input_units: float | None = Field(default=0.0)
    output_units: float | None = Field(default=0.0)
    cached_input_units: float | None = Field(
        default=0.0, sa_column_kwargs={"server_default": "0"}
    )
    cost_usd: float | None = Field(default=0.0)
    pricing_source: str = Field(default="", sa_column_kwargs={"server_default": ""})
    ttfb_ms: float | None = None
    total_latency_ms: float | None = None
    status: str | None = Field(default="success")
    fallback_from: str | None = None
    error_message: str | None = None
    metadata_json: str | None = Field(
        default=None,
        sa_column=Column("metadata", Text, nullable=True),
    )
    session_id: str | None = None

    # 0005: tenant attribution
    tenant_id: str | None = None
