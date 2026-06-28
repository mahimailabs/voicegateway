"""ORM model for the ``tenants`` table."""

from __future__ import annotations

from typing import ClassVar

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class Tenant(SQLModel, table=True):
    """A registered tenant: identity, status, and optional configuration."""

    __tablename__: ClassVar[str] = "tenants"
    __table_args__: ClassVar[tuple] = (
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'archived')",
            name="ck_tenants_status",
        ),
    )

    tenant_id: str = Field(primary_key=True)
    name: str = Field(default="", sa_column_kwargs={"server_default": ""})
    created_at: str = Field(
        default="",
        sa_column_kwargs={"server_default": sa.text("CURRENT_TIMESTAMP")},
    )
    status: str = Field(default="active", sa_column_kwargs={"server_default": "active"})
    retention_days: int | None = None
    branding_json: str | None = None
