"""Per-turn timing capture for voice-conversation metrics."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Final

from voicegateway.inference.session.context import get_session_id

logger = logging.getLogger(__name__)


_DEFAULT_FLUSH_SIZE: Final[int] = 25


@dataclass
class TurnRow:
    """One caller-agent turn captured by :class:`TurnTracker`."""

    session_id: str
    turn_index: int
    caller_speak_start_ms: int
    caller_speak_end_ms: int
    agent_speak_start_ms: int | None = None
    agent_speak_end_ms: int | None = None
    response_speed_ms: int | None = None


FlushCallback = Callable[[list[TurnRow]], Awaitable[None]]


async def _noop_flush(rows: list[TurnRow]) -> None:
    """Default callback used when no repository is wired yet."""
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
    """Records per-turn caller/agent speech intervals keyed by session id."""

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

    async def on_user_started_speaking(
        self,
        session_id: str | None = None,
        at_ms: int | None = None,
    ) -> None:
        """Record the caller-speech start for the current turn."""
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
        """Record the caller-speech end for the in-flight turn."""
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
        """Close the turn boundary: append a ``TurnRow`` to the buffer."""
        sid = self._resolve_session_id(session_id)
        if sid is None:
            return
        agent_start = at_ms if at_ms is not None else self._now_ms()
        flush_now = False
        async with self._lock:
            state = self._sessions.get(sid)
            if state is None or state.pending_caller_start_ms is None:
                return
            caller_start = state.pending_caller_start_ms
            caller_end = state.pending_caller_end_ms
            if caller_end is None:
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

    async def flush_session(self, session_id: str) -> int:
        """Flush buffered turns for one session via the registered callback."""
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

            raise
        return len(to_flush)

    async def close_session(self, session_id: str) -> int:
        """Finalize a session: flush remaining turns and drop the state."""
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
        """Return the list of session ids with in-flight or buffered state."""
        return list(self._sessions.keys())

    @staticmethod
    def _now_ms() -> int:
        return int(time.monotonic() * 1000)

    @staticmethod
    def _resolve_session_id(session_id: str | None) -> str | None:
        return session_id if session_id is not None else get_session_id()


__all__ = ["FlushCallback", "TurnRow", "TurnTracker"]
