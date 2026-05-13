"""Conversation-state snapshots at LLM and tool-call boundaries."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Final

from voicegateway.inference.session.context import get_session_id
from voicegateway.schemas.state_snapshot_schema import StateSnapshot

logger = logging.getLogger(__name__)


_DEFAULT_MIN_INTERVAL_SECONDS: Final[float] = 1.0


SnapshotCallback = Callable[[dict[str, Any]], Awaitable[None]]


async def _noop_callback(snapshot: dict[str, Any]) -> None:
    """Default callback used when no ReplayCapture is wired yet."""
    logger.debug(
        "StateSnapshotter no-op callback: snapshot dropped (no capture wired)",
    )


class StateSnapshotter:
    """Captures conversation-state snapshots with a per-session rate cap."""

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

    async def on_message_added(
        self,
        *,
        system_prompt: str = "",
        message_history: list[dict[str, Any]] | None = None,
        session_id: str | None = None,
    ) -> bool:
        """Snapshot at LLM message-add boundary."""
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
        """Snapshot at tool-call result-arrival boundary."""
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

    def reset_session(self, session_id: str) -> None:
        """Drop the last-snapshot timestamp for a session."""
        self._last_snapshot_ms.pop(session_id, None)

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
