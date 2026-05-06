"""ContextVar-backed session correlation for the voicegateway.inference module.

When a user constructs `inference.STT(...)`, `inference.LLM(...)` and
`inference.TTS(...)` inside the same async context (the standard
`AgentSession` flow), all three factories call
``get_or_create_session_id()``: the first creates a fresh ``"vg-<uuid>"``
ID, the others inherit it. The result is that one logical voice session
sees one shared ``session_id`` across all three modalities, with no
user-facing parameter.

Limitations (documented for v0.0.5; revisited in v0.0.6+):
- ContextVars are per-task. If a user constructs the three factories in
  separate ``asyncio.Task`` instances spawned without copying context,
  each factory creates its own ID. This is the "different async
  contexts" gap called out in design.md sections 3.2 and 7.2.

Design source: `.agents/design.md` section 3.2.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar

_SESSION_ID_PREFIX = "vg-"

_current_session_id: ContextVar[str | None] = ContextVar(
    "vg_session_id", default=None
)


def get_or_create_session_id() -> str:
    """Return the current session ID, creating one if absent.

    Looks up ``_current_session_id`` in the active asyncio context. If
    the value is ``None`` (default), generate a new ID prefixed with
    ``"vg-"`` and store it in the context so subsequent calls in the
    same context return the same ID.

    Returns:
        A non-empty string of the form ``"vg-<uuid4>"``.
    """
    sid = _current_session_id.get()
    if sid is None:
        sid = f"{_SESSION_ID_PREFIX}{uuid.uuid4()}"
        _current_session_id.set(sid)
    return sid


def get_session_id() -> str | None:
    """Return the current session ID without creating one.

    Useful for read-only call sites (cost trackers, request loggers) that
    should tag the request when a session exists but should not implicitly
    open a session of their own.

    Returns:
        The current session ID, or ``None`` if no session is active in
        the current context.
    """
    return _current_session_id.get()


def reset_session_id() -> None:
    """Clear the session ID in the current context.

    Primarily for tests that need a clean ContextVar state without
    spinning up a new asyncio task. Production code should not call
    this; rely on context isolation instead.
    """
    _current_session_id.set(None)
