"""Conversation-state snapshots at LLM and tool-call boundaries.

Implements REQ-VG-REPLAY-005 (see conversation state at every moment).
The replay timeline reconstructs agent state at any playhead position
by walking the most recent ``StateSnapshot`` plus subsequent message
diffs; this module owns the snapshot side. The diff-walk lives in the
dashboard ``ConversationStatePane`` (T12).

Snapshots fire at three boundary kinds:

- ``on_message_added`` -> after every LLM message append to the
  running history.
- ``on_tool_invoked`` -> at the moment the agent invokes a tool call.
- ``on_tool_resolved`` -> when the tool result arrives back.

A per-session rate cap of one snapshot per second guards against
storage explosion on chatty agents (the Foundry's "max one per
second" requirement). Boundary events that fire faster than the cap
are coalesced into the next-eligible snapshot.

The module is decoupled from the storage layer: callers inject an
async ``on_snapshot`` callback. The natural target is
:meth:`voicegateway.middleware.replay_capture.ReplayCapture.record_state_snapshot`
(T02), which routes the snapshot into the same per-session buffer
that handles backpressure for the other three modalities.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Final

from pydantic import BaseModel, Field

from voicegateway.inference._session_context import get_session_id

logger = logging.getLogger(__name__)


_DEFAULT_MIN_INTERVAL_SECONDS: Final[float] = 1.0


class StateSnapshot(BaseModel):
    """One captured conversation-state snapshot.

    Serialized to JSON and stored in the ``replay_state_snapshots``
    table (migration 0004, T04). All fields are optional so a partial
    snapshot (e.g. tool invocation with no structured output yet) is
    representable.
    """

    system_prompt: str = ""
    message_history: list[dict[str, Any]] = Field(default_factory=list)
    tool_call_in_flight: dict[str, Any] | None = None
    structured_output_collected: dict[str, Any] | None = None


SnapshotCallback = Callable[[dict[str, Any]], Awaitable[None]]


async def _noop_callback(snapshot: dict[str, Any]) -> None:
    """Default callback used when no ReplayCapture is wired yet."""
    logger.debug(
        "StateSnapshotter no-op callback: snapshot dropped (no capture wired)",
    )


class StateSnapshotter:
    """Captures conversation-state snapshots with a per-session rate cap.

    The snapshotter does NOT own state itself; callers pass the
    current state on each boundary event. The class only enforces
    the rate cap and forwards to the injected callback.

    Rate cap semantics: a boundary event that fires within
    ``min_interval_seconds`` of the prior snapshot for the same
    session is silently dropped. This is intentional coalescing per
    Foundry; the reconstruction in
    :class:`ConversationStatePane <dashboard>` walks message diffs
    between snapshots so coalesced state is still recoverable.

    Example::

        snap = StateSnapshotter(on_snapshot=capture.record_state_snapshot)
        await snap.on_message_added(
            session_id=sid,
            system_prompt="...",
            message_history=[...],
        )
    """

    def __init__(
        self,
        on_snapshot: SnapshotCallback | None = None,
        min_interval_seconds: float = _DEFAULT_MIN_INTERVAL_SECONDS,
    ) -> None:
        if min_interval_seconds < 0:
            raise ValueError(
                f"min_interval_seconds must be >= 0, got {min_interval_seconds}"
            )
        self._on_snapshot: SnapshotCallback = on_snapshot or _noop_callback
        self._min_interval = min_interval_seconds
        self._last_snapshot_ms: dict[str, int] = {}

    # ---- public boundary handlers ----------------------------------------

    async def on_message_added(
        self,
        *,
        system_prompt: str = "",
        message_history: list[dict[str, Any]] | None = None,
        session_id: str | None = None,
    ) -> bool:
        """Snapshot at LLM message-add boundary.

        Returns True if the snapshot fired, False if rate-capped.
        """
        snap = StateSnapshot(
            system_prompt=system_prompt,
            message_history=message_history or [],
            tool_call_in_flight=None,
            structured_output_collected=None,
        )
        return await self._emit(snap, session_id)

    async def on_tool_invoked(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any] | None = None,
        system_prompt: str = "",
        message_history: list[dict[str, Any]] | None = None,
        session_id: str | None = None,
    ) -> bool:
        """Snapshot at tool-call invocation boundary."""
        snap = StateSnapshot(
            system_prompt=system_prompt,
            message_history=message_history or [],
            tool_call_in_flight={
                "name": tool_name,
                "args": tool_args or {},
                "result": None,
            },
            structured_output_collected=None,
        )
        return await self._emit(snap, session_id)

    async def on_tool_resolved(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any] | None = None,
        result: Any = None,
        system_prompt: str = "",
        message_history: list[dict[str, Any]] | None = None,
        structured_output_collected: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> bool:
        """Snapshot at tool-call result-arrival boundary.

        Note: tool-resolve snapshots bypass the rate cap. Even if
        another snapshot fired within ``min_interval_seconds``,
        the resolved state is structurally important (the in-flight
        tool transitions to "done"), so the replay timeline must see
        the boundary.
        """
        snap = StateSnapshot(
            system_prompt=system_prompt,
            message_history=message_history or [],
            tool_call_in_flight={
                "name": tool_name,
                "args": tool_args or {},
                "result": result,
            },
            structured_output_collected=structured_output_collected,
        )
        return await self._emit(snap, session_id, bypass_rate_cap=True)

    # ---- lifecycle helpers -----------------------------------------------

    def reset_session(self, session_id: str) -> None:
        """Drop the last-snapshot timestamp for a session.

        Called by ReplayCapture's session lifecycle (or test fixtures);
        a fresh session should not inherit the previous one's
        rate-cap state.
        """
        self._last_snapshot_ms.pop(session_id, None)

    # ---- internals -------------------------------------------------------

    async def _emit(
        self,
        snapshot: StateSnapshot,
        session_id: str | None,
        bypass_rate_cap: bool = False,
    ) -> bool:
        sid = self._resolve_session_id(session_id)
        if sid is None:
            logger.debug(
                "StateSnapshotter._emit without session_id; dropping",
            )
            return False
        now_ms = int(time.monotonic() * 1000)
        if not bypass_rate_cap:
            last_ms = self._last_snapshot_ms.get(sid)
            if last_ms is not None and (now_ms - last_ms) < self._min_interval * 1000:
                return False
        self._last_snapshot_ms[sid] = now_ms
        try:
            await self._on_snapshot(snapshot.model_dump())
        except Exception:
            logger.exception(
                "StateSnapshotter on_snapshot raised; snapshot dropped",
            )
            # Do not re-raise: snapshots are best-effort observability.
            return False
        return True

    @staticmethod
    def _resolve_session_id(session_id: str | None) -> str | None:
        return session_id if session_id is not None else get_session_id()


__all__ = [
    "SnapshotCallback",
    "StateSnapshot",
    "StateSnapshotter",
]
