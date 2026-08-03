"""Alembic migration f7c3b9d2e845: diagnostics_runs.

Runs ``alembic upgrade head`` against a throwaway SQLite file (never a configured
database) and checks the DDL the migration actually produces, including that it
has not drifted from the SQLModel definition -- nothing in this repo runs
autogenerate, so the migration and the model can disagree silently.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import create_engine, text
from sqlmodel import SQLModel

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database
from voicegateway.models.diagnostics_run_model import (  # noqa: F401 - registers table
    DiagnosticsRun,
)


async def _migrated_engine(tmp_path: Path) -> tuple[Database, sa.Engine]:
    db = Database(GatewayConfig(cost_tracking={"db_path": str(tmp_path / "diag.db")}))
    await db.run_migrations()
    return db, create_engine(f"sqlite:///{db.db_file_path}")


def _columns(engine: sa.Engine, table: str) -> dict[str, tuple[str, int]]:
    """``{column: (declared_type, notnull)}`` as SQLite reports it."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).all()
    return {row[1]: (str(row[2]).upper(), int(row[3])) for row in rows}


async def test_migration_creates_diagnostics_runs(tmp_path: Path) -> None:
    db, engine = await _migrated_engine(tmp_path)
    try:
        with engine.connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'table'")
                )
            }
        assert "diagnostics_runs" in tables

        cols = _columns(engine, "diagnostics_runs")
        # The three timestamps hold the ISO-8601 strings the endpoint already
        # returns. Numeric columns would force a reformat and change the exact
        # string the dashboard renders.
        for column in ("created_at", "started_at", "ended_at"):
            assert cols[column][0] == "VARCHAR", (
                f"diagnostics_runs.{column} is {cols[column][0]}; it must stay a "
                "string so the payload is not re-derived through epoch time"
            )
        assert cols["created_at"][1] == 1, (
            "created_at must be NOT NULL: every run has an age"
        )
        assert cols["started_at"][1] == 0, "a queued run has not started"
        assert cols["ended_at"][1] == 0, "a running run has not ended"
        # NULL results means "nothing was recorded", not "{}".
        assert cols["results_json"][1] == 0
        assert cols["checks_json"][1] == 1
        assert cols["config_json"][1] == 1
        assert cols["run_id"][1] == 1
        assert cols["project"][1] == 1

        with engine.connect() as conn:
            indexes = {
                row[0]
                for row in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'index'")
                )
            }
        assert {
            "idx_diagnostics_runs_created_at",
            "idx_diagnostics_runs_project",
        }.issubset(indexes)
    finally:
        engine.dispose()
        await db.dispose()


async def test_no_loss_or_percentile_columns_were_reserved(tmp_path: Path) -> None:
    """Per-call loss/jitter/MOS is not observable server-side and is not built.

    A reserved-but-unwritten loss column becomes a flat 0.0 series that reads as
    a clean bill of health. Percentiles belong to the presentation layer, which
    has to render "max of N" below 10 samples.
    """
    db, engine = await _migrated_engine(tmp_path)
    try:
        cols = set(_columns(engine, "diagnostics_runs"))
        forbidden = {
            c
            for c in cols
            if any(k in c for k in ("loss", "jitter", "mos", "p50", "p95", "p99"))
        }
        assert not forbidden, f"unmeasured/derived columns reserved: {forbidden}"
    finally:
        engine.dispose()
        await db.dispose()


async def test_migration_matches_the_model(tmp_path: Path) -> None:
    """The handwritten migration must produce what ``create_all`` would.

    Nothing here runs alembic autogenerate, so a column added to the model and
    forgotten in the migration (or vice versa) is invisible until a fresh install
    diverges from an upgraded one.
    """
    db, migrated = await _migrated_engine(tmp_path)
    declared = create_engine(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        SQLModel.metadata.create_all(
            declared, tables=[SQLModel.metadata.tables["diagnostics_runs"]]
        )
        assert _columns(migrated, "diagnostics_runs") == _columns(
            declared, "diagnostics_runs"
        )
    finally:
        declared.dispose()
        migrated.dispose()
        await db.dispose()
