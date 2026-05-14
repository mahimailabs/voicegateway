"""ORM model for the ``managed_providers`` table."""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import Index
from sqlmodel import Field, SQLModel


class ManagedProvider(SQLModel, table=True):
    """A configured upstream provider (OpenAI, Deepgram, ...) with encrypted key."""

    __tablename__: ClassVar[str] = "managed_providers"
    __table_args__ = (
        Index("idx_managed_providers_project", "project"),
        Index("idx_managed_providers_type", "provider_type"),
    )

    provider_id: str = Field(primary_key=True)
    provider_type: str
    api_key_encrypted: str = Field(default="", sa_column_kwargs={"server_default": ""})
    base_url: str | None = None
    extra_config: str = Field(default="{}", sa_column_kwargs={"server_default": "{}"})
    created_at: float
    updated_at: float
    project: str | None = None
