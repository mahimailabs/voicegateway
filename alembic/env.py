"""Alembic environment.

Loads voicegw.yaml to compute the database URL, then registers every
SQLModel-mapped entity so ``--autogenerate`` can diff the runtime schema
against the declared metadata.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlmodel import SQLModel

import voicegateway.models  # noqa: F401, E402 — registers every model
from alembic import context
from voicegateway.core.config import GatewayConfig  # noqa: E402
from voicegateway.core.database import resolve_database_url  # noqa: E402

alembic_config = context.config

# fileConfig() reconfigures the root logger from alembic.ini. That's
# fine for the CLI, but Database.run_migrations() sets this env var so
# the programmatic path doesn't clobber the caller's logging setup
# (which breaks pytest caplog and any host that already wired logging).
if (
    alembic_config.config_file_name is not None
    and os.environ.get("VOICEGW_ALEMBIC_SKIP_LOGGING") != "1"
):
    fileConfig(alembic_config.config_file_name)


def _resolve_url() -> str:
    try:
        cfg = GatewayConfig.load()
    except Exception:
        # Fall back to the URL in alembic.ini when no voicegw.yaml is on disk
        # (CI bootstrap, fresh clones, etc.).
        return alembic_config.get_main_option("sqlalchemy.url") or ""
    return resolve_database_url(cfg)


alembic_config.set_main_option("sqlalchemy.url", _resolve_url())

target_metadata = SQLModel.metadata

# Dedicated version table so a future cloud migration chain can share the same
# database without colliding on the default "alembic_version".
VERSION_TABLE = "alembic_version_voicegateway"


def _maybe_rename_legacy_version_table(connection: Connection) -> None:
    """Rename alembic_version -> alembic_version_voicegateway for existing installs.

    Called at the top of do_run_migrations, before context.configure, so that
    Alembic always sees the new namespaced table and never tries to rebuild
    from base when it finds the old generic one.

    IMPORTANT: the rename query is executed inside an explicit ``begin()``
    block that commits before returning.  Any SQL execution on a SQLAlchemy 2
    connection triggers autobegin; if we left the connection in that
    auto-begun transaction, Alembic would detect ``_in_external_transaction``
    and treat ``context.begin_transaction()`` as a no-op, causing the version
    stamp INSERT to be issued but never committed.
    """
    with connection.begin():
        rows = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
            if connection.dialect.name == "sqlite"
            else "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = 'alembic_version'"
        ).fetchall()
        old_exists = len(rows) > 0

        new_rows = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?"
            if connection.dialect.name == "sqlite"
            else "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = %s",
            (VERSION_TABLE,),
        ).fetchall()
        new_exists = len(new_rows) > 0

        if old_exists and not new_exists:
            connection.exec_driver_sql(
                f"ALTER TABLE alembic_version RENAME TO {VERSION_TABLE}"
            )
    # After the begin() block commits, connection.in_transaction() is False,
    # so Alembic will not see an external transaction and will manage its own
    # commit for the version stamp.


def run_migrations_offline() -> None:
    """Emit SQL to stdout without opening a DB connection."""
    context.configure(
        url=alembic_config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        version_table=VERSION_TABLE,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _maybe_rename_legacy_version_table(connection)
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        version_table=VERSION_TABLE,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        alembic_config.get_section(alembic_config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
