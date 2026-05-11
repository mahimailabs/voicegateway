"""Per-session replay-event capture for the v0.3.0 conversation timeline.

Implements REQ-VG-REPLAY-003 (every captured event along the way): each
STT chunk, each LLM token, and each TTS frame the Instrumented* wrappers
emit becomes a typed ``ReplayEvent`` keyed by ``session_id`` and indexed
by start-relative milliseconds. The events accumulate in a bounded
per-session asyncio buffer; when the buffer fills, the oldest event is
dropped and a per-session ``dropped_count`` increments so the dashboard
can surface "events dropped here" rather than silently misleading the
developer.

Flush triggers:

- ``flush_size_events`` reached -> async flush to the injected
  ``flush_callback``.
- ``close_session(session_id)`` -> final flush + state drop.

The class is repository-agnostic. Callers inject a ``FlushCallback``
(async callable taking ``list[ReplayEvent]``). A default no-op callback
keeps the capture usable before ``replay_repo`` lands (T05). The
``ProjectConfig.replay`` knobs from T07 set ``buffer_size_events``
(default 5000) and ``flush_size_events`` (default 500).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final

from voicegateway.inference._session_context import get_session_id

logger = logging.getLogger(__name__)


_DEFAULT_BUFFER_SIZE: Final[int] = 5000
_DEFAULT_FLUSH_SIZE: Final[int] = 500


@dataclass
class ReplayEvent:
    """One captured replay event.

    Maps 1-to-1 to one of the four ``replay_*`` table rows created by
    storage migration 0004 (T04). ``modality`` selects which table the
    event lands in (``stt``, ``llm``, ``tts``); ``payload`` is the
    JSON-serializable per-modality body. ``cost_usd`` is nullable
    (provider-specific) and surfaces in the RunningCostCounter (T12).

    State snapshots (REQ-VG-REPLAY-005) use the same shape with
    ``modality="state"`` so the replay timeline can interleave snapshots
    with the three streaming modalities under a single buffer.
    """

    session_id: str
    modality: str  # "stt" | "llm" | "tts" | "state"
    t_ms: int  # start-relative milliseconds
    payload: dict[str, Any]
    provider: str = ""
    cost_usd: float | None = None


FlushCallback = Callable[[list[ReplayEvent]], Awaitable[None]]


async def _noop_flush(events: list[ReplayEvent]) -> None:
    """Default callback used before replay_repo (T05) is wired."""
    if events:
        logger.debug(
            "ReplayCapture no-op flush: %d events dropped (no repository wired)",
            len(events),
        )


@dataclass
class _SessionState:
    """In-flight per-session capture state."""

    started_at_ms: int  # monotonic ms when the first event arrived
    buffer: deque[ReplayEvent] = field(default_factory=deque)
    dropped_count: int = 0


class ReplayCapture:
    """Captures replay events for v0.3.0's conversation timeline.

    Multiple concurrent sessions are supported; per-session state is
    keyed by ``session_id`` (resolved from the explicit kwarg or the
    v0.0.5 ``voicegateway.inference`` ContextVar). All mutating
    operations go through an internal asyncio lock so concurrent
    callbacks from the STT/LLM/TTS wrappers cannot interleave a
    partial buffer.

    Backpressure: ``buffer_size_events`` caps the per-session deque.
    When the cap is reached, ``append`` drops the oldest event and
    increments ``dropped_count``. This matches the Refinery's "dropped
    count is recorded and surfaced as 'events dropped here'" contract.

    Example::

        capture = ReplayCapture(flush_callback=replay_repo.bulk_write_events)
        await capture.record_stt_chunk(text="hello", is_final=True,
                                       provider="deepgram", cost_usd=0.0001)
        # ... session runs ...
        await capture.close_session(session_id)
    """

    def __init__(
        self,
        flush_callback: FlushCallback | None = None,
        flush_size_events: int = _DEFAULT_FLUSH_SIZE,
        buffer_size_events: int = _DEFAULT_BUFFER_SIZE,
    ) -> None:
        if flush_size_events < 1:
            raise ValueError(f"flush_size_events must be >= 1, got {flush_size_events}")
        if buffer_size_events < 1:
            raise ValueError(
                f"buffer_size_events must be >= 1, got {buffer_size_events}"
            )
        if flush_size_events > buffer_size_events:
            raise ValueError(
                f"flush_size_events ({flush_size_events}) must be <= "
                f"buffer_size_events ({buffer_size_events})"
            )
        self._flush_callback: FlushCallback = flush_callback or _noop_flush
        self._flush_size = flush_size_events
        self._buffer_size = buffer_size_events
        self._sessions: dict[str, _SessionState] = {}
        self._lock = asyncio.Lock()

    # ---- public capture API -----------------------------------------------

    async def record_stt_chunk(
        self,
        text: str,
        *,
        is_final: bool = False,
        alternatives: list[str] | None = None,
        provider: str = "",
        cost_usd: float | None = None,
        session_id: str | None = None,
        at_ms: int | None = None,
    ) -> None:
        """Record one STT chunk (partial or final transcription)."""
        await self._append(
            modality="stt",
            payload={
                "text": text,
                "is_final": is_final,
                "alternatives": alternatives or [],
            },
            provider=provider,
            cost_usd=cost_usd,
            session_id=session_id,
            at_ms=at_ms,
        )

    async def record_llm_token(
        self,
        token_text: str,
        *,
        role: str = "assistant",
        is_tool_invoke: bool = False,
        tool_args_partial: str | None = None,
        provider: str = "",
        cost_usd: float | None = None,
        session_id: str | None = None,
        at_ms: int | None = None,
    ) -> None:
        """Record one LLM token (or a tool-invoke marker)."""
        await self._append(
            modality="llm",
            payload={
                "token_text": token_text,
                "role": role,
                "is_tool_invoke": is_tool_invoke,
                "tool_args_partial": tool_args_partial,
            },
            provider=provider,
            cost_usd=cost_usd,
            session_id=session_id,
            at_ms=at_ms,
        )

    async def record_tts_frame(
        self,
        *,
        frame_duration_ms: int,
        underrun: bool = False,
        voice_id: str = "",
        provider: str = "",
        cost_usd: float | None = None,
        session_id: str | None = None,
        at_ms: int | None = None,
    ) -> None:
        """Record one TTS audio frame (marker, not raw audio)."""
        await self._append(
            modality="tts",
            payload={
                "frame_duration_ms": frame_duration_ms,
                "underrun": underrun,
                "voice_id": voice_id,
            },
            provider=provider,
            cost_usd=cost_usd,
            session_id=session_id,
            at_ms=at_ms,
        )

    async def record_state_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        session_id: str | None = None,
        at_ms: int | None = None,
    ) -> None:
        """Record one conversation-state snapshot.

        ``snapshot`` carries the system prompt, message history,
        in-flight tool call, and structured outputs collected. Owned
        by the state_snapshotter (T03); ReplayCapture is the
        shared buffer.
        """
        await self._append(
            modality="state",
            payload=snapshot,
            provider="",
            cost_usd=None,
            session_id=session_id,
            at_ms=at_ms,
        )

    # ---- lifecycle helpers -------------------------------------------------

    async def flush_session(self, session_id: str) -> int:
        """Flush buffered events for one session. Session stays alive.

        Returns the number of events flushed.
        """
        async with self._lock:
            state = self._sessions.get(session_id)
            if state is None or not state.buffer:
                return 0
            to_flush = list(state.buffer)
            state.buffer.clear()
        try:
            await self._flush_callback(to_flush)
        except Exception:
            logger.exception(
                "ReplayCapture flush_callback raised; dropping %d events",
                len(to_flush),
            )
            raise
        return len(to_flush)

    async def close_session(self, session_id: str) -> int:
        """Final flush + drop the per-session state."""
        flushed = await self.flush_session(session_id)
        async with self._lock:
            self._sessions.pop(session_id, None)
        return flushed

    def dropped_count(self, session_id: str) -> int:
        """Return the running dropped-count for the session (for the
        replay timeline's "events dropped here" surface)."""
        state = self._sessions.get(session_id)
        return state.dropped_count if state else 0

    def active_sessions(self) -> list[str]:
        """List session ids with in-flight or buffered state."""
        return list(self._sessions.keys())

    # ---- internals ---------------------------------------------------------

    async def _append(
        self,
        modality: str,
        payload: dict[str, Any],
        provider: str,
        cost_usd: float | None,
        session_id: str | None,
        at_ms: int | None,
    ) -> None:
        sid = self._resolve_session_id(session_id)
        if sid is None:
            logger.debug(
                "ReplayCapture._append (%s) without session_id; dropping",
                modality,
            )
            return
        now_ms = at_ms if at_ms is not None else self._now_ms()
        flush_now = False
        async with self._lock:
            state = self._sessions.get(sid)
            if state is None:
                state = _SessionState(started_at_ms=now_ms)
                self._sessions[sid] = state
            relative_ms = max(0, now_ms - state.started_at_ms)
            event = ReplayEvent(
                session_id=sid,
                modality=modality,
                t_ms=relative_ms,
                payload=payload,
                provider=provider,
                cost_usd=cost_usd,
            )
            if len(state.buffer) >= self._buffer_size:
                # Buffer full: drop oldest and record the drop.
                state.buffer.popleft()
                state.dropped_count += 1
                # No-op log at warning level so a persistent overflow
                # is visible in production.
                if state.dropped_count % 100 == 1:
                    logger.warning(
                        "ReplayCapture buffer overflow on session %s "
                        "(%d events dropped so far)",
                        sid,
                        state.dropped_count,
                    )
            state.buffer.append(event)
            flush_now = len(state.buffer) >= self._flush_size
        if flush_now:
            await self.flush_session(sid)

    @staticmethod
    def _now_ms() -> int:
        return int(time.monotonic() * 1000)

    @staticmethod
    def _resolve_session_id(session_id: str | None) -> str | None:
        return session_id if session_id is not None else get_session_id()


__all__ = [
    "FlushCallback",
    "ReplayCapture",
    "ReplayEvent",
]
