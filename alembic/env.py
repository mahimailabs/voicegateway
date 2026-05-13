"""Alembic environment.

Loads voicegw.yaml to compute the database URL, then registers every
SQLModel-mapped entity so ``--autogenerate`` can diff the runtime schema
against the declared metadata.
"""

from __future__ import annotations

import asyncio
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

if alembic_config.config_file_name is not None:
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


def run_migrations_offline() -> None:
    """Emit SQL to stdout without opening a DB connection."""
    context.configure(
        url=alembic_config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
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
