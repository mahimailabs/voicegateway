"""ORM model for the ``api_keys`` table."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ApiKey(SQLModel, table=True):
    """A long-lived authentication token issued to a tenant operator."""

    __tablename__: ClassVar[str] = "api_keys"

    id: int | None = Field(default=None, primary_key=True)
    key_prefix: str = Field(index=True)
    key_hash: str
    name: str
    tenant_id: str | None = Field(default=None, index=True)
    issued_by: str | None = None
    issued_at: datetime = Field(  # type: ignore[call-overload]
        default_factory=_utcnow,
        sa_type=DateTime(timezone=True),
    )
    last_used_at: datetime | None = Field(  # type: ignore[call-overload]
        default=None, sa_type=DateTime(timezone=True)
    )
    revoked_at: datetime | None = Field(  # type: ignore[call-overload]
        default=None, sa_type=DateTime(timezone=True)
    )
