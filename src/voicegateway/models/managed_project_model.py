"""ORM model for the ``managed_projects`` table."""

from __future__ import annotations

from typing import ClassVar

from sqlmodel import Field, SQLModel


class ManagedProject(SQLModel, table=True):
    """A configured project: cost budgets, default stack, branding, guardrails."""

    __tablename__: ClassVar[str] = "managed_projects"

    project_id: str = Field(primary_key=True)
    name: str
    description: str = Field(default="", sa_column_kwargs={"server_default": ""})
    daily_budget: float = Field(default=0.0, sa_column_kwargs={"server_default": "0"})
    budget_action: str = Field(
        default="warn", sa_column_kwargs={"server_default": "warn"}
    )
    default_stack: str | None = None
    stt_model: str | None = None
    llm_model: str | None = None
    tts_model: str | None = None
    tags: str = Field(default="[]", sa_column_kwargs={"server_default": "[]"})
    created_at: float
    updated_at: float

    # 0006: branding payload (JSON-encoded)
    branding_json: str | None = None

    # 0007: guardrail policy overlay (JSON-encoded)
    guardrail_policy_json: str | None = None
