"""A database stamped by a newer build must degrade, not fail every write.

Reproduces the shape of the real incident: a dev checkout migrates the shared
SQLite file forward, then a released wheel opens the same file and finds a
revision it ships no script for. Alembic raises, and because the failure was
never remembered, every subsequent request-log write re-ran the whole upgrade
and logged its traceback.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database, DatabaseAheadOfCode

# Deliberately synthetic. The revision from the original incident
# (a1c6e39b7f24) is this repo's head, so only a made-up id stays unknown to
# every build and keeps this test from rotting the next time head moves.
UNKNOWN_REVISION = "0000deadbeef"


def _build_config(db_path: Path) -> GatewayConfig:
    return GatewayConfig(cost_tracking={"db_path": str(db_path)})


async def _migrated_db(db_path: Path) -> None:
    """Bring a fresh file to head, then close it."""
    db = Database(_build_config(db_path))
    try:
        await db.run_migrations()
    finally:
        await db.dispose()


def _stamp(db_path: Path, revision: str) -> None:
    """Rewrite the version row to a revision no shipped script defines."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE alembic_version_voicegateway SET version_num = ?", (revision,)
        )
        conn.commit()


@pytest.mark.asyncio
async def test_unknown_stamped_revision_raises_typed_error(tmp_path: Path) -> None:
    """The generic alembic CommandError is translated into something catchable."""
    db_path = tmp_path / "voicegw.db"
    await _migrated_db(db_path)
    _stamp(db_path, UNKNOWN_REVISION)

    db = Database(_build_config(db_path))
    try:
        with pytest.raises(DatabaseAheadOfCode) as excinfo:
            await db.run_migrations()
    finally:
        await db.dispose()

    # The message has to name the revision, or an operator cannot tell which
    # build wrote the file.
    assert UNKNOWN_REVISION in str(excinfo.value)
    assert excinfo.value.revision == UNKNOWN_REVISION


@pytest.mark.asyncio
async def test_failure_is_remembered_not_retried(tmp_path: Path) -> None:
    """The second call must not re-run alembic. This is the log-flood fix."""
    db_path = tmp_path / "voicegw.db"
    await _migrated_db(db_path)
    _stamp(db_path, UNKNOWN_REVISION)

    db = Database(_build_config(db_path))
    calls = 0
    original = db._run_alembic_upgrade

    def counting_upgrade() -> None:
        nonlocal calls
        calls += 1
        original()

    db._run_alembic_upgrade = counting_upgrade  # type: ignore[method-assign]

    try:
        for _ in range(5):
            with pytest.raises(DatabaseAheadOfCode):
                await db.run_migrations()
    finally:
        await db.dispose()

    assert calls == 1, f"alembic re-ran {calls} times; the failure was not cached"


@pytest.mark.asyncio
async def test_concurrent_migrations_do_not_race(tmp_path: Path) -> None:
    """Two upgrades must never be inside alembic at the same time.

    ``EnvironmentContext`` installs ``config``, ``script`` and friends as module
    globals and deletes them again on exit, so overlapping upgrades in one
    process tear down each other's state and the loser raises ``KeyError:
    'config'``. Different Database objects on different files are still the same
    process, so isolation per instance buys nothing here.
    """

    async def migrate(index: int) -> None:
        db = Database(_build_config(tmp_path / f"db{index}.db"))
        try:
            await db.run_migrations()
        finally:
            await db.dispose()

    await asyncio.gather(*(migrate(i) for i in range(8)))

    for index in range(8):
        with sqlite3.connect(str(tmp_path / f"db{index}.db")) as conn:
            row = conn.execute(
                "SELECT version_num FROM alembic_version_voicegateway"
            ).fetchone()
        assert row is not None, f"db{index} was never stamped"


@pytest.mark.asyncio
async def test_migrations_survive_default_executor_shutdown(tmp_path: Path) -> None:
    """A write landing during teardown must not hit the loop's dead executor.

    ``asyncio.to_thread`` borrows the event loop's default executor. Once the
    loop has shut that down, every call raises ``RuntimeError: Executor
    shutdown has been called``, which is what the final sink write hit on the
    way out of a LiveKit job.
    """
    db_path = tmp_path / "voicegw.db"

    await asyncio.get_running_loop().shutdown_default_executor()

    db = Database(_build_config(db_path))
    try:
        await db.run_migrations()
    finally:
        await db.dispose()

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT version_num FROM alembic_version_voicegateway"
        ).fetchone()
    assert row is not None
