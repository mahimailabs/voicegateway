"""ClickHouse DDL migration runner.

Supports two execution contexts:

1. Production (async, clickhouse-connect):
   ``await apply_migrations(client, migrations_dir)``
   - `client` is a ``clickhouse_connect.driver.AsyncClient``

2. Tests (sync, chDB Session):
   ``apply_migrations_to_session(sess, migrations_dir)``
   - `sess` is a ``chdb.session.Session``

Tracking table
--------------
``telemetry.schema_migrations (version UInt32, name String, applied_at DateTime)``
lives in ClickHouse itself.  Each ``.sql`` file is ONE logical change; the file
is split on ``;`` and every non-empty statement is executed in order.  ClickHouse
has no multi-statement transactions, so ``IF NOT EXISTS`` guards in every DDL
statement provide idempotency.
"""

from __future__ import annotations

import logging
import pathlib
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

_MIGRATIONS_DIR = pathlib.Path(__file__).parent / "migrations"

_CREATE_TRACKING_DB = "CREATE DATABASE IF NOT EXISTS telemetry"

_CREATE_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS telemetry.schema_migrations (
    version     UInt32,
    name        String,
    applied_at  DateTime DEFAULT now()
)
ENGINE = MergeTree
ORDER BY version
"""

_INSERT_VERSION = "INSERT INTO telemetry.schema_migrations (version, name) VALUES ({version}, '{name}')"

_SELECT_VERSIONS = "SELECT version FROM telemetry.schema_migrations ORDER BY version"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _migration_files(
    migrations_dir: pathlib.Path,
) -> list[tuple[int, str, pathlib.Path]]:
    """Return sorted list of (version, name, path) for all .sql files."""
    files = sorted(migrations_dir.glob("*.sql"))
    result = []
    for f in files:
        m = re.match(r"^(\d+)_(.+)\.sql$", f.name)
        if not m:
            log.warning("Skipping migration file with unexpected name: %s", f.name)
            continue
        version = int(m.group(1))
        name = m.group(2)
        result.append((version, name, f))
    return result


def _split_statements(sql: str) -> list[str]:
    """Split SQL text on ';' and return non-empty statements."""
    stmts = []
    for raw in sql.split(";"):
        stmt = raw.strip()
        # Strip inline comments and blank-line-only results
        stmt = "\n".join(
            line for line in stmt.splitlines() if not line.strip().startswith("--")
        ).strip()
        if stmt:
            stmts.append(stmt)
    return stmts


# ---------------------------------------------------------------------------
# chDB Session runner (sync, for unit tests)
# ---------------------------------------------------------------------------


def apply_migrations_to_session(
    sess,
    migrations_dir: pathlib.Path | None = None,
) -> None:
    """Apply all pending migrations to a chDB persistent Session.

    Args:
        sess: ``chdb.session.Session`` instance.
        migrations_dir: Directory containing ``*.sql`` files.
                        Defaults to the bundled ``migrations/`` folder.
    """
    if migrations_dir is None:
        migrations_dir = _MIGRATIONS_DIR

    def run(sql: str) -> str:
        result = sess.query(sql, "CSV")
        # chdb v4+ returns a query_result object with a .bytes() method
        raw = result.bytes() if hasattr(result, "bytes") else bytes(result)
        return raw.decode().strip()

    # Bootstrap tracking infrastructure
    run(_CREATE_TRACKING_DB)
    for stmt in _split_statements(_CREATE_TRACKING_TABLE):
        run(stmt)

    # Determine already-applied versions
    applied_raw = run(_SELECT_VERSIONS)
    applied: set[int] = set()
    for line in applied_raw.splitlines():
        line = line.strip()
        if line:
            try:
                applied.add(int(line))
            except ValueError:
                pass

    # Apply pending migrations
    for version, name, path in _migration_files(migrations_dir):
        if version in applied:
            log.debug("Migration %04d (%s) already applied, skipping.", version, name)
            continue

        log.info("Applying migration %04d: %s", version, name)
        sql = path.read_text()
        for stmt in _split_statements(sql):
            run(stmt)

        run(_INSERT_VERSION.format(version=version, name=name))
        log.info("Migration %04d applied successfully.", version)


# ---------------------------------------------------------------------------
# clickhouse-connect async runner (production)
# ---------------------------------------------------------------------------


async def apply_migrations(
    client,
    migrations_dir: pathlib.Path | None = None,
) -> None:
    """Apply all pending migrations via an async clickhouse-connect client.

    Args:
        client: ``clickhouse_connect.driver.AsyncClient`` (or compatible).
        migrations_dir: Directory containing ``*.sql`` files.
                        Defaults to the bundled ``migrations/`` folder.
    """
    if migrations_dir is None:
        migrations_dir = _MIGRATIONS_DIR

    async def run(sql: str):
        return await client.command(sql)

    # Bootstrap tracking infrastructure
    await run(_CREATE_TRACKING_DB)
    for stmt in _split_statements(_CREATE_TRACKING_TABLE):
        await run(stmt)

    # Determine already-applied versions
    result = await client.query(_SELECT_VERSIONS)
    applied: set[int] = {int(row[0]) for row in result.result_rows}

    # Apply pending migrations
    for version, name, path in _migration_files(migrations_dir):
        if version in applied:
            log.debug("Migration %04d (%s) already applied, skipping.", version, name)
            continue

        log.info("Applying migration %04d: %s", version, name)
        sql = path.read_text()
        for stmt in _split_statements(sql):
            await run(stmt)

        await run(_INSERT_VERSION.format(version=version, name=name))
        log.info("Migration %04d applied successfully.", version)
