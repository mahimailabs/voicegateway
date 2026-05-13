"""Pydantic request/response shapes for the ``virtual_keys`` API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class VirtualKeyCreate(BaseModel):
    """Payload to mint a new virtual key."""

    name: str = Field(min_length=1, max_length=128)
    tenant_id: str | None = None
    issued_by: str | None = None


class VirtualKeyResponse(BaseModel):
    """A virtual-key row safe to expose: never contains the bcrypt hash."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    key_prefix: str
    name: str
    tenant_id: str | None = None
    issued_by: str | None = None
    issued_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class CreatedVirtualKey(BaseModel):
    """Response after ``create``: the plaintext is shown exactly once."""

    plaintext: str
    key: VirtualKeyResponse


class VirtualKeyListResponse(BaseModel):
    """Paginated list response."""

    items: list[VirtualKeyResponse]
    total: int
