"""Alembic migration d2f6b8a1c3e5: agent_probe_results.

Runs ``alembic upgrade head`` against a throwaway SQLite file (never a configured
database) and checks the DDL the migration actually produces, including that it
has not drifted from the SQLModel definition -- nothing in this repo runs
autogenerate, so the migration and the model can disagree silently. This table
shipped with exactly that disagreement: the migration declared ``result_json``
as ``sa.Text()`` while the model declared a plain ``str`` (VARCHAR), which SQLite
hides and PostgreSQL does not.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import create_engine, text
from sqlmodel import SQLModel

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database
from voicegateway.models.agent_probe_result_model import (  # noqa: F401 - registers table
    AgentProbeResult,
)


async def _migrated_engine(tmp_path: Path) -> tuple[Database, sa.Engine]:
    db = Database(GatewayConfig(cost_tracking={"db_path": str(tmp_path / "probe.db")}))
    await db.run_migrations()
    return db, create_engine(f"sqlite:///{db.db_file_path}")


def _columns(engine: sa.Engine, table: str) -> dict[str, tuple[str, int]]:
    """``{column: (declared_type, notnull)}`` as SQLite reports it."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).all()
    return {row[1]: (str(row[2]).upper(), int(row[3])) for row in rows}


async def test_migration_creates_agent_probe_results(tmp_path: Path) -> None:
    db, engine = await _migrated_engine(tmp_path)
    try:
        with engine.connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'table'")
                )
            }
        assert "agent_probe_results" in tables

        cols = _columns(engine, "agent_probe_results")
        # The cached probe payload is an opaque serialized document of no fixed
        # length, so it is TEXT and not VARCHAR(n).
        assert cols["result_json"][0] == "TEXT", (
            f"agent_probe_results.result_json is {cols['result_json'][0]}; the "
            "migration declares sa.Text() and the model must agree"
        )
        assert cols["result_json"][1] == 1, (
            "a cached row without a payload is not a cache hit"
        )
        # Epoch seconds the probe ran, so the UI can say "measured Ns ago". A row
        # with no timestamp could not be aged, so it is NOT NULL.
        assert cols["created_at"][0] == "FLOAT"
        assert cols["created_at"][1] == 1
    finally:
        engine.dispose()
        await db.dispose()


async def test_migration_matches_the_model(tmp_path: Path) -> None:
    """The handwritten migration must produce what ``create_all`` would.

    Nothing here runs alembic autogenerate, so a column added to the model and
    forgotten in the migration (or vice versa) is invisible until a fresh install
    diverges from an upgraded one. This is the assertion that catches the
    VARCHAR-vs-Text drift, because it compares compiled DDL rather than trusting
    either side's declaration.
    """
    db, migrated = await _migrated_engine(tmp_path)
    declared = create_engine(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        SQLModel.metadata.create_all(
            declared, tables=[SQLModel.metadata.tables["agent_probe_results"]]
        )
        assert _columns(migrated, "agent_probe_results") == _columns(
            declared, "agent_probe_results"
        )
    finally:
        declared.dispose()
        migrated.dispose()
        await db.dispose()
