"""Async repo for the ``transcript_turns`` table (per-call transcripts)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import text

from voicegateway.inference.session.context import current_tenant

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class TranscriptTurnRow:
    """One transcript turn as returned to the API/UI."""

    session_id: str
    seq: int
    role: str
    text: str
    created_at: str | None = None


_INSERT = text(
    "INSERT INTO transcript_turns (session_id, seq, role, text, tenant_id) "
    "VALUES (:session_id, :seq, :role, :text, :tenant_id)"
)


async def create_transcript_bulk(
    session: AsyncSession,
    session_id: str,
    turns: list[tuple[str, str]],
    *,
    tenant_id: str | None = None,
) -> int:
    """Replace a session's transcript with ``turns`` (a list of (role, text)).

    Deletes any existing rows for the session first, so a re-capture (e.g. a
    close handler that runs twice) is idempotent rather than doubling the
    transcript. ``seq`` is assigned from list order. Returns the count inserted.
    """
    resolved = tenant_id if tenant_id is not None else current_tenant()
    await session.execute(
        text("DELETE FROM transcript_turns WHERE session_id = :session_id"),
        {"session_id": session_id},
    )
    if not turns:
        await session.commit()
        return 0
    params = [
        {
            "session_id": session_id,
            "seq": i,
            "role": role,
            "text": body,
            "tenant_id": resolved,
        }
        for i, (role, body) in enumerate(turns)
    ]
    await session.execute(_INSERT, params)
    await session.commit()
    return len(params)


async def list_transcript_by_session(
    session: AsyncSession, session_id: str
) -> list[TranscriptTurnRow]:
    """Return a session's transcript turns, ordered by ``seq`` ASC."""
    result = await session.execute(
        text(
            "SELECT session_id, seq, role, text, created_at "
            "FROM transcript_turns WHERE session_id = :session_id "
            "ORDER BY seq ASC"
        ),
        {"session_id": session_id},
    )
    return [
        TranscriptTurnRow(
            session_id=row[0], seq=row[1], role=row[2], text=row[3], created_at=row[4]
        )
        for row in result
    ]


__all__ = [
    "TranscriptTurnRow",
    "create_transcript_bulk",
    "list_transcript_by_session",
]
