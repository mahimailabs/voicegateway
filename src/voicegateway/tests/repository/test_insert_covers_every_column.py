"""A table's columns and its INSERT are two things that must agree.

`revision` was added to `RequestRecord`, added to the migration, and accepted by
both. `_INSERT_REQUEST` names its columns explicitly and nobody added it there,
so every row wrote NULL. The field existed in the model, existed in the schema,
was documented in the changelog, and was never once stored.

Every test asserting on the in-memory record passed the whole time. It surfaced
only when a later test read the value back out of storage, which is the same
shape as a sink test that checks one end of a wire.

THIS IS THE DIFFERENTIAL FORM OF THAT LESSON. Two producers of one truth (the
schema and the INSERT) drift silently, so something has to compare them
mechanically rather than rely on a reviewer noticing a missing line in a
twenty-three column statement.

Columns filled by the database or by a later UPDATE are listed per table with a
reason. That list is the only place a column may be excused, so excusing one is
a visible edit rather than a silent omission.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import text

from voicegateway.services.storage_service import StorageService

_REPO_DIR = Path(__file__).resolve().parents[2] / "repository"

#: Columns an INSERT is not expected to write, and why. Anything not listed
#: here must appear in its table's INSERT statement.
_WRITTEN_ELSEWHERE: dict[str, dict[str, str]] = {
    "api_keys": {
        "last_used_at": "stamped by an UPDATE when the key is used",
        "revoked_at": "stamped by an UPDATE on revocation",
    },
    "sessions": {
        "call_id": "resolved from room_name after the call row exists",
        "talk_time_seconds": "computed by finalize_session_metrics",
        "per_minute_cost_usd": "computed by finalize_session_metrics",
        "response_speed_p50_ms": "computed by finalize_session_metrics",
        "response_speed_p95_ms": "computed by finalize_session_metrics",
        "talk_over_rate": "computed by finalize_session_metrics",
        "budget_ms_used": "accumulated across the session, not known at insert",
        "replay_size_bytes": "computed when replay artifacts are written",
    },
}

#: Filled by the database itself on every table that has them.
_DB_ASSIGNED = frozenset({"id", "created_at", "updated_at"})

#: The telemetry tables. A dropped column here loses measurement silently,
#: which is what happened, so they are named rather than discovered.
_TELEMETRY_TABLES = ("requests", "turns", "tool_calls", "dead_air_events")


def _insert_columns(table: str) -> set[str] | None:
    """The column list of the INSERT that writes ``table``, if there is one."""
    for path in sorted(_REPO_DIR.glob("*.py")):
        match = re.search(
            rf"INSERT INTO {re.escape(table)}\s*\(([^)]*)\)",
            path.read_text(),
            re.S,
        )
        if match:
            raw = match.group(1).replace('"', "").replace("'", "")
            return {
                c.strip() for c in re.split(r"[,\s]+", raw) if c.strip().isidentifier()
            }
    return None


async def _table_columns(tmp_path: Path, table: str) -> set[str]:
    storage = StorageService(str(tmp_path / "schema.db"))
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        result = await db.execute(
            text("SELECT name FROM pragma_table_info(:t)"), {"t": table}
        )
        cols = {row[0] for row in result}
    await storage.aclose()
    return cols


async def test_every_telemetry_insert_writes_every_column(tmp_path: Path) -> None:
    """The one that would have caught the `revision` drop.

    A column present in the schema and absent from the INSERT writes NULL on
    every row, forever, with nothing raising.
    """
    for table in _TELEMETRY_TABLES:
        columns = await _table_columns(tmp_path, table)
        assert columns, f"{table} does not exist"
        insert = _insert_columns(table)
        assert insert is not None, f"no INSERT INTO {table} found in the repository"
        excused = set(_WRITTEN_ELSEWHERE.get(table, {}))
        missing = columns - insert - _DB_ASSIGNED - excused
        assert not missing, (
            f"{table}: these columns exist in the schema and are never written "
            f"by its INSERT, so they are NULL on every row: {sorted(missing)}. "
            f"Add them to the INSERT, or list them in _WRITTEN_ELSEWHERE with "
            f"the reason they are filled elsewhere."
        )


async def test_the_excuse_list_names_only_real_columns(tmp_path: Path) -> None:
    """An excuse for a column that no longer exists is a stale excuse.

    Without this, deleting a column would leave its entry behind, and the next
    column added with that name would be silently pre-excused.
    """
    for table, excuses in _WRITTEN_ELSEWHERE.items():
        columns = await _table_columns(tmp_path, table)
        if not columns:
            continue
        unknown = set(excuses) - columns
        assert not unknown, (
            f"{table}: excuses for columns that do not exist: {sorted(unknown)}"
        )


async def test_no_telemetry_column_is_excused(tmp_path: Path) -> None:
    """The telemetry tables get no exemptions at all.

    Every column on a measurement row is written at insert. If one ever needs
    an exemption that is a design change worth arguing for explicitly, not a
    line quietly added to a dictionary.
    """
    for table in _TELEMETRY_TABLES:
        assert table not in _WRITTEN_ELSEWHERE, (
            f"{table} is a telemetry table and should write every column at "
            f"insert; an exemption here hides a measurement that is never stored"
        )
