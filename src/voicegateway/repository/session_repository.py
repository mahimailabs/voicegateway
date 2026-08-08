"""Async repo for the sessions table + session-row finalize helpers (ORM).

Also owns the **sessions <-> calls correlation**: the write side that points a
session at the call it ran inside (:func:`correlate_session_to_call`) and the
read side that measures how often that join actually resolves
(:func:`read_correlation_rate`). See the block comment above the correlation
section for the join key's limits and for the metric's denominator.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from voicegateway.repository import (
    replay_repository as replay,
)
from voicegateway.repository import (
    turns_repository as turns,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_logger = logging.getLogger(__name__)


_SESSION_ORDER_CLAUSES: dict[str, str] = {
    "started_at_desc": "started_at DESC",
    "started_at_asc": "started_at ASC",
    "cost_desc": "total_cost_usd DESC, started_at DESC",
    "cost_asc": "total_cost_usd ASC, started_at DESC",
}


def row_to_session(row: Any) -> dict[str, Any]:
    """Map a session row to the dict shape callers expect."""
    out = {
        "id": row[0],
        "project": row[1],
        "started_at": row[2],
        "ended_at": row[3],
        "modalities": row[4].split(",") if row[4] else [],
        "total_cost_usd": float(row[5] or 0.0),
        "request_count": int(row[6] or 0),
    }
    try:
        tenant_id = row[7]
        out["tenant_id"] = None if tenant_id is None else str(tenant_id)
    except (IndexError, KeyError):
        pass
    try:
        routed_llm = row[8]
        routed_tts = row[9]
        budget_ms = row[10]
        budget_overrun = row[11]
        out["routed_llm"] = None if routed_llm is None else str(routed_llm)
        out["routed_tts"] = None if routed_tts is None else str(routed_tts)
        out["budget_ms"] = None if budget_ms is None else int(budget_ms)
        out["budget_overrun"] = None if budget_overrun is None else bool(budget_overrun)
    except (IndexError, KeyError):
        pass
    return out


_BASE_SELECT = (
    "SELECT id, project, started_at, ended_at, modalities, "
    "       total_cost_usd, request_count, tenant_id, "
    "       routed_llm, routed_tts, budget_ms, budget_overrun "
    "FROM sessions"
)


async def list_sessions(
    session: AsyncSession,
    limit: int = 100,
    project: str | None = None,
    order_by: str = "started_at_desc",
    tenant: str | None = None,
    agent: str | None = None,
) -> list[dict[str, Any]]:
    """Return recent sessions, ordered per ``order_by``."""
    clause = _SESSION_ORDER_CLAUSES.get(order_by)
    if clause is None:
        supported = ", ".join(sorted(_SESSION_ORDER_CLAUSES))
        raise ValueError(f"Unknown order_by {order_by!r}. Supported: {supported}.")
    conditions: list[str] = []
    params: dict[str, Any] = {"limit": limit}
    if project:
        conditions.append("project = :project")
        params["project"] = project
    if tenant is not None:
        if tenant == "":
            conditions.append("tenant_id IS NULL")
        else:
            conditions.append("tenant_id = :tenant")
            params["tenant"] = tenant
    if agent is not None:
        if agent == "":
            conditions.append("agent_id IS NULL")
        else:
            conditions.append("agent_id = :agent")
            params["agent"] = agent
    where = f"WHERE {' AND '.join(conditions)} " if conditions else ""
    result = await session.execute(
        text(f"{_BASE_SELECT} {where}ORDER BY {clause} LIMIT :limit"),
        params,
    )
    return [row_to_session(row) for row in result]


async def get_session(session: AsyncSession, session_id: str) -> dict[str, Any] | None:
    """Return one session by id + per-modality / provider breakdowns."""
    result = await session.execute(
        text(f"{_BASE_SELECT} WHERE id = :session_id"),
        {"session_id": session_id},
    )
    row = result.fetchone()
    if row is None:
        return None
    out = row_to_session(row)

    mod_result = await session.execute(
        text(
            "SELECT modality, COALESCE(SUM(cost_usd), 0) AS cost, "
            "COUNT(*) AS request_count "
            "FROM requests WHERE session_id = :session_id GROUP BY modality"
        ),
        {"session_id": session_id},
    )
    out["by_modality"] = {
        mod_row[0]: {
            "cost": float(mod_row[1] or 0.0),
            "request_count": int(mod_row[2] or 0),
        }
        for mod_row in mod_result
    }

    prov_result = await session.execute(
        text(
            "SELECT DISTINCT provider FROM requests "
            "WHERE session_id = :session_id ORDER BY provider"
        ),
        {"session_id": session_id},
    )
    out["providers"] = [prov_row[0] for prov_row in prov_result]
    return out


async def finalize_session_metrics(
    session: AsyncSession,
    session_id: str,
    *,
    min_overlap_ms: int = turns.DEFAULT_TALK_OVER_MIN_OVERLAP_MS,
) -> None:
    """Recompute and upsert the five aggregate columns on a session row.

    ``min_overlap_ms`` reaches ``count_overlap_turns``, which is what makes
    ``metrics.talk_over_min_overlap_ms`` mean something.
    """
    session_turns = await turns.list_turns_by_session(session, session_id)
    if not session_turns:
        return

    talk_time_ms = 0
    for t in session_turns:
        talk_time_ms += t.caller_speak_end_ms - t.caller_speak_start_ms
        if t.agent_speak_start_ms is not None and t.agent_speak_end_ms is not None:
            talk_time_ms += t.agent_speak_end_ms - t.agent_speak_start_ms
    talk_time_seconds = talk_time_ms / 1000.0

    cost_result = await session.execute(
        text("SELECT total_cost_usd FROM sessions WHERE id = :session_id"),
        {"session_id": session_id},
    )
    cost_row = cost_result.fetchone()
    total_cost = (
        float(cost_row[0]) if cost_row is not None and cost_row[0] is not None else 0.0
    )
    per_minute_cost = (
        total_cost / (talk_time_seconds / 60.0) if talk_time_seconds > 0 else None
    )

    pcts = await turns.aggregate_response_speed(session, session_id)
    overlap_count = await turns.count_overlap_turns(
        session, session_id, min_overlap_ms=min_overlap_ms
    )
    total_turns = len(session_turns)
    talk_over_rate = overlap_count / total_turns if total_turns > 0 else None

    await session.execute(
        text(
            "UPDATE sessions "
            "   SET talk_time_seconds = :tts, "
            "       per_minute_cost_usd = :pmc, "
            "       response_speed_p50_ms = :p50, "
            "       response_speed_p95_ms = :p95, "
            "       talk_over_rate = :tor "
            " WHERE id = :session_id"
        ),
        {
            "tts": talk_time_seconds,
            "pmc": per_minute_cost,
            "p50": pcts["p50_ms"],
            "p95": pcts["p95_ms"],
            "tor": talk_over_rate,
            "session_id": session_id,
        },
    )
    await session.commit()


async def finalize_session_replay(session: AsyncSession, session_id: str) -> None:
    """Compute ``replay_size_bytes`` for the session and upsert the column."""
    size_bytes = await replay.aggregate_storage_per_session(session, session_id)
    if size_bytes <= 0:
        return
    await session.execute(
        text(
            "UPDATE sessions SET replay_size_bytes = :size_bytes WHERE id = :session_id"
        ),
        {"size_bytes": size_bytes, "session_id": session_id},
    )
    await session.commit()


# ---------------------------------------------------------------------------
# sessions <-> calls correlation
#
# ``sessions.room_name`` is the ONLY join key there is, and it is best effort by
# construction: it comes from ``record.metadata["room"]``, which ``attach``
# stamps only when it resolved a LiveKit job context. A web or Pipecat session
# has no room and can therefore NEVER join -- that is correct behaviour, not a
# failure, which is why such sessions are reported separately rather than
# counted against the rate (see :func:`read_correlation_rate`).
#
# The key is also NOT UNIQUE. ``calls`` keys on ``room_sid`` precisely because a
# deployment that pins one fixed room name would otherwise collapse two
# concurrent calls into one row, so a room name may match 0, 1 or MANY calls.
# Ambiguity is refused, never guessed: pointing a session at one of several
# candidate calls would silently attribute its cost and its latency to the wrong
# call, and nothing downstream could tell.
#
# Forward-only. Nothing here scans or repairs history: a ``calls`` row can only
# exist from the moment the webhook receiver was deployed, so an older session
# has nothing to join to and a backfill would either find nothing or invent a
# link. Instead the resolution is retried on the session's next request, which
# is what closes the ordinary race (the first request of a call can easily beat
# the ``room_started`` webhook) with no repair pass at all.
# ---------------------------------------------------------------------------


# The rate below which the correlation number is worth an operator's attention.
# 90%: a healthy LiveKit deployment correlates essentially every session that
# had a room, so the gap between 100% and this default is slack for the in-flight
# sessions whose call row simply has not been written yet at read time.
CORRELATION_WARN_THRESHOLD = 0.9

# The closed set of :attr:`CorrelationRate.status` values. "unknown" is a real,
# distinct answer: with an empty denominator there is no rate to publish, and
# neither 100% ("everything correlated") nor 0% ("nothing correlated") would be
# true.
CORRELATION_STATUSES: tuple[str, ...] = ("ok", "warn", "unknown")

# Distinct ambiguous room names remembered for log throttling. An ambiguous room
# name is a permanent property of a deployment that pins one room, so warning per
# request would flood the log of exactly the load test this metric exists to
# measure. Bounded, because the key is attacker-adjacent input (a room name).
_AMBIGUOUS_ROOM_LOG_SLOTS = 256


@lru_cache(maxsize=_AMBIGUOUS_ROOM_LOG_SLOTS)
def _warn_ambiguous_room(room_name: str) -> None:
    """Warn once per room name per process. Memoized on purpose (see above)."""
    _logger.warning(
        "room %r matches more than one call, so the sessions in it are left "
        "uncorrelated: room_name is not a unique key, and picking one call "
        "would attribute a session to the wrong one. Correlate by giving each "
        "call its own room name.",
        room_name,
    )


@dataclass(frozen=True)
class CorrelationRate:
    """How often the sessions <-> calls join actually resolves.

    ``rate`` is ``correlated / eligible``, and it is ``None`` -- never 1.0 and
    never 0.0 -- when ``eligible`` is 0, because a rate with an empty
    denominator is not a measurement.
    """

    # Denominator: sessions that HAD a room and so should have joined.
    eligible: int
    # Numerator: of those, the ones whose call_id points at a call that exists.
    correlated: int
    rate: float | None
    # Uncorrelated because the room name matched more than one call. A subset of
    # ``eligible - correlated``, broken out because it is the one failure mode a
    # correct webhook pipeline still produces, and it needs a different fix.
    ambiguous: int
    # Sessions whose call_id points at a call that is GONE (calls prune on their
    # own clock, so a session can outlive the call it names). Excluded from
    # ``correlated``: a dangling pointer is not a correlation.
    dangling: int
    # Sessions with no room at all -- web and Pipecat. Excluded from BOTH the
    # numerator and the denominator: they could never join, and counting them as
    # failures would peg the rate low forever and train everyone to ignore it.
    no_room: int
    warn_threshold: float
    status: str


async def correlate_session_to_call(
    db: AsyncSession, *, session_id: str, room_name: str
) -> str | None:
    """Point one session at the call it ran inside. Returns the ``calls.id``.

    Resolution by ``room_name``, with all three cardinalities handled explicitly:

    * **exactly one match** -- the session is pointed at it and the id returned.
    * **no match** -- nothing is written and ``None`` is returned. The call row
      may simply not exist yet (the first request of a call can beat the
      ``room_started`` webhook, and a deployment with no webhook receiver has no
      call rows at all), so this is retried on the session's next request rather
      than repaired later.
    * **more than one match** -- nothing is written, ``None`` is returned and the
      room is warned about once per process. Room names are not unique; guessing
      would silently file a session under the wrong call.

    Already-correlated sessions are left alone and their existing id is returned:
    the first resolution wins, so a second call later taking the same room name
    cannot re-point a session that was resolved unambiguously at the time.

    Does not commit -- the caller owns the transaction, so the correlation lands
    in the same commit as the session row it describes.
    """
    current = (
        await db.execute(
            text("SELECT call_id FROM sessions WHERE id = :sid"),
            {"sid": session_id},
        )
    ).fetchone()
    if current is None:
        # No session row to correlate. Not reachable from ``log_request`` (the
        # UPSERT runs first), so this only guards a direct caller.
        return None
    if current[0] is not None:
        return str(current[0])

    # LIMIT 2 is exactly enough to tell 0 from 1 from many, and reading more
    # would only be a bigger scan for an answer that is already decided.
    rows = (
        await db.execute(
            text("SELECT id FROM calls WHERE room_name = :room LIMIT 2"),
            {"room": room_name},
        )
    ).fetchall()
    if not rows:
        _logger.debug(
            "no call row for room %r yet; session %s stays uncorrelated",
            room_name,
            session_id,
        )
        return None
    if len(rows) > 1:
        _warn_ambiguous_room(room_name)
        return None

    call_id = str(rows[0][0])
    # ``call_id IS NULL`` in the predicate, not just in the read above: two
    # concurrent requests of the same session can both find it unset, and the
    # first writer must win rather than the last.
    await db.execute(
        text("UPDATE sessions SET call_id = :cid WHERE id = :sid AND call_id IS NULL"),
        {"cid": call_id, "sid": session_id},
    )
    return call_id


def _correlation_filters(
    project: str | None, since: str | None, prefix: str
) -> tuple[str, dict[str, Any]]:
    """Shared WHERE terms for both correlation-rate statements.

    The probe exclusion is not optional: VoiceGateway's own probe rooms are
    synthetic traffic it created itself, and every other read surface in this
    package already excludes them (``list_calls``, ``read_avg_ttfb_by_modality``).
    Leaving them in would let a burst of probes move a number that is supposed to
    describe real calls.

    ``since`` is compared lexically against the ISO-8601 ``started_at``, which is
    what the retention pass already does with ``ended_at``: every writer formats
    with ``datetime.isoformat()`` in UTC, so the strings sort chronologically.
    """
    # Imported here, not at module scope: ``request_log_repository`` imports THIS
    # module for the write side above, and a module-level import back would make
    # the pair fail on whichever of the two is imported first.
    from voicegateway.repository.request_log_repository import PROBE_ROOM_PREFIX

    conditions = [
        f"({prefix}room_name IS NULL OR {prefix}room_name NOT LIKE :probe_prefix)"
    ]
    params: dict[str, Any] = {"probe_prefix": f"{PROBE_ROOM_PREFIX}%"}
    if project:
        conditions.append(f"{prefix}project = :project")
        params["project"] = project
    if since:
        conditions.append(f"{prefix}started_at >= :since")
        params["since"] = since
    return " AND ".join(conditions), params


async def read_correlation_rate(
    db: AsyncSession,
    *,
    project: str | None = None,
    since: str | None = None,
    warn_threshold: float | None = None,
) -> CorrelationRate:
    """Measure the sessions <-> calls join. See :class:`CorrelationRate`.

    Shipped from day one because the join fails SILENTLY: a missing webhook
    receiver, a room name the agent never saw, or a room pinned across concurrent
    calls all produce a plausible-looking dashboard with no correlation behind it.
    Without this number nobody finds out.

    The denominator is **sessions that had a room**, not all sessions. A session
    with no room could never have joined, so counting it as a failure would make
    the number a permanent alarm; it is reported as ``no_room`` instead, where an
    unexpected count is still visible.

    ``since`` bounds the window (ISO-8601, against ``started_at``); an all-time
    rate is dominated by however the deployment was configured months ago.
    """
    threshold = CORRELATION_WARN_THRESHOLD if warn_threshold is None else warn_threshold
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"warn_threshold must be within [0, 1], got {threshold}")

    where, params = _correlation_filters(project, since, "s.")
    # LEFT JOIN, so a session pointing at a pruned call lands in ``dangling``
    # rather than in ``correlated``. SUM(CASE ...) rather than COUNT(...) FILTER:
    # the FILTER clause needs SQLite 3.30+, and this has to run wherever the
    # engine runs.
    totals = (
        await db.execute(
            text(f"""
            SELECT
                SUM(CASE WHEN s.room_name IS NOT NULL THEN 1 ELSE 0 END),
                SUM(CASE WHEN s.room_name IS NOT NULL AND c.id IS NOT NULL
                         THEN 1 ELSE 0 END),
                SUM(CASE WHEN s.room_name IS NOT NULL AND s.call_id IS NOT NULL
                              AND c.id IS NULL
                         THEN 1 ELSE 0 END),
                SUM(CASE WHEN s.room_name IS NULL THEN 1 ELSE 0 END)
            FROM sessions s
            LEFT JOIN calls c ON c.id = s.call_id
            WHERE {where}
        """),
            params,
        )
    ).fetchone()
    # SUM over zero rows is NULL, and fetchone() on an aggregate always returns a
    # row, so both have to collapse to 0 here.
    eligible = int(totals[0] or 0) if totals is not None else 0
    correlated = int(totals[1] or 0) if totals is not None else 0
    dangling = int(totals[2] or 0) if totals is not None else 0
    no_room = int(totals[3] or 0) if totals is not None else 0

    # Ambiguity is a property of the room name TODAY, so it is counted at read
    # time: nothing was written when the write side refused to guess.
    ambiguous_row = (
        await db.execute(
            text(f"""
            SELECT COUNT(*)
            FROM sessions s
            WHERE {where}
              AND s.room_name IS NOT NULL
              AND s.call_id IS NULL
              AND (SELECT COUNT(*) FROM calls c
                   WHERE c.room_name = s.room_name) > 1
        """),
            params,
        )
    ).fetchone()
    ambiguous = int(ambiguous_row[0] or 0) if ambiguous_row is not None else 0

    rate = correlated / eligible if eligible else None
    if rate is None:
        status = "unknown"
    elif rate < threshold:
        status = "warn"
    else:
        status = "ok"
    return CorrelationRate(
        eligible=eligible,
        correlated=correlated,
        rate=rate,
        ambiguous=ambiguous,
        dangling=dangling,
        no_room=no_room,
        warn_threshold=threshold,
        status=status,
    )


__all__ = [
    "CORRELATION_STATUSES",
    "CORRELATION_WARN_THRESHOLD",
    "CorrelationRate",
    "correlate_session_to_call",
    "finalize_session_metrics",
    "finalize_session_replay",
    "get_session",
    "list_sessions",
    "read_correlation_rate",
    "row_to_session",
]
