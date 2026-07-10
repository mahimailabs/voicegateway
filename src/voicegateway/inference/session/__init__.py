"""Session correlation: ContextVars and LiveKit AgentSession attach helpers."""

from voicegateway.inference.session.attach import (
    attach,
    attach_session,
    register_components,
    reset_components,
)
from voicegateway.inference.session.context import (
    DEFAULT_TENANT,
    current_routing_decision,
    current_tenant,
    get_or_create_session_id,
    get_session_id,
    reset_routing_decision,
    reset_session_id,
    reset_tenant_id,
    set_routing_decision,
    set_tenant,
    start_session,
)

__all__ = [
    "DEFAULT_TENANT",
    "attach",
    "attach_session",
    "current_routing_decision",
    "current_tenant",
    "get_or_create_session_id",
    "get_session_id",
    "register_components",
    "reset_components",
    "reset_routing_decision",
    "reset_session_id",
    "reset_tenant_id",
    "set_routing_decision",
    "set_tenant",
    "start_session",
]
