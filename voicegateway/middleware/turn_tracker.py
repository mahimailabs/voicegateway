"""Per-turn timing capture for voice-conversation metrics.

Implements REQ-VG-METRICS-002 (agent response speed per turn) and contributes
the raw caller/agent speech intervals that REQ-VG-METRICS-003 (talk-over rate)
computes by post-hoc interval overlap in ``turns_repo`` (T05).

The :class:`TurnTracker` is repository-agnostic: callers inject a
``flush_callback`` (async callable taking ``list[TurnRow]``). A no-op default
keeps the tracker usable before the turns_repo lands; T08 wires the cost-tracker
side and T09 ships the explicit ``attach_session`` helper for non-standard
worker patterns (Foundry Open Question 1's escape hatch).

Event lifecycle, per session:

1. ``on_user_started_speaking`` records the caller-speech start.
2. ``on_user_stopped_speaking`` records the caller-speech end.
3. ``on_agent_audio_first_frame`` closes the turn: a ``TurnRow`` is appended
   to the per-session buffer with ``response_speed_ms`` computed as
   ``agent_speak_start_ms - caller_speak_end_ms``. If the caller-stop
   event was never observed, ``caller_speak_end_ms`` is inferred as the
   agent's first-frame timestamp and ``response_speed_ms`` is set to None
   to mark the inference. Auto-flushes when the buffer hits ``flush_size``.
4. ``on_agent_audio_last_frame`` sets ``agent_speak_end_ms`` on the
   most-recent buffered turn.
5. ``close_session`` flushes any remaining buffered turns and, if a caller
   pair was pending without an agent response, emits a final
   agent-never-speaks turn (per the Foundry test strategy:
   "agent-never-speaks case yields NULL response_speed_ms").

All event handlers are coroutines so they integrate with the async middleware
pipeline (cost_tracker, instrumented_provider). They are idempotent against
duplicate start events; the second ``on_user_started_speaking`` for a turn
in flight is silently dropped.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Final

from voicegateway.inference._session_context import get_session_id

logger = logging.getLogger(__name__)


_DEFAULT_FLUSH_SIZE: Final[int] = 25


@dataclass
class TurnRow:
    """One caller-agent turn captured by :class:`TurnTracker`.

    Maps 1-to-1 to the ``turns`` table created by storage migration 0003
    (T04). Timestamps are integer milliseconds on a monotonic-ish clock; the
    table stores them as INTEGER. ``agent_speak_*`` and ``response_speed_ms``
    are nullable for the agent-never-speaks case.
    """

    session_id: str
    turn_index: int
    caller_speak_start_ms: int
    caller_speak_end_ms: int
    agent_speak_start_ms: int | None = None
    agent_speak_end_ms: int | None = None
    response_speed_ms: int | None = None


FlushCallback = Callable[[list[TurnRow]], Awaitable[None]]


async def _noop_flush(rows: list[TurnRow]) -> None:
    """Default callback used when no repository is wired yet.

    Logs at debug level so the no-wiring case is observable in tests but
    does not noise production logs.
    """
    if rows:
        logger.debug(
            "TurnTracker no-op flush: %d turns dropped (no repository wired)",
            len(rows),
        )


@dataclass
class _SessionState:
    """In-flight per-session state."""

    turn_index: int = 0
    pending_caller_start_ms: int | None = None
    pending_caller_end_ms: int | None = None
    buffered_turns: list[TurnRow] = field(default_factory=list)


class TurnTracker:
    """Records per-turn caller/agent speech intervals keyed by session id.

    Multiple concurrent sessions are supported; per-session state is keyed by
    ``session_id`` (resolved from the explicit kwarg or the v0.0.5
    ``voicegateway.inference`` ContextVar). All mutating operations go
    through an internal asyncio lock so concurrent event callbacks from the
    STT and TTS plugins cannot interleave a partial turn.

    Example::

        from voicegateway.middleware.turn_tracker import TurnTracker

        tracker = TurnTracker(flush_callback=turns_repo.create_turns_bulk)
        # ... InstrumentedSTT and InstrumentedTTS plugins call tracker.on_*
        await tracker.close_session(session_id)
    """

    def __init__(
        self,
        flush_callback: FlushCallback | None = None,
        flush_size: int = _DEFAULT_FLUSH_SIZE,
    ) -> None:
        if flush_size < 1:
            raise ValueError(f"flush_size must be >= 1, got {flush_size}")
        self._flush_callback: FlushCallback = flush_callback or _noop_flush
        self._flush_size = flush_size
        self._sessions: dict[str, _SessionState] = {}
        self._lock = asyncio.Lock()

    # ---- public event handlers --------------------------------------------

    async def on_user_started_speaking(
        self,
        session_id: str | None = None,
        at_ms: int | None = None,
    ) -> None:
        """Record the caller-speech start for the current turn.

        Idempotent: a second start before an agent reply is silently
        dropped (the InstrumentedSTT plugin may emit duplicate VAD events
        during a single caller utterance).
        """
        sid = self._resolve_session_id(session_id)
        if sid is None:
            logger.debug("on_user_started_speaking with no session_id; ignoring")
            return
        start_ms = at_ms if at_ms is not None else self._now_ms()
        async with self._lock:
            state = self._sessions.setdefault(sid, _SessionState())
            if state.pending_caller_start_ms is None:
                state.pending_caller_start_ms = start_ms

    async def on_user_stopped_speaking(
        self,
        session_id: str | None = None,
        at_ms: int | None = None,
    ) -> None:
        """Record the caller-speech end for the in-flight turn.

        No-op if no caller-start was previously observed for this session.
        """
        sid = self._resolve_session_id(session_id)
        if sid is None:
            return
        end_ms = at_ms if at_ms is not None else self._now_ms()
        async with self._lock:
            state = self._sessions.get(sid)
            if state is None or state.pending_caller_start_ms is None:
                return
            state.pending_caller_end_ms = end_ms

    async def on_agent_audio_first_frame(
        self,
        session_id: str | None = None,
        at_ms: int | None = None,
    ) -> None:
        """Close the turn boundary: append a ``TurnRow`` to the buffer.

        Computes ``response_speed_ms`` as ``at_ms - caller_speak_end_ms``
        when the caller-stop event was observed; sets it to None and
        infers caller_speak_end_ms as ``at_ms`` otherwise.

        Auto-flushes when the buffer reaches ``flush_size``.
        """
        sid = self._resolve_session_id(session_id)
        if sid is None:
            return
        agent_start = at_ms if at_ms is not None else self._now_ms()
        flush_now = False
        async with self._lock:
            state = self._sessions.get(sid)
            if state is None or state.pending_caller_start_ms is None:
                # No caller activity preceded the agent speech (e.g. the
                # initial agent greeting). Not a turn in the REQ-002 sense.
                return
            caller_start = state.pending_caller_start_ms
            caller_end = state.pending_caller_end_ms
            if caller_end is None:
                # Caller-stop event was missed. Infer caller_end as the
                # agent's first-frame timestamp and null the response
                # speed so the row signals "we did not measure this".
                caller_end = agent_start
                response_speed = None
            else:
                response_speed = max(0, agent_start - caller_end)
            turn = TurnRow(
                session_id=sid,
                turn_index=state.turn_index,
                caller_speak_start_ms=caller_start,
                caller_speak_end_ms=caller_end,
                agent_speak_start_ms=agent_start,
                response_speed_ms=response_speed,
            )
            state.buffered_turns.append(turn)
            state.turn_index += 1
            state.pending_caller_start_ms = None
            state.pending_caller_end_ms = None
            flush_now = len(state.buffered_turns) >= self._flush_size
        if flush_now:
            await self.flush_session(sid)

    async def on_agent_audio_last_frame(
        self,
        session_id: str | None = None,
        at_ms: int | None = None,
    ) -> None:
        """Set ``agent_speak_end_ms`` on the most recently buffered turn."""
        sid = self._resolve_session_id(session_id)
        if sid is None:
            return
        agent_end = at_ms if at_ms is not None else self._now_ms()
        async with self._lock:
            state = self._sessions.get(sid)
            if state is None or not state.buffered_turns:
                return
            last = state.buffered_turns[-1]
            if last.agent_speak_end_ms is None:
                last.agent_speak_end_ms = agent_end

    # ---- lifecycle helpers -------------------------------------------------

    async def flush_session(self, session_id: str) -> int:
        """Flush buffered turns for one session via the registered callback.

        Returns the number of turns flushed. The session state stays alive
        so subsequent events keep accruing.
        """
        async with self._lock:
            state = self._sessions.get(session_id)
            if state is None or not state.buffered_turns:
                return 0
            to_flush = state.buffered_turns
            state.buffered_turns = []
        try:
            await self._flush_callback(to_flush)
        except Exception:
            logger.exception(
                "TurnTracker flush_callback raised; dropping %d turns",
                len(to_flush),
            )
            # Re-raise so the caller (cost_tracker session-close path) sees
            # the failure rather than silently losing data.
            raise
        return len(to_flush)

    async def close_session(self, session_id: str) -> int:
        """Finalize a session: flush remaining turns and drop the state.

        If a caller pair was pending without any agent reply, emits a final
        ``TurnRow`` with ``agent_speak_*`` and ``response_speed_ms`` set to
        None so the agent-never-speaks case is observable.

        Returns the total number of turns flushed (including the optional
        agent-never-speaks tail).
        """
        async with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return 0
            if state.pending_caller_start_ms is not None:
                tail = TurnRow(
                    session_id=session_id,
                    turn_index=state.turn_index,
                    caller_speak_start_ms=state.pending_caller_start_ms,
                    caller_speak_end_ms=(
                        state.pending_caller_end_ms
                        if state.pending_caller_end_ms is not None
                        else state.pending_caller_start_ms
                    ),
                    agent_speak_start_ms=None,
                    agent_speak_end_ms=None,
                    response_speed_ms=None,
                )
                state.buffered_turns.append(tail)
                state.turn_index += 1
                state.pending_caller_start_ms = None
                state.pending_caller_end_ms = None
        flushed = await self.flush_session(session_id)
        async with self._lock:
            self._sessions.pop(session_id, None)
        return flushed

    def active_sessions(self) -> list[str]:
        """Return the list of session ids with in-flight or buffered state.

        Read-only; primarily for diagnostics and test introspection.
        """
        return list(self._sessions.keys())

    # ---- internals ---------------------------------------------------------

    @staticmethod
    def _now_ms() -> int:
        return int(time.monotonic() * 1000)

    @staticmethod
    def _resolve_session_id(session_id: str | None) -> str | None:
        return session_id if session_id is not None else get_session_id()


__all__ = ["FlushCallback", "TurnRow", "TurnTracker"]
