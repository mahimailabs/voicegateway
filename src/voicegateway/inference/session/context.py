"""ContextVar-backed session correlation for the voicegateway.inference module."""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Any

_SESSION_ID_PREFIX = "vg-"

#: The sentinel tenant identifier used when no tenant is specified.
DEFAULT_TENANT: str = "default"

_current_session_id: ContextVar[str | None] = ContextVar("vg_session_id", default=None)
_current_guardrails_bypassed: ContextVar[bool] = ContextVar(
    "vg_guardrails_bypassed", default=False
)
_current_guardrail_policy_snapshot: ContextVar[dict[str, Any] | None] = ContextVar(
    "vg_guardrail_policy_snapshot", default=None
)
_current_guardrail_bypass_logged: ContextVar[bool] = ContextVar(
    "vg_guardrail_bypass_logged", default=False
)


def get_or_create_session_id() -> str:
    """Return the current session ID, creating one if absent."""
    sid = _current_session_id.get()
    if sid is None:
        sid = f"{_SESSION_ID_PREFIX}{uuid.uuid4()}"
        _current_session_id.set(sid)
    return sid


def get_session_id() -> str | None:
    """Return the current session ID without creating one."""
    return _current_session_id.get()


def start_session(*, bypass_guardrails: bool = False) -> str:
    """Force a new session id for the current context."""
    sid = f"{_SESSION_ID_PREFIX}{uuid.uuid4()}"
    _current_session_id.set(sid)
    _current_guardrails_bypassed.set(bool(bypass_guardrails))
    _current_guardrail_policy_snapshot.set(None)
    _current_guardrail_bypass_logged.set(False)
    return sid


def reset_session_id() -> None:
    """Clear the session ID in the current context."""
    _current_session_id.set(None)
    _current_guardrails_bypassed.set(False)
    _current_guardrail_policy_snapshot.set(None)
    _current_guardrail_bypass_logged.set(False)


def set_guardrails_bypassed(bypass: bool) -> None:
    """Set the per-session guardrail bypass flag for the active context."""
    _current_guardrails_bypassed.set(bool(bypass))


def current_guardrails_bypassed() -> bool:
    """Return whether the current session opted out of guardrail injection."""
    return _current_guardrails_bypassed.get()


def get_or_freeze_guardrail_policy_snapshot(
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Freeze the guardrail policy for the current session."""
    snapshot = _current_guardrail_policy_snapshot.get()
    if snapshot is None:
        snapshot = dict(policy)
        if isinstance(policy.get("categories"), dict):
            snapshot["categories"] = dict(policy["categories"])
        _current_guardrail_policy_snapshot.set(snapshot)
    return snapshot


def current_guardrail_policy_snapshot() -> dict[str, Any] | None:
    """Return the frozen policy snapshot, if the session has reached LLM."""
    snapshot = _current_guardrail_policy_snapshot.get()
    if snapshot is None:
        return None
    out = dict(snapshot)
    if isinstance(snapshot.get("categories"), dict):
        out["categories"] = dict(snapshot["categories"])
    return out


def guardrail_bypass_already_logged() -> bool:
    """Return whether the bypass audit event has been recorded."""
    return _current_guardrail_bypass_logged.get()


def mark_guardrail_bypass_logged() -> None:
    """Mark the current session's bypass audit event as written/scheduled."""
    _current_guardrail_bypass_logged.set(True)


_TENANT_ID_MAX_LEN = 128

_current_tenant_id: ContextVar[str | None] = ContextVar("vg_tenant_id", default=None)


def set_tenant(tenant_id: str | None) -> None:
    """Set the current tenant identifier in the active context."""
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
    """Return the current tenant identifier, or ``None`` when unset."""
    return _current_tenant_id.get()


def reset_tenant_id() -> None:
    """Clear the tenant identifier in the current context."""
    _current_tenant_id.set(None)


# Tuple layout: (stt, llm, tts, budget_ms, budget_overrun).
RoutingDecisionTuple = tuple[str, str, str, int, bool]

_current_routing_decision: ContextVar[RoutingDecisionTuple | None] = ContextVar(
    "vg_routing_decision", default=None
)


def set_routing_decision(decision: RoutingDecisionTuple | None) -> None:
    """Set the current session's routing decision in the active context."""
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
