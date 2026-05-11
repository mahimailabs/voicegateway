"""Async repo for the four ``replay_*`` tables.

Implements REQ-VG-REPLAY-001 (open any past conversation as a replay)
and REQ-VG-REPLAY-006 (privacy + retention) data access. Mirrors the
flat-function-module pattern from v0.2.0's ``turns_repo`` and
``dead_air_repo``: each function takes an ``aiosqlite.Connection`` and
the caller owns the connection lifecycle.

The natural caller is ``ReplayCapture.flush_callback`` from T02 with a
``functools.partial(replay_repo.bulk_write_events, db)`` wiring landed
in T09's session-close path.

Schema reference: ``voicegateway/storage/migrations/0004_replay_tables.py``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from voicegateway.middleware.replay_capture import ReplayEvent

if TYPE_CHECKING:
    import aiosqlite


# Modality -> (table_name, has_provider_cost) tuples. The four replay
# tables share identical column shapes but ``state_snapshots`` semantically
# does not carry provider/cost; both default to '' / NULL when the
# inserter omits them, matching migration 0004.
_TABLE_BY_MODALITY: dict[str, str] = {
    "stt": "replay_stt_events",
    "llm": "replay_llm_tokens",
    "tts": "replay_tts_frames",
    "state": "replay_state_snapshots",
}


_INSERT_BY_MODALITY: dict[str, str] = {
    "stt": (
        "INSERT INTO replay_stt_events "
        "(session_id, t_ms, payload, provider, cost_usd) "
        "VALUES (?, ?, ?, ?, ?)"
    ),
    "llm": (
        "INSERT INTO replay_llm_tokens "
        "(session_id, t_ms, payload, provider, cost_usd) "
        "VALUES (?, ?, ?, ?, ?)"
    ),
    "tts": (
        "INSERT INTO replay_tts_frames "
        "(session_id, t_ms, payload, provider, cost_usd) "
        "VALUES (?, ?, ?, ?, ?)"
    ),
    "state": (
        "INSERT INTO replay_state_snapshots "
        "(session_id, t_ms, payload) "
        "VALUES (?, ?, ?)"
    ),
}


def _payload_to_text(payload: dict[str, Any]) -> str:
    """Serialize a payload dict to the TEXT JSON the schema stores."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


async def bulk_write_events(db: aiosqlite.Connection, events: list[ReplayEvent]) -> int:
    """Bulk-insert events into their per-modality tables.

    Partitions by ``event.modality`` and runs one ``executemany`` per
    partition. Empty input is a no-op (returns 0). Commits the
    connection on success.

    Returns the total number of events inserted across the four tables.
    """
    if not events:
        return 0

    by_modality: dict[str, list[tuple[Any, ...]]] = {
        "stt": [],
        "llm": [],
        "tts": [],
        "state": [],
    }
    for ev in events:
        if ev.modality not in by_modality:
            raise ValueError(
                f"unknown modality {ev.modality!r}; expected one of "
                f"{sorted(by_modality)}"
            )
        if ev.modality == "state":
            by_modality["state"].append(
                (ev.session_id, ev.t_ms, _payload_to_text(ev.payload))
            )
        else:
            by_modality[ev.modality].append(
                (
                    ev.session_id,
                    ev.t_ms,
                    _payload_to_text(ev.payload),
                    ev.provider,
                    ev.cost_usd,
                )
            )

    inserted = 0
    for modality, rows in by_modality.items():
        if not rows:
            continue
        await db.executemany(_INSERT_BY_MODALITY[modality], rows)
        inserted += len(rows)
    await db.commit()
    return inserted


async def read_full_replay(
    db: aiosqlite.Connection, session_id: str
) -> list[ReplayEvent]:
    """Return the full replay for one session, ordered by ``t_ms`` ASC.

    UNION ALL across the four replay tables. Each row carries its
    modality so the consumer (the dashboard's Replay page, T11) can
    route to the right pane.
    """
    # Each subquery selects the seven canonical columns (session_id,
    # modality literal, t_ms, payload, provider, cost_usd, created_at)
    # so the outer ORDER BY t_ms is stable across modalities.
    sql = (
        "SELECT session_id, 'stt' AS modality, t_ms, payload, "
        "       provider, cost_usd FROM replay_stt_events "
        " WHERE session_id = ? "
        "UNION ALL "
        "SELECT session_id, 'llm' AS modality, t_ms, payload, "
        "       provider, cost_usd FROM replay_llm_tokens "
        " WHERE session_id = ? "
        "UNION ALL "
        "SELECT session_id, 'tts' AS modality, t_ms, payload, "
        "       provider, cost_usd FROM replay_tts_frames "
        " WHERE session_id = ? "
        "UNION ALL "
        "SELECT session_id, 'state' AS modality, t_ms, payload, "
        "       '' AS provider, NULL AS cost_usd "
        "  FROM replay_state_snapshots "
        " WHERE session_id = ? "
        "ORDER BY t_ms ASC"
    )
    cursor = await db.execute(sql, (session_id, session_id, session_id, session_id))
    out: list[ReplayEvent] = []
    async for row in cursor:
        sid, modality, t_ms, payload_text, provider, cost_usd = row
        try:
            payload: dict[str, Any] = json.loads(payload_text)
        except json.JSONDecodeError:
            payload = {"_decode_error": True, "raw": payload_text}
        out.append(
            ReplayEvent(
                session_id=sid,
                modality=modality,
                t_ms=int(t_ms),
                payload=payload,
                provider=provider or "",
                cost_usd=float(cost_usd) if cost_usd is not None else None,
            )
        )
    return out


async def delete_replay(db: aiosqlite.Connection, session_id: str) -> int:
    """Delete every replay row for one session across all four tables.

    Implements REQ-VG-REPLAY-006 AC-3 ("when the developer deletes a
    session from the dashboard, all replay events tied to that session
    are deleted in the same operation"). Single transaction across the
    four tables. Returns the total number of rows deleted.
    """
    total = 0
    for table in _TABLE_BY_MODALITY.values():
        cursor = await db.execute(
            f"DELETE FROM {table} WHERE session_id = ?", (session_id,)
        )
        # aiosqlite Cursor exposes rowcount after execute for DELETE.
        if cursor.rowcount is not None and cursor.rowcount > 0:
            total += cursor.rowcount
    await db.commit()
    return total


async def aggregate_storage_per_session(
    db: aiosqlite.Connection, session_id: str
) -> int:
    """Sum the JSON-payload byte length across all four tables.

    Approximates the on-disk footprint for one session's replay rows.
    The actual on-disk size includes row overhead, indexes, and JSON
    encoding artifacts, but the payload sum is the dominant term
    (90%+) and tracks the developer-facing storage trade-off.

    Implements REQ-VG-REPLAY-006's "the dashboard surfaces current
    storage usage so the cost is not invisible" requirement; the
    sessions row's ``replay_size_bytes`` column is upserted from this
    sum at session close (T08's ``finalize_session_replay``).
    """
    total = 0
    for table in _TABLE_BY_MODALITY.values():
        cursor = await db.execute(
            f"SELECT COALESCE(SUM(length(payload)), 0) FROM {table} "
            "WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        if row is not None and row[0] is not None:
            total += int(row[0])
    return total


__all__ = [
    "aggregate_storage_per_session",
    "bulk_write_events",
    "delete_replay",
    "read_full_replay",
]
