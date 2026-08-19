"""What makes ``talk_over_rate``'s zero trustworthy, pinned rather than assumed.

Issue #242 reported the harm: a session reporting "no talk-over" when it had in
fact measured nothing. `count_overlap_turns` compares `agent_speak_end_ms`
against `caller_speak_start_ms` in SQL, and a NULL start makes that comparison
NULL, so the row would fail to match and land in the count as "did not overlap".
A zero built that way is a claim the data cannot support, told to somebody who
opened that view BECAUSE they suspect a barge-in problem.

That cannot happen, and this file pins the two reasons:

* `turns.caller_speak_start_ms` is NOT NULL, so a turn row without a caller
  start cannot be written. The NULL-comparison path is unreachable.
* A session with no turns at all reports None, not 0.0. That is the only way
  "nothing was measured" can actually arise, and it already reads as unmeasured.

Relaxing that constraint without handling the unmeasured case would reintroduce
exactly the reported defect, silently, which is why the constraint is asserted
here instead of trusted.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from voicegateway.repository import session_repository as sessions
from voicegateway.services.storage_service import StorageService


async def test_a_turn_cannot_be_written_without_a_caller_start(
    tmp_path: Path,
) -> None:
    """The constraint that makes the SQL comparison's NULL branch unreachable."""
    storage = StorageService(str(tmp_path / "schema.db"))
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        result = await db.execute(
            text("SELECT \"notnull\" FROM pragma_table_info('turns') WHERE name = :n"),
            {"n": "caller_speak_start_ms"},
        )
        row = result.fetchone()
    await storage.aclose()
    assert row is not None, "turns.caller_speak_start_ms is missing entirely"
    assert bool(row[0]), (
        "caller_speak_start_ms became nullable. count_overlap_turns compares it "
        "in SQL, so a NULL now counts as 'did not overlap' and talk_over_rate "
        "reports a confident 0.0 for a session that measured nothing. Handle the "
        "unmeasured case before relaxing this."
    )


async def test_a_session_with_no_turns_reports_unmeasured_not_zero(
    tmp_path: Path,
) -> None:
    """The only way "nothing measured" can arise, and it already reads as such."""
    storage = StorageService(str(tmp_path / "empty.db"))
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        await db.execute(
            text(
                "INSERT INTO sessions (id, project, started_at) "
                "VALUES ('s', 'default', 0)"
            )
        )
        await db.commit()
    async with storage._conn.session() as db:
        await sessions.finalize_session_metrics(db, "s")
    async with storage._conn.session() as db:
        result = await db.execute(
            text("SELECT talk_over_rate FROM sessions WHERE id = 's'")
        )
        row = result.fetchone()
    await storage.aclose()
    assert row is not None
    assert row[0] is None, f"expected unmeasured, got {row[0]!r}"
