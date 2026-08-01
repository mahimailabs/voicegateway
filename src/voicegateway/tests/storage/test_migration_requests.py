"""Alembic migrations for ``requests``: the DDL must match the SQLModel.

Runs ``alembic upgrade head`` against a throwaway SQLite file (never a configured
database) and checks the DDL the migrations actually produce, including that it
has not drifted from the SQLModel definition -- nothing in this repo runs
autogenerate, so the migration and the model can disagree silently. ``requests``
shipped with exactly that disagreement: migration c7d2a9f1e6b4 added
``rate_rule`` as ``sa.Text(), nullable=True`` while the model declared a plain
``str``, which maps to VARCHAR NOT NULL. SQLite hides the VARCHAR/Text half and
PostgreSQL does not; the NOT NULL half is enforced everywhere, so an upgraded
install and a fresh ``create_all`` install disagreed on what a valid row is.

The table is assembled by four migrations (f1ae43d7fa98 creates it,
a7c2e91f8d34 adds ``cached_input_units``, c7d2a9f1e6b4 adds the two billing
columns, b2e7c4a9f1d3 adds ``agent_id``), so the comparison below is against the
whole upgrade chain rather than any single revision.

Out of scope: the ClickHouse schema is a third declaration of this table
(``clickhouse/migrations/0004_requests_rated_price.sql`` makes ``rate_rule`` a
non-nullable ``LowCardinality(String)``). It is a separate store with its own
migration runner and is not what these tests unify.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import create_engine, text
from sqlmodel import SQLModel

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database
from voicegateway.models.request_model import Request  # noqa: F401 - registers table


async def _migrated_engine(tmp_path: Path) -> tuple[Database, sa.Engine]:
    db = Database(GatewayConfig(cost_tracking={"db_path": str(tmp_path / "req.db")}))
    await db.run_migrations()
    return db, create_engine(f"sqlite:///{db.db_file_path}")


def _columns(engine: sa.Engine, table: str) -> dict[str, tuple[str, int]]:
    """``{column: (declared_type, notnull)}`` as SQLite reports it."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).all()
    return {row[1]: (str(row[2]).upper(), int(row[3])) for row in rows}


async def test_migration_creates_requests(tmp_path: Path) -> None:
    db, engine = await _migrated_engine(tmp_path)
    try:
        with engine.connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'table'")
                )
            }
        assert "requests" in tables

        cols = _columns(engine, "requests")
        # The audit token for the rule that produced the rated price. Nullable
        # and TEXT, because that is what c7d2a9f1e6b4 deployed: every install
        # that upgraded has a nullable column, so declaring NOT NULL would only
        # mean fresh installs reject rows the older ones accept.
        assert cols["rate_rule"] == ("TEXT", 0), (
            f"requests.rate_rule is {cols['rate_rule']}; migration c7d2a9f1e6b4 "
            "declares sa.Text(), nullable=True and the model must agree"
        )
        # Its companion column was already nullable on both sides; pinned here
        # so the pair cannot drift apart later.
        assert cols["rated_price_usd"] == ("FLOAT", 0)
        # The identity of a row, and the clock the retention pass prunes on.
        # Neither can be absent.
        assert cols["id"][1] == 1
        assert cols["timestamp"][1] == 1
        # A request may have no session at all (direct gateway use, no attach),
        # and the retention pass nulls this pointer when the session it names is
        # pruned, so it has to stay nullable.
        assert cols["session_id"][1] == 0
    finally:
        engine.dispose()
        await db.dispose()


async def test_migration_matches_the_model(tmp_path: Path) -> None:
    """The handwritten migrations must produce what ``create_all`` would.

    Nothing here runs alembic autogenerate, so a column added to the model and
    forgotten in the migration (or vice versa) is invisible until a fresh install
    diverges from an upgraded one. This is the assertion that catches the
    VARCHAR-NOT-NULL-vs-Text-nullable drift on ``rate_rule``, because it compares
    the DDL both sides actually emit rather than trusting either declaration.
    """
    db, migrated = await _migrated_engine(tmp_path)
    declared = create_engine(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        SQLModel.metadata.create_all(
            declared, tables=[SQLModel.metadata.tables["requests"]]
        )
        assert _columns(migrated, "requests") == _columns(declared, "requests")
    finally:
        declared.dispose()
        migrated.dispose()
        await db.dispose()
