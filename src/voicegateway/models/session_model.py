"""ORM model for the ``sessions`` table."""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import Index
from sqlmodel import Field, SQLModel


class Session(SQLModel, table=True):
    """One row per logical voice session.

    Populated by CostTracker on the first request of a session;
    total_cost_usd and request_count accumulate per request. ended_at
    stays NULL until the session closes. Aggregate columns
    (talk_time_seconds, response_speed_*, replay_size_bytes,
    budget_*, routed_*, guardrails_*) land via session-finalize hooks.
    """

    __tablename__: ClassVar[str] = "sessions"
    __table_args__ = (
        Index("idx_sessions_project", "project"),
        Index("idx_sessions_started_at", "started_at"),
    )

    id: str = Field(primary_key=True)
    project: str
    started_at: str
    ended_at: str | None = None
    modalities: str = Field(default="", sa_column_kwargs={"server_default": ""})
    total_cost_usd: float | None = Field(default=0.0)
    request_count: int | None = Field(default=0)

    # 0003: talk-time + response-speed aggregates
    talk_time_seconds: float | None = None
    per_minute_cost_usd: float | None = None
    response_speed_p50_ms: int | None = None
    response_speed_p95_ms: int | None = None
    talk_over_rate: float | None = None

    # 0004: replay aggregate
    replay_size_bytes: int | None = None

    # 0005: tenant attribution
    tenant_id: str | None = None

    # 0006: routing + budget
    budget_ms: int | None = None
    budget_ms_used: int | None = None
    budget_overrun: int | None = None
    routed_llm: str | None = None
    routed_tts: str | None = None

    # 0007: guardrails
    guardrails_active: int | None = None
    guardrails_bypassed: int | None = None
    guardrail_policy_snapshot_json: str | None = None
