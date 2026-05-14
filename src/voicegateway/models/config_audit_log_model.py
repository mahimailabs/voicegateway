"""ORM model for the ``config_audit_log`` table."""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import Index
from sqlmodel import Field, SQLModel


class ConfigAuditLog(SQLModel, table=True):
    """An append-only record of managed-config mutations."""

    __tablename__: ClassVar[str] = "config_audit_log"
    __table_args__ = (
        Index("idx_audit_timestamp", "timestamp"),
        Index("idx_audit_entity", "entity_type", "entity_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    timestamp: float
    entity_type: str
    entity_id: str
    action: str
    changes_json: str | None = None
    source: str = Field(default="api", sa_column_kwargs={"server_default": "api"})
