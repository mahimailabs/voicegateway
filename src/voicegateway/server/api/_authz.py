"""The one place the enforcement mode is consulted.

Every gate computes whether it *would* refuse a request, then asks
:func:`decide` what to do about that. Under ``warn`` the request proceeds and
one structured event names it; under ``enforce`` it is refused; under
``local_development`` it proceeds silently because that is what the operator
explicitly asked for.

No gate branches on the mode itself. That is what makes flipping 0.27.0 to
enforce a default change rather than a code change, and what stops one gate
quietly disagreeing with another about what warn means.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request

    from voicegateway.core.config import AuthConfig
    from voicegateway.schemas.telemetry.security_schema import PrincipalKind

_logger = logging.getLogger("voicegateway.auth")

#: Log event name. Operators grep for this during the warn release, so it is
#: a stable string and not an f-string that varies per call site.
WOULD_REFUSE_EVENT = "vg.auth.would_refuse"


class Decision(StrEnum):
    """What a gate should do, once the mode has been taken into account."""

    #: Proceed. Either nothing was wrong, or local development is on.
    ALLOW = "allow"
    #: Proceed, but this would be refused under enforce. Logged and counted.
    WARN = "warn"
    #: Refuse. The caller raises the appropriate 401 or 403.
    REFUSE = "refuse"


def decide(
    *,
    would_refuse: bool,
    reason: str,
    auth: AuthConfig,
    request: Request,
    principal_kind: PrincipalKind,
    key_id: int | None,
) -> Decision:
    """Turn a would-be refusal into the mode's decision, with side effects.

    ``reason`` is a short machine-ish phrase, never a message built from
    request content: this line goes to the operator's log, and an auth
    warning that echoes a payload is a new leak rather than a diagnostic.
    """
    if not would_refuse:
        return Decision.ALLOW
    if auth.local_development:
        return Decision.ALLOW
    if auth.enforcement == "enforce":
        return Decision.REFUSE

    state = request.app.state
    state.auth_would_refuse = getattr(state, "auth_would_refuse", 0) + 1
    _logger.warning(
        "%s route=%s %s reason=%r principal_kind=%s key_id=%s "
        "(served under auth.enforcement=warn; refused under enforce)",
        WOULD_REFUSE_EVENT,
        request.method,
        request.url.path,
        reason,
        principal_kind.value,
        key_id,
    )
    return Decision.WARN


__all__ = ["WOULD_REFUSE_EVENT", "Decision", "decide"]
