"""ORM model for the ``managed_models`` table."""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import Index
from sqlmodel import Field, SQLModel


class ManagedModel(SQLModel, table=True):
    """A registered model bound to a managed provider."""

    __tablename__: ClassVar[str] = "managed_models"
    __table_args__ = (
        Index("idx_managed_models_modality", "modality"),
        Index("idx_managed_models_provider", "provider_id"),
    )

    model_id: str = Field(primary_key=True)
    modality: str
    provider_id: str
    model_name: str
    display_name: str | None = None
    default_language: str | None = None
    default_voice: str | None = None
    extra_config: str = Field(default="{}", sa_column_kwargs={"server_default": "{}"})
    enabled: int = Field(default=1, sa_column_kwargs={"server_default": "1"})
    created_at: float
    updated_at: float
