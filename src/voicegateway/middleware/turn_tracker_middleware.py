"""Per-turn timing capture for voice-conversation metrics."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Final

from voicegateway.inference.session.context import get_session_id
from voicegateway.middleware.base_middleware import SessionScopedComponent

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
    # Agent configuration revision. See RequestRecord.revision.
    revision: str | None = None


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
    # Whether pending_caller_end_ms came from a source that knows when the
    # caller ACTUALLY stopped, rather than when something noticed.
    caller_end_is_precise: bool = False
    buffered_turns: list[TurnRow] = field(default_factory=list)
    # Tool calls dispatched and not yet terminal, by call_id. While this is
    # non-empty the agent's first audio frame is a HOLDING LINE, not the answer,
    # so it must not close the turn. A set rather than a counter because
    # tool_call_ended is keyed by call_id and can arrive out of order or twice;
    # discarding an id is idempotent where decrementing a counter is not.
    tools_in_flight: set[str] = field(default_factory=set)


class TurnTracker(SessionScopedComponent):
    """Records per-turn caller/agent speech intervals keyed by session id."""

    def __init__(
        self,
        flush_callback: FlushCallback | None = None,
        flush_size: int = _DEFAULT_FLUSH_SIZE,
        *,
        precise_end_required: bool = False,
        revision: str | None = None,
    ) -> None:
        """``precise_end_required`` gates ``response_speed_ms`` on a real anchor.

        Set it on transports whose stop event is known to lag the caller's true
        stop, which is every LiveKit build: ``user_state_changed -> listening``
        fires only after the VAD has waited out its silence window, so a stop
        stamped there is late by that window and the response speed derived from
        it is understated by the same amount. Flatteringly, which is why it
        would not have been noticed.

        With this set, a turn whose end was never corrected reports
        ``response_speed_ms = None`` rather than a number half a second too
        good. Mixing corrected and uncorrected turns in one percentile is worse
        than a gap: the gap is visible.
        """
        if flush_size < 1:
            raise ValueError(f"flush_size must be >= 1, got {flush_size}")
        self._flush_callback: FlushCallback = flush_callback or _noop_flush
        self._flush_size = flush_size
        self._precise_end_required = precise_end_required
        # Stamped on every row this tracker writes. Session-scoped rather than
        # per-turn: an agent's configuration cannot change mid-session, and
        # threading it per call would invite a caller to vary it within one.
        self._revision = revision
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
        *,
        precise: bool = False,
    ) -> None:
        """Record the caller-speech end for the in-flight turn.

        ``precise=True`` means ``at_ms`` is when the caller actually stopped,
        not when a listener noticed. A precise report may overwrite an imprecise
        one exactly once; nothing overwrites a precise one.
        """
        sid = self._resolve_session_id(session_id)
        if sid is None:
            return
        end_ms = at_ms if at_ms is not None else self._now_ms()
        async with self._lock:
            state = self._sessions.get(sid)
            if state is None or state.pending_caller_start_ms is None:
                return
            # First-wins AMONG EQUALS, matching on_user_started_speaking. Two
            # events can report the same boundary (a discrete
            # ``user_stopped_speaking`` on one build, the ``user_state_changed``
            # transition out of ``speaking`` on another), and both are bound so
            # either kind of build works. Between two imprecise reports the
            # first is the accurate one; overwriting would let the later stretch
            # the caller's speech.
            #
            # A precise report is the exception and MUST be able to overwrite,
            # because the imprecise one always arrives first: the state change
            # fires when the VAD gives up on more speech, while the corrected
            # anchor is only recoverable from the end-of-utterance metric that
            # follows. A plain first-wins guard here silently discards the
            # correction and leaves every number exactly as wrong as before.
            if state.pending_caller_end_ms is None:
                state.pending_caller_end_ms = end_ms
                state.caller_end_is_precise = precise
            elif precise and not state.caller_end_is_precise:
                state.pending_caller_end_ms = end_ms
                state.caller_end_is_precise = True

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
            if state.tools_in_flight:
                # FILLER, NOT THE ANSWER. An agent covering a slow tool call
                # with a holding line ("let me pull that up") emits its first
                # audio frame here, and closing on it reports the time to the
                # holding line. That understates the wait by however long the
                # tool took, and because filler turns report fast numbers they
                # pull p50 DOWN rather than showing up as outliers: the error
                # hides in the aggregate everyone reads.
                #
                # Returning leaves the turn open, so the next first frame after
                # the last tool goes terminal closes it on the answer. A turn
                # with no tool in flight never reaches this branch and is
                # byte-identical to before.
                return
            caller_start = state.pending_caller_start_ms
            caller_end = state.pending_caller_end_ms
            if caller_end is None:
                caller_end = agent_start
                response_speed = None
            elif self._precise_end_required and not state.caller_end_is_precise:
                # The boundary is still useful for talk time, where the VAD
                # window is noise. It is not usable as a latency headline, where
                # that same window is most of the number. Absent, not guessed.
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
                revision=self._revision,
            )
            state.buffered_turns.append(turn)
            state.turn_index += 1
            state.pending_caller_start_ms = None
            state.pending_caller_end_ms = None
            state.caller_end_is_precise = False
            flush_now = len(state.buffered_turns) >= self._flush_size
        if flush_now:
            await self.flush_session(sid)

    async def on_tool_started(
        self,
        session_id: str | None = None,
        call_id: str | None = None,
    ) -> None:
        """Mark a tool call in flight, holding the turn open past any filler."""
        sid = self._resolve_session_id(session_id)
        if sid is None or call_id is None:
            return
        async with self._lock:
            # setdefault, not get: the tool can be dispatched before any caller
            # speech was seen on this session, and dropping the id there would
            # leave a turn that closes on filler with nothing to explain why.
            state = self._sessions.setdefault(sid, _SessionState())
            state.tools_in_flight.add(call_id)

    async def on_tool_ended(
        self,
        session_id: str | None = None,
        call_id: str | None = None,
    ) -> None:
        """Clear a tool call, whatever its outcome.

        DONE, ERROR AND CANCELLED ARE THE SAME EVENT HERE. The turn is held open
        only because an answer is still coming; once the call is terminal no
        answer is coming from it, and a cancelled tool that stayed in flight
        would leave the turn open to swallow the caller's next utterance. One
        cancelled tool would then corrupt every later row in the call, which is
        worse than the mistimed row this whole change exists to fix.
        """
        sid = self._resolve_session_id(session_id)
        if sid is None or call_id is None:
            return
        async with self._lock:
            state = self._sessions.get(sid)
            if state is None:
                return
            state.tools_in_flight.discard(call_id)

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
                    revision=self._revision,
                )
                state.buffered_turns.append(tail)
                state.turn_index += 1
                state.pending_caller_start_ms = None
                state.pending_caller_end_ms = None
                state.caller_end_is_precise = False
        flushed = await self.flush_session(session_id)
        async with self._lock:
            self._sessions.pop(session_id, None)
        return flushed

    def active_sessions(self) -> list[str]:
        """Return the list of session ids with in-flight or buffered state."""
        return list(self._sessions.keys())

    @staticmethod
    def _now_ms() -> int:
        """Epoch milliseconds. Deliberately NOT ``time.monotonic()``.

        These values are written to disk and read back by other processes.
        Monotonic is time since an arbitrary origin (the host boot on Linux and
        macOS), so a stored absolute monotonic value means nothing outside the
        process that produced it, and nothing at all after a reboot.

        ``rooms.py`` is where that bites: it gathers turns from every session in
        a room and sorts them on ``caller_speak_start_ms`` to build one
        chronological order. Its own comment names the cases, "an agent that
        reconnected, two agents in one room", which are exactly the cases where
        the values come from different processes on possibly different hosts.
        Sorting them together was ordering by two unrelated clocks.

        Epoch also makes the end-of-utterance correction exact instead of
        approximate. LiveKit computes its anchor as
        ``last_speaking_time = metric.timestamp - end_of_utterance_delay``, both
        in ``time.time()``, so subtracting in the same units reproduces LiveKit's
        own number rather than estimating it through a monotonic bridge.

        The trade is NTP. A wall clock can step, where monotonic cannot, which
        would corrupt a delta spanning the step. Within one turn a slew is
        microseconds, a step is rare and isolated to that turn, and
        ``response_speed`` already clamps at zero. A rare, bounded error beats a
        systematic one on every cross-session read.
        """
        return int(time.time() * 1000)

    @staticmethod
    def _resolve_session_id(session_id: str | None) -> str | None:
        return session_id if session_id is not None else get_session_id()


__all__ = ["FlushCallback", "TurnRow", "TurnTracker"]
