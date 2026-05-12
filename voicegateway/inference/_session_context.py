"""ContextVar-backed session correlation for the voicegateway.inference module.

When a user constructs `inference.STT(...)`, `inference.LLM(...)` and
`inference.TTS(...)` inside the same async context (the standard
`AgentSession` flow), all three factories call
``get_or_create_session_id()``: the first creates a fresh ``"vg-<uuid>"``
ID, the others inherit it. The result is that one logical voice session
sees one shared ``session_id`` across all three modalities, with no
user-facing parameter.

Worker patterns that handle multiple conversations sequentially in a
single asyncio task (rather than spawning one task per call) need to
explicitly roll a new session id between conversations — call
``inference.start_session()`` at the top of each conversation handler.
Without that, the second conversation reuses the first's id and the
``sessions`` table merges costs across what should be distinct sessions.

The standard livekit-agents worker spawns one task per call and so this
case does not apply; the call is only needed for non-standard worker
loops or scripted sequential drivers.

Design source: `.agents/design.md` section 3.2.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar

_SESSION_ID_PREFIX = "vg-"

_current_session_id: ContextVar[str | None] = ContextVar("vg_session_id", default=None)


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


def start_session() -> str:
    """Force a new session id for the current context.

    The next ``inference.STT/LLM/TTS`` factory call will see no
    pre-existing id and create a fresh ``"vg-<uuid>"`` via
    ``get_or_create_session_id``. This call also returns the new id so
    callers can log it without a second lookup.

    Use this in non-standard worker patterns where a single asyncio
    task handles multiple conversations sequentially. The standard
    livekit-agents worker spawns a fresh task per call, so the
    ContextVar starts clean and this call is unnecessary.
    """
    sid = f"{_SESSION_ID_PREFIX}{uuid.uuid4()}"
    _current_session_id.set(sid)
    return sid


def reset_session_id() -> None:
    """Clear the session ID in the current context.

    Primarily for tests that need a clean ContextVar state without
    spinning up a new asyncio task. Production code that needs a fresh
    session ID between conversations should call ``start_session()``
    instead — it returns the new id and avoids a None-then-create
    round-trip.
    """
    _current_session_id.set(None)


# ----------------------------------------------------------------------
# v0.4.0 tenant-id ContextVar (REQ-VG-TENANT-001).
#
# Parallel to the session_id ContextVar above. The auth middleware
# sets this when a tenant-scoped virtual key authenticates a request;
# the session-create path sets it when the caller passes an explicit
# tenant identifier. Storage repos read ``current_tenant()`` at write
# time and stamp the row's ``tenant_id`` column. NULL is treated as
# "unattributed" at the dashboard layer (OQ3: no backfill).
# ----------------------------------------------------------------------

_TENANT_ID_MAX_LEN = 128  # OQ2 lock: 128-char UTF-8 cap.

_current_tenant_id: ContextVar[str | None] = ContextVar("vg_tenant_id", default=None)


def set_tenant(tenant_id: str | None) -> None:
    """Set the current tenant identifier in the active context.

    ``None`` clears the value (back to the implicit "unattributed"
    bucket). Non-string and over-length identifiers raise ``ValueError``
    to surface misuse at the call site rather than at storage write
    time (OQ2: 128-char UTF-8 cap, otherwise no constraints).
    """
    if tenant_id is None:
        _current_tenant_id.set(None)
        return
    if not isinstance(tenant_id, str):
        raise ValueError(
            f"tenant_id must be a str or None, got {type(tenant_id).__name__}"
        )
    if len(tenant_id) > _TENANT_ID_MAX_LEN:
        raise ValueError(
            f"tenant_id length {len(tenant_id)} exceeds {_TENANT_ID_MAX_LEN} "
            f"char cap (OQ2 lock)"
        )
    _current_tenant_id.set(tenant_id)


def current_tenant() -> str | None:
    """Return the current tenant identifier, or ``None`` when unset.

    Storage repos call this at write time. NULL on the row signals
    "unattributed" to the dashboard's tenant filter (REQ-VG-TENANT-001
    AC-3).
    """
    return _current_tenant_id.get()


def reset_tenant_id() -> None:
    """Clear the tenant identifier in the current context.

    Test-only helper; production resets happen via the
    contextvars.Token returned by ``set_tenant`` or by spawning a
    fresh asyncio task. Mirrors the ``reset_session_id`` shape.
    """
    _current_tenant_id.set(None)


# ----------------------------------------------------------------------
# v0.5.0 routing-decision ContextVar (REQ-VG-ROUTE-001..002).
#
# Parallel to tenant_id_ctx above. The session-create path runs the
# router once (voicegateway.middleware.router.route_session), sets the
# resulting RoutedTriple via ``set_routing_decision``, and then the
# next ``log_request`` reads it on the sessions UPSERT to stamp
# routed_llm / routed_tts / budget_ms / budget_overrun. Pre-v0.5.0
# sessions and sessions where the router never ran keep NULL on the
# four columns and the dashboard renders "-" / "ok" accordingly.
#
# ``RoutedTriple`` is a 4-tuple (stt, llm, tts, budget_ms,
# budget_overrun); flattening to a Tuple avoids a circular import
# between this module and middleware/router.py. The caller passes
# the tuple shape directly.
# ----------------------------------------------------------------------

# Tuple layout: (stt, llm, tts, budget_ms, budget_overrun).
RoutingDecisionTuple = tuple[str, str, str, int, bool]

_current_routing_decision: ContextVar[RoutingDecisionTuple | None] = ContextVar(
    "vg_routing_decision", default=None
)


def set_routing_decision(decision: RoutingDecisionTuple | None) -> None:
    """Set the current session's routing decision in the active context.

    ``decision`` is ``(stt, llm, tts, budget_ms, budget_overrun)``.
    Pass ``None`` to clear. Storage's ``log_request`` reads
    :func:`current_routing_decision` at the sessions UPSERT and stamps
    the four routing columns from this tuple. The router writes once
    per session; per AC-5 the triple is immutable for the session’s
    lifetime.
    """
    if decision is None:
        _current_routing_decision.set(None)
        return
    if not isinstance(decision, tuple) or len(decision) != 5:
        raise ValueError(
            "routing decision must be a 5-tuple "
            "(stt, llm, tts, budget_ms, budget_overrun) or None"
        )
    stt, llm, tts, budget_ms, overrun = decision
    if not (
        isinstance(stt, str)
        and isinstance(llm, str)
        and isinstance(tts, str)
        and isinstance(budget_ms, int)
        and isinstance(overrun, bool)
    ):
        raise ValueError("routing decision tuple has wrong field types")
    _current_routing_decision.set((stt, llm, tts, budget_ms, overrun))


def current_routing_decision() -> RoutingDecisionTuple | None:
    """Return the current routing decision, or ``None`` when unset."""
    return _current_routing_decision.get()


def reset_routing_decision() -> None:
    """Clear the routing decision (test-only helper)."""
    _current_routing_decision.set(None)
