"""Pydantic request/response shapes for the ``api_keys`` API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiKeyCreate(BaseModel):
    """Payload to mint a new api key."""

    name: str = Field(min_length=1, max_length=128)
    #: Comma-separated and required. No default, because the default used to
    #: be the wildcard and every key the product minted inherited it
    #: (VG-SEC-006). A caller must now say what the key is for.
    scopes: str = Field(min_length=1)
    tenant_id: str | None = None
    issued_by: str | None = None
    project_ids: tuple[str, ...] | None = None

    @field_validator("project_ids")
    @classmethod
    def validate_project_ids(
        cls, value: tuple[str, ...] | None
    ) -> tuple[str, ...] | None:
        if value == ():
            raise ValueError(
                "project_ids must be omitted or contain at least one project"
            )
        if value is not None and any("," in project_id for project_id in value):
            raise ValueError("project_ids entries cannot contain commas")
        return value


class ApiKeyResponse(BaseModel):
    """An api-key row safe to expose: never contains the bcrypt hash."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    key_prefix: str
    name: str
    tenant_id: str | None = None
    issued_by: str | None = None
    issued_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    project_ids: tuple[str, ...] | None = None

    @field_validator("project_ids", mode="before")
    @classmethod
    def decode_project_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item for item in value.split(",") if item)
        return value


class CreatedApiKey(BaseModel):
    """Response after ``create``: the plaintext is shown exactly once."""

    plaintext: str
    key: ApiKeyResponse


class ApiKeyListResponse(BaseModel):
    """Paginated list response."""

    items: list[ApiKeyResponse]
    total: int
