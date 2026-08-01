"""Alembic b3e7c1a95d24: the Go runtime pair on ``node_samples``.

``node_samples`` had no drift test at all, which is the gap this file closes.
Nothing in this repo runs autogenerate, and the production path is
``run_migrations`` rather than ``create_all`` (``core/database.py`` defines
``create_all`` but no production caller reaches it, while nearly every repository
test does). So a column added to the model and forgotten in the migration passes
the whole unit suite and then has no column in a real deployment.

Runs ``alembic upgrade head`` against a throwaway SQLite file, never a configured
database.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import create_engine, text
from sqlmodel import SQLModel

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database
from voicegateway.models.node_sample_model import (  # noqa: F401 - registers table
    NodeSample,
)
from voicegateway.repository import node_samples_repository as repo


async def _migrated_engine(tmp_path: Path) -> tuple[Database, sa.Engine]:
    db = Database(GatewayConfig(cost_tracking={"db_path": str(tmp_path / "nodes.db")}))
    await db.run_migrations()
    return db, create_engine(f"sqlite:///{db.db_file_path}")


def _columns(engine: sa.Engine, table: str) -> dict[str, tuple[str, int]]:
    """``{column: (declared_type, notnull)}`` as SQLite reports it."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).all()
    return {row[1]: (str(row[2]).upper(), int(row[3])) for row in rows}


async def test_the_migration_adds_the_go_runtime_pair(tmp_path: Path) -> None:
    db, engine = await _migrated_engine(tmp_path)
    try:
        columns = _columns(engine, "node_samples")
        # BIGINT, for the same reason the memory columns are: an INT4 takes
        # neither a large heap nor a large limit, silently on SQLite and as a
        # 500 on PostgreSQL.
        assert columns["heap_inuse_bytes"][0] == "BIGINT"
        assert columns["go_goroutines"][0] == "INTEGER"
        # Nullable, both. NULL means "not measured"; a 0 in go_goroutines would
        # describe a process with no goroutines, which is not a state a running
        # Go program is in.
        assert columns["heap_inuse_bytes"][1] == 0
        assert columns["go_goroutines"][1] == 0
    finally:
        engine.dispose()
        await db.dispose()


async def test_migration_matches_the_model(tmp_path: Path) -> None:
    """The handwritten migration must produce what ``create_all`` would.

    This is the check that catches a column added to one and not the other. It
    would have been red before b3e7c1a95d24 existed, because the model carried
    the two new columns and the migrated schema did not.
    """
    db, migrated = await _migrated_engine(tmp_path)
    declared = create_engine(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        SQLModel.metadata.create_all(
            declared, tables=[SQLModel.metadata.tables["node_samples"]]
        )
        assert _columns(migrated, "node_samples") == _columns(declared, "node_samples")
    finally:
        declared.dispose()
        migrated.dispose()
        await db.dispose()


def test_the_new_columns_are_registered_as_gauges_not_counters() -> None:
    """A gauge read as a counter would be diffed, and a heap size is not a rate.

    ``read_counter_rate`` raises on a column outside ``COUNTER_COLUMNS``, so
    membership here is what keeps a caller from asking for the wrong thing.
    """
    for column in ("heap_inuse_bytes", "go_goroutines"):
        assert column in repo.GAUGE_COLUMNS
        assert column not in repo.COUNTER_COLUMNS
        assert column in repo.VALUE_COLUMNS


def test_every_value_column_exists_on_the_model() -> None:
    """The column sets and the table cannot drift apart silently.

    ``insert_samples`` raises ValueError on an unknown key, so a name in
    VALUE_COLUMNS with no matching column would fail only at write time, on a
    deployment, with a real scrape in hand.
    """
    model_columns = set(SQLModel.metadata.tables["node_samples"].columns.keys())
    missing = sorted(repo.VALUE_COLUMNS - model_columns)
    assert not missing, f"declared as values but absent from the table: {missing}"


def test_every_value_column_exists_on_the_read_row() -> None:
    """VALUE_COLUMNS, the model and NodeSampleRow are three lists of one thing.

    This is the one that actually bit: the columns were added to the model and to
    GAUGE_COLUMNS, and NodeSampleRow (a separate dataclass the read path maps
    into) was missed. correlate_window then did getattr(row, column) for every
    gauge and raised AttributeError, taking out the whole nodes surface. Adding a
    column now fails here instead of at request time.
    """
    import dataclasses

    row_fields = {f.name for f in dataclasses.fields(repo.NodeSampleRow)}
    missing = sorted(repo.VALUE_COLUMNS - row_fields)
    assert not missing, f"declared as values but absent from NodeSampleRow: {missing}"
