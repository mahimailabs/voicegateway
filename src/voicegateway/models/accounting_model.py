"""Append-only accounting ledger entities."""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import Column, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


class PricingRevision(SQLModel, table=True):
    __tablename__: ClassVar[str] = "pricing_revisions"
    __table_args__ = (UniqueConstraint("tenant_id", "side", "revision_id"),)

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(index=True)
    revision_id: str = Field(index=True)
    side: str
    scope_json: str = Field(sa_column=Column(Text, nullable=False))
    scope_key: str = Field(index=True)
    content_json: str = Field(sa_column=Column(Text, nullable=False))
    content_hash: str
    contract_version: int
    currency: str
    active: bool = Field(default=False, index=True)
    created_at_ns: int


class AccountingUsage(SQLModel, table=True):
    __tablename__: ClassVar[str] = "accounting_usage"
    __table_args__ = (
        UniqueConstraint("tenant_id", "project_id", "event_id"),
        UniqueConstraint("tenant_id", "project_id", "component", "attempt_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(index=True)
    project_id: str = Field(index=True)
    event_id: str = Field(index=True)
    attempt_id: str
    component: str
    modality: str
    offering: str
    model_id: str
    session_id: str = Field(index=True)
    turn_id: str | None = None
    producer_id: str
    ownership_mode: str
    pricing_binding_id: str | None = None
    acquisition_revision_id: str | None = None
    selling_revision_id: str | None = None
    occurred_at_ns: int
    payload_hash: str
    envelope_json: str = Field(sa_column=Column(Text, nullable=False))
    acquisition_total_usd: str | None = None
    selling_total_usd: str | None = None
    acquisition_complete: bool
    selling_complete: bool
    status: str = Field(index=True)
    receipt_id: str
    created_at_ns: int


class AccountingProjection(SQLModel, table=True):
    __tablename__: ClassVar[str] = "accounting_projection_outbox"
    __table_args__ = (UniqueConstraint("tenant_id", "project_id", "event_id"),)

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(index=True)
    project_id: str = Field(index=True)
    event_id: str = Field(index=True)
    payload_json: str = Field(sa_column=Column(Text, nullable=False))
    attempts: int = 0
    last_error_code: str | None = None
    projected_at_ns: int | None = None


class AccountingRejection(SQLModel, table=True):
    __tablename__: ClassVar[str] = "accounting_rejections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "project_id", "event_id", "payload_hash"),
    )

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(index=True)
    project_id: str = Field(index=True)
    event_id: str
    payload_hash: str
    code: str
    receipt_id: str
    created_at_ns: int


class AccountingOwnership(SQLModel, table=True):
    __tablename__: ClassVar[str] = "accounting_ownership"
    __table_args__ = (UniqueConstraint("tenant_id", "project_id", "component"),)

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(index=True)
    project_id: str = Field(index=True)
    component: str
    mode: str
    updated_at_ns: int


class PreparedPricingBinding(SQLModel, table=True):
    __tablename__: ClassVar[str] = "prepared_pricing_bindings"

    binding_id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    project_id: str = Field(index=True)
    component: str
    offering: str
    acquisition_revision_id: str | None = None
    selling_revision_id: str | None = None
    ownership_mode: str
    prepared_at_ns: int
