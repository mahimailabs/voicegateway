"""ORM model for the ``latency_observations`` table (0006)."""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import Index, text
from sqlmodel import Field, SQLModel


class LatencyObservation(SQLModel, table=True):
    """One denormalized (project, provider, modality) latency rollup."""

    __tablename__: ClassVar[str] = "latency_observations"
    __table_args__ = (
        Index("idx_latency_obs_project_provider", "project_id", "provider", "modality"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: str
    provider: str
    modality: str
    p50_ms: int | None = None
    p95_ms: int | None = None
    sample_count: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    window_start: str
    window_end: str
    refreshed_at: str = Field(
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
