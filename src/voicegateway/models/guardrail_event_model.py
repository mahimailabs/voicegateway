"""ORM model for the ``guardrail_events`` table (0007)."""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import CheckConstraint, Index, text
from sqlmodel import Field, SQLModel


class GuardrailEvent(SQLModel, table=True):
    """One guardrail decision event (fired or bypassed) for a session turn."""

    __tablename__: ClassVar[str] = "guardrail_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('fired', 'bypassed')",
            name="ck_guardrail_events_event_type",
        ),
        Index("idx_guardrail_events_session_id", "session_id"),
        Index("idx_guardrail_events_created_at", "created_at"),
        Index("idx_guardrail_events_category", "category"),
        Index("idx_guardrail_events_action", "action"),
        Index("idx_guardrail_events_tenant_id", "tenant_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    event_type: str
    session_id: str
    tenant_id: str | None = None
    turn_index: int | None = None
    category: str | None = None
    action: str | None = None
    context_excerpt: str | None = None
    created_at: str = Field(
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
