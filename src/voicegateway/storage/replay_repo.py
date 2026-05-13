"""Async repo for the four ``replay_*`` tables."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from voicegateway.inference._session_context import current_tenant
from voicegateway.middleware.replay_capture import ReplayEvent

if TYPE_CHECKING:
    import aiosqlite


_TABLE_BY_MODALITY: dict[str, str] = {
    "stt": "replay_stt_events",
    "llm": "replay_llm_tokens",
    "tts": "replay_tts_frames",
    "state": "replay_state_snapshots",
}


_INSERT_BY_MODALITY: dict[str, str] = {
    "stt": (
        "INSERT INTO replay_stt_events "
        "(session_id, t_ms, payload, provider, cost_usd, tenant_id) "
        "VALUES (?, ?, ?, ?, ?, ?)"
    ),
    "llm": (
        "INSERT INTO replay_llm_tokens "
        "(session_id, t_ms, payload, provider, cost_usd, tenant_id) "
        "VALUES (?, ?, ?, ?, ?, ?)"
    ),
    "tts": (
        "INSERT INTO replay_tts_frames "
        "(session_id, t_ms, payload, provider, cost_usd, tenant_id) "
        "VALUES (?, ?, ?, ?, ?, ?)"
    ),
    "state": (
        "INSERT INTO replay_state_snapshots "
        "(session_id, t_ms, payload, tenant_id) "
        "VALUES (?, ?, ?, ?)"
    ),
}


def _payload_to_text(payload: dict[str, Any]) -> str:
    """Serialize a payload dict to the TEXT JSON the schema stores."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


async def bulk_write_events(
    db: aiosqlite.Connection,
    events: list[ReplayEvent],
    *,
    tenant_id: str | None = None,
) -> int:
    """Bulk-insert events into their per-modality tables."""
    if not events:
        return 0
    resolved = tenant_id if tenant_id is not None else current_tenant()

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
                (ev.session_id, ev.t_ms, _payload_to_text(ev.payload), resolved)
            )
        else:
            by_modality[ev.modality].append(
                (
                    ev.session_id,
                    ev.t_ms,
                    _payload_to_text(ev.payload),
                    ev.provider,
                    ev.cost_usd,
                    resolved,
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
    """Delete every replay row for one session across all four tables."""
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
    """Sum the JSON-payload byte length across all four tables."""
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
