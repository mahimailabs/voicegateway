"""Accounting migration/model parity and non-destructive rollback guards."""

from __future__ import annotations

import asyncio
import runpy
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import BigInteger, create_engine, inspect
from sqlmodel import SQLModel

from voicegateway.accounting.contracts import (
    PricingDimension,
    PricingRevisionCreate,
    PricingSide,
)
from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database
from voicegateway.models import accounting_model as _accounting_models  # noqa: F401
from voicegateway.models.api_key_model import ApiKey
from voicegateway.services.accounting_service import AccountingService, RevisionConflict
from voicegateway.services.storage_service import StorageService

_TABLES = (
    "pricing_revisions",
    "accounting_usage",
    "accounting_projection_outbox",
    "accounting_rejections",
    "accounting_ownership",
    "prepared_pricing_bindings",
)


def _shape(engine: sa.Engine, table: str) -> dict[str, tuple[str, bool]]:
    return {
        item["name"]: (str(item["type"]), bool(item["nullable"]))
        for item in inspect(engine).get_columns(table)
    }


async def test_accounting_migration_matches_sqlmodel(tmp_path: Path) -> None:
    database = Database(
        GatewayConfig(cost_tracking={"db_path": str(tmp_path / "migrated.db")})
    )
    await database.run_migrations()
    migrated = create_engine(f"sqlite:///{database.db_file_path}")
    declared = create_engine(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        SQLModel.metadata.create_all(
            declared,
            tables=[SQLModel.metadata.tables[name] for name in _TABLES]
            + [ApiKey.__table__],
        )
        for table in _TABLES:
            assert _shape(migrated, table) == _shape(declared, table), table
        assert (
            _shape(migrated, "api_keys")["project_ids"]
            == _shape(declared, "api_keys")["project_ids"]
        )
    finally:
        migrated.dispose()
        declared.dispose()
        await database.dispose()


def test_every_accounting_nanosecond_column_is_bigint() -> None:
    for table in _TABLES:
        for column in SQLModel.metadata.tables[table].columns:
            if column.name.endswith("_ns"):
                assert isinstance(column.type, BigInteger), f"{table}.{column.name}"


def test_accounting_downgrade_refuses_to_destroy_ledger() -> None:
    migration = runpy.run_path(
        str(
            Path(__file__).resolve().parents[4]
            / "alembic/versions/a6c9e2f4b817_immutable_accounting_ledger.py"
        )
    )
    with pytest.raises(RuntimeError, match="no destructive downgrade"):
        migration["downgrade"]()


def _revision(revision_id: str) -> PricingRevisionCreate:
    return PricingRevisionCreate(
        revision_id=revision_id,
        side="selling",
        scope={"offering": "provider/model"},
        rates=({"dimension": "requests", "unit": "request", "rate": "1"},),
        unsupported_dimensions=tuple(
            item for item in PricingDimension if item is not PricingDimension.REQUESTS
        ),
    )


async def test_concurrent_activation_leaves_one_active_revision(tmp_path: Path) -> None:
    storage = StorageService(str(tmp_path / "activation.db"))
    async with storage.session() as session:
        service = AccountingService(session, tenant_id="tenant-a")
        for revision_id in ("v0", "v1", "v2"):
            await service.create_revision(_revision(revision_id))
        await service.activate_revision(PricingSide.SELLING, "v0")

    async def activate(revision_id: str):
        async with storage.session() as session:
            try:
                await AccountingService(
                    session, tenant_id="tenant-a"
                ).activate_revision(
                    PricingSide.SELLING,
                    revision_id,
                    expected_current_revision_id="v0",
                )
                return "activated"
            except RevisionConflict:
                return "conflict"

    outcomes = await asyncio.gather(activate("v1"), activate("v2"))
    assert sorted(outcomes) == ["activated", "conflict"]
    async with storage.session() as session:
        rows = (
            await session.execute(
                sa.select(SQLModel.metadata.tables["pricing_revisions"]).where(
                    SQLModel.metadata.tables["pricing_revisions"].c.active.is_(True)
                )
            )
        ).all()
    assert len(rows) == 1
    await storage.aclose()
