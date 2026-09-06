"""immutable pricing revisions and exact usage ledger

Revision ID: a6c9e2f4b817
Revises: e5c9b41a72f8
Create Date: 2026-09-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a6c9e2f4b817"
down_revision: str | None = "e5c9b41a72f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("project_ids", sa.Text(), nullable=True))
    op.create_table(
        "pricing_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("revision_id", sa.String(), nullable=False),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("scope_json", sa.Text(), nullable=False),
        sa.Column("scope_key", sa.String(), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("contract_version", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at_ns", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("tenant_id", "side", "revision_id"),
    )
    op.create_index(
        "ix_pricing_revisions_tenant_id", "pricing_revisions", ["tenant_id"]
    )
    op.create_index(
        "ix_pricing_revisions_revision_id", "pricing_revisions", ["revision_id"]
    )
    op.create_index("ix_pricing_revisions_active", "pricing_revisions", ["active"])
    op.create_index(
        "ix_pricing_revisions_scope_key", "pricing_revisions", ["scope_key"]
    )
    op.create_index(
        "uq_pricing_revisions_active_scope",
        "pricing_revisions",
        ["tenant_id", "side", "scope_key"],
        unique=True,
        sqlite_where=sa.text("active = 1"),
        postgresql_where=sa.text("active"),
    )
    op.create_table(
        "accounting_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("attempt_id", sa.String(), nullable=False),
        sa.Column("component", sa.String(), nullable=False),
        sa.Column("modality", sa.String(), nullable=False),
        sa.Column("offering", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("turn_id", sa.String(), nullable=True),
        sa.Column("producer_id", sa.String(), nullable=False),
        sa.Column("ownership_mode", sa.String(), nullable=False),
        sa.Column("pricing_binding_id", sa.String(), nullable=True),
        sa.Column("acquisition_revision_id", sa.String(), nullable=True),
        sa.Column("selling_revision_id", sa.String(), nullable=True),
        sa.Column("occurred_at_ns", sa.BigInteger(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("envelope_json", sa.Text(), nullable=False),
        sa.Column("acquisition_total_usd", sa.String(), nullable=True),
        sa.Column("selling_total_usd", sa.String(), nullable=True),
        sa.Column("acquisition_complete", sa.Boolean(), nullable=False),
        sa.Column("selling_complete", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("receipt_id", sa.String(), nullable=False),
        sa.Column("created_at_ns", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("tenant_id", "project_id", "event_id"),
        sa.UniqueConstraint("tenant_id", "project_id", "attempt_id"),
    )
    for column in ("tenant_id", "project_id", "event_id", "session_id", "status"):
        op.create_index(f"ix_accounting_usage_{column}", "accounting_usage", [column])
    op.create_table(
        "accounting_projection_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(), nullable=True),
        sa.Column("projected_at_ns", sa.BigInteger(), nullable=True),
        sa.UniqueConstraint("tenant_id", "project_id", "event_id"),
    )
    for column in ("tenant_id", "project_id", "event_id"):
        op.create_index(
            f"ix_accounting_projection_outbox_{column}",
            "accounting_projection_outbox",
            [column],
        )
    op.create_table(
        "accounting_rejections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("receipt_id", sa.String(), nullable=False),
        sa.Column("created_at_ns", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("tenant_id", "project_id", "event_id", "payload_hash"),
    )
    op.create_index(
        "ix_accounting_rejections_tenant_id", "accounting_rejections", ["tenant_id"]
    )
    op.create_index(
        "ix_accounting_rejections_project_id", "accounting_rejections", ["project_id"]
    )
    op.create_table(
        "accounting_ownership",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("component", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("updated_at_ns", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("tenant_id", "project_id", "component"),
    )
    op.create_index(
        "ix_accounting_ownership_tenant_id", "accounting_ownership", ["tenant_id"]
    )
    op.create_index(
        "ix_accounting_ownership_project_id", "accounting_ownership", ["project_id"]
    )
    op.create_table(
        "prepared_pricing_bindings",
        sa.Column("binding_id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("component", sa.String(), nullable=False),
        sa.Column("offering", sa.String(), nullable=False),
        sa.Column("acquisition_revision_id", sa.String(), nullable=True),
        sa.Column("selling_revision_id", sa.String(), nullable=True),
        sa.Column("ownership_mode", sa.String(), nullable=False),
        sa.Column("prepared_at_ns", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "ix_prepared_pricing_bindings_tenant_id",
        "prepared_pricing_bindings",
        ["tenant_id"],
    )
    op.create_index(
        "ix_prepared_pricing_bindings_project_id",
        "prepared_pricing_bindings",
        ["project_id"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "This additive accounting migration has no destructive downgrade. "
        "Roll back the application while retaining ledger tables and project allowlists."
    )
