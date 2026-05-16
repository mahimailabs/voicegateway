"""Async repo for the four ``replay_*`` tables (ORM)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from voicegateway.inference.session.context import current_tenant
from voicegateway.middleware.replay_capture_middleware import ReplayEvent

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_TABLE_BY_MODALITY: dict[str, str] = {
    "stt": "replay_stt_events",
    "llm": "replay_llm_tokens",
    "tts": "replay_tts_frames",
    "state": "replay_state_snapshots",
}


_INSERT_BY_MODALITY: dict[str, Any] = {
    "stt": text(
        "INSERT INTO replay_stt_events "
        "(session_id, t_ms, payload, provider, cost_usd, tenant_id) "
        "VALUES (:session_id, :t_ms, :payload, :provider, :cost_usd, :tenant_id)"
    ),
    "llm": text(
        "INSERT INTO replay_llm_tokens "
        "(session_id, t_ms, payload, provider, cost_usd, tenant_id) "
        "VALUES (:session_id, :t_ms, :payload, :provider, :cost_usd, :tenant_id)"
    ),
    "tts": text(
        "INSERT INTO replay_tts_frames "
        "(session_id, t_ms, payload, provider, cost_usd, tenant_id) "
        "VALUES (:session_id, :t_ms, :payload, :provider, :cost_usd, :tenant_id)"
    ),
    "state": text(
        "INSERT INTO replay_state_snapshots "
        "(session_id, t_ms, payload, tenant_id) "
        "VALUES (:session_id, :t_ms, :payload, :tenant_id)"
    ),
}


def _payload_to_text(payload: dict[str, Any]) -> str:
    """Serialize a payload dict to the TEXT JSON the schema stores."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


async def bulk_write_events(
    session: AsyncSession,
    events: list[ReplayEvent],
    *,
    tenant_id: str | None = None,
) -> int:
    """Bulk-insert events into their per-modality tables."""
    if not events:
        return 0
    resolved = tenant_id if tenant_id is not None else current_tenant()

    by_modality: dict[str, list[dict[str, Any]]] = {
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
                {
                    "session_id": ev.session_id,
                    "t_ms": ev.t_ms,
                    "payload": _payload_to_text(ev.payload),
                    "tenant_id": resolved,
                }
            )
        else:
            by_modality[ev.modality].append(
                {
                    "session_id": ev.session_id,
                    "t_ms": ev.t_ms,
                    "payload": _payload_to_text(ev.payload),
                    "provider": ev.provider,
                    "cost_usd": ev.cost_usd,
                    "tenant_id": resolved,
                }
            )

    inserted = 0
    for modality, rows in by_modality.items():
        if not rows:
            continue
        await session.execute(_INSERT_BY_MODALITY[modality], rows)
        inserted += len(rows)
    await session.commit()
    return inserted


async def read_full_replay(session: AsyncSession, session_id: str) -> list[ReplayEvent]:
    """Return the full replay for one session, ordered by ``t_ms`` ASC."""
    result = await session.execute(
        text(
            "SELECT session_id, 'stt' AS modality, t_ms, payload, "
            "       provider, cost_usd FROM replay_stt_events "
            " WHERE session_id = :sid "
            "UNION ALL "
            "SELECT session_id, 'llm' AS modality, t_ms, payload, "
            "       provider, cost_usd FROM replay_llm_tokens "
            " WHERE session_id = :sid "
            "UNION ALL "
            "SELECT session_id, 'tts' AS modality, t_ms, payload, "
            "       provider, cost_usd FROM replay_tts_frames "
            " WHERE session_id = :sid "
            "UNION ALL "
            "SELECT session_id, 'state' AS modality, t_ms, payload, "
            "       '' AS provider, NULL AS cost_usd "
            "  FROM replay_state_snapshots "
            " WHERE session_id = :sid "
            "ORDER BY t_ms ASC"
        ),
        {"sid": session_id},
    )
    out: list[ReplayEvent] = []
    for row in result:
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


async def delete_replay(session: AsyncSession, session_id: str) -> int:
    """Delete every replay row for one session across all four tables."""
    total = 0
    for table in _TABLE_BY_MODALITY.values():
        result = await session.execute(
            text(f"DELETE FROM {table} WHERE session_id = :sid"),
            {"sid": session_id},
        )
        if result.rowcount is not None and result.rowcount > 0:  # type: ignore[attr-defined]
            total += result.rowcount  # type: ignore[attr-defined]
    await session.commit()
    return total


async def aggregate_storage_per_session(session: AsyncSession, session_id: str) -> int:
    """Sum the JSON-payload byte length across all four tables."""
    total = 0
    for table in _TABLE_BY_MODALITY.values():
        result = await session.execute(
            text(
                f"SELECT COALESCE(SUM(length(payload)), 0) FROM {table} "
                "WHERE session_id = :sid"
            ),
            {"sid": session_id},
        )
        row = result.fetchone()
        if row is not None and row[0] is not None:
            total += int(row[0])
    return total


__all__ = [
    "aggregate_storage_per_session",
    "bulk_write_events",
    "delete_replay",
    "read_full_replay",
]
