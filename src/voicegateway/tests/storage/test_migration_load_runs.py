"""Alembic c5a91e37f2b8: ``load_runs`` + ``load_run_tests``.

Four silent failures live in adding a table here, and each has a test below:

1. A ``table=True`` model that is not re-exported from ``models/__init__.py``
   never reaches ``SQLModel.metadata``, so it is invisible to BOTH ``create_all``
   and autogenerate, with no error anywhere.
2. A revision with the wrong ``down_revision`` forks the chain, and alembic says
   nothing until an ``upgrade head`` on a real deployment.
3. ``Database.create_all`` is NOT the production path (the lifespan calls
   ``run_migrations``), while nearly every repository test DOES call it. So a
   model added without a migration passes the whole unit suite and then has no
   table in a real deployment.
4. A table with no ``_prune_`` branch in ``retention_service`` grows forever, and
   the method existing is not enough: the call site inside ``_prune_project`` is
   the half that actually runs.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import create_engine, text
from sqlmodel import SQLModel

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database
from voicegateway.models.load_run_model import (  # noqa: F401 - registers tables
    LoadRun,
    LoadRunTest,
)
from voicegateway.services import retention_service

TABLES = ("load_runs", "load_run_tests")


async def _migrated_engine(tmp_path: Path) -> tuple[Database, sa.Engine]:
    db = Database(GatewayConfig(cost_tracking={"db_path": str(tmp_path / "runs.db")}))
    await db.run_migrations()
    return db, create_engine(f"sqlite:///{db.db_file_path}")


def _columns(engine: sa.Engine, table: str) -> dict[str, tuple[str, int]]:
    """``{column: (declared_type, notnull)}`` as SQLite reports it."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).all()
    return {row[1]: (str(row[2]).upper(), int(row[3])) for row in rows}


def test_both_models_are_registered_with_the_metadata() -> None:
    """Silent failure 1. A model nobody re-exported is invisible, not an error."""
    import voicegateway.models  # noqa: F401 - the package-level re-export

    for table in TABLES:
        assert table in SQLModel.metadata.tables, (
            f"{table} is not in SQLModel.metadata: it was not re-exported from "
            "models/__init__.py, so create_all and autogenerate both cannot see it"
        )


async def test_the_migration_creates_both_tables(tmp_path: Path) -> None:
    db, engine = await _migrated_engine(tmp_path)
    try:
        with engine.connect() as conn:
            present = {
                row[0]
                for row in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'table'")
                )
            }
        assert set(TABLES).issubset(present)

        runs = _columns(engine, "load_runs")
        # Epoch milliseconds must be BIGINT: INT4 overflows on PostgreSQL.
        for column in ("started_at_ms", "ended_at_ms", "created_at_ms"):
            assert runs[column][0] == "BIGINT", (
                f"load_runs.{column} is {runs[column][0]}"
            )
        # created_at_ms is the age every row is pruned on, so it cannot be NULL.
        assert runs["created_at_ms"][1] == 1

        tests = _columns(engine, "load_run_tests")
        # A 500-concurrent hour passes 90 million packets, so INT4 is not enough.
        for column in ("rtp_packets_sent", "rtp_packets_received"):
            assert tests[column][0] == "BIGINT", f"{column} is {tests[column][0]}"
    finally:
        engine.dispose()
        await db.dispose()


async def test_every_measured_column_is_nullable(tmp_path: Path) -> None:
    """NULL means "not measured", so nothing measured may be NOT NULL.

    A 0 in peak_concurrency describes a test that carried no calls, which is a
    different claim from "the artifact did not say". If a measured column were
    NOT NULL, every import would have to invent a value for what it did not read.
    """
    db, engine = await _migrated_engine(tmp_path)
    try:
        tests = _columns(engine, "load_run_tests")
        measured = [
            c
            for c in tests
            if c not in ("id", "run_id", "name", "sequence", "created_at_ms")
        ]
        assert measured, "no measured columns found; the parser is wrong"
        not_null = [c for c in measured if tests[c][1] == 1]
        assert not not_null, f"measured columns declared NOT NULL: {not_null}"
    finally:
        engine.dispose()
        await db.dispose()


async def test_migration_matches_the_model(tmp_path: Path) -> None:
    """Silent failure 3. The handwritten migration must equal ``create_all``.

    Nothing here runs autogenerate, and the production path is ``run_migrations``
    while the tests use ``create_all``, so the two can disagree in a way only a
    real deployment notices.
    """
    db, migrated = await _migrated_engine(tmp_path)
    declared = create_engine(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        SQLModel.metadata.create_all(
            declared, tables=[SQLModel.metadata.tables[t] for t in TABLES]
        )
        for table in TABLES:
            assert _columns(migrated, table) == _columns(declared, table), table
    finally:
        declared.dispose()
        migrated.dispose()
        await db.dispose()


async def test_reimporting_the_same_test_updates_rather_than_duplicates(
    tmp_path: Path,
) -> None:
    """The unique constraint on (run_id, name) is what makes an import idempotent."""
    db, engine = await _migrated_engine(tmp_path)
    try:
        with engine.connect() as conn:
            # SQLite names a UNIQUE constraint's index sqlite_autoindex_*, not
            # the constraint name, so this checks the COLUMNS a unique index
            # covers rather than what it is called.
            unique_sets = []
            for row in conn.execute(text("PRAGMA index_list(load_run_tests)")):
                name, is_unique = row[1], int(row[2])
                if not is_unique:
                    continue
                cols = [
                    r[2] for r in conn.execute(text(f'PRAGMA index_info("{name}")'))
                ]
                unique_sets.append(set(cols))
        assert {"run_id", "name"} in unique_sets, (
            "the migration carries no unique constraint on (run_id, name), so a "
            f"re-import would duplicate rows. unique indexes cover: {unique_sets}"
        )
    finally:
        engine.dispose()
        await db.dispose()


def test_retention_has_both_the_prune_method_and_its_call_site() -> None:
    """Silent failure 4. The method alone never runs.

    ``agent_probe_results`` is the counter-example in this repo: it stops at the
    repository with no passthrough and no prune branch, so it grows forever and
    nothing says so.
    """
    worker = retention_service.RetentionWorker
    assert hasattr(worker, "_prune_load_runs"), "no _prune_load_runs method"

    source = inspect.getsource(worker._prune_project)
    assert "self._prune_load_runs(" in source, (
        "_prune_load_runs exists but _prune_project never calls it, so the table "
        "grows forever"
    )


def test_the_prune_deletes_children_before_the_parent() -> None:
    """A crash between the two would orphan rows nothing ever reads back."""
    source = inspect.getsource(retention_service.RetentionWorker._prune_load_runs)
    child = source.index("DELETE FROM load_run_tests")
    parent = source.index("DELETE FROM load_runs ")
    assert child < parent, "the parent is deleted before its children"
    # One commit per chunk, after both deletes, so they land together.
    assert source.index("await db.commit()") > parent


def test_the_storage_service_exposes_the_repository() -> None:
    """Without a passthrough the repository is unreachable from the app.

    This is the step ``agent_probe_results`` skipped.
    """
    from voicegateway.services.storage_service import StorageService

    for name in (
        "upsert_load_run",
        "upsert_load_run_test",
        "get_load_run",
        "list_load_runs",
        "list_load_run_tests",
    ):
        assert hasattr(StorageService, name), f"StorageService has no {name}"
