"""Optional bearer-token authentication for the HTTP API.

Mirrors the pattern used by the MCP transport (``voicegateway/mcp/auth.py``):
enabled only when tokens are configured — either in ``voicegw.yaml`` under
the ``auth.api_keys`` block or via the ``VOICEGW_API_KEY`` env var. When
neither is set, the gateway serves unauthenticated (preserves local dev
UX). Tokens are compared in constant time.

The HTTP API wires ``require_scope("write")`` as a FastAPI dependency onto
mutating endpoints; the MCP server keeps its own auth module and is
untouched.
"""

from __future__ import annotations

import hmac
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voicegateway.core.config import AuthConfig

logger = logging.getLogger(__name__)


_ENV_KEY = "VOICEGW_API_KEY"
_WILDCARD_SCOPE = "*"


class AuthError(Exception):
    """Raised when a request is missing a token or the token is invalid.

    ``status_code`` is 401 for missing/invalid credentials and 403 for a
    valid token that lacks the required scope.
    """

    def __init__(self, message: str, status_code: int = 401):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class ApiKey:
    """A single configured API key with a scope allowlist."""

    token: str
    name: str = ""
    scopes: tuple[str, ...] = field(default_factory=lambda: (_WILDCARD_SCOPE,))

    def has_scope(self, required: str) -> bool:
        return _WILDCARD_SCOPE in self.scopes or required in self.scopes


def load_api_keys(auth_config: AuthConfig | None) -> list[ApiKey]:
    """Build the ApiKey list from config + env fallback.

    Entries whose token is empty (e.g. ``${VOICEGW_API_KEY}`` when the env
    var is unset) are skipped — this matches how the rest of the config
    treats unset substitutions.

    If no keys are configured but ``VOICEGW_API_KEY`` is set, synthesize a
    single wildcard-scope key named "env". This is the one-liner UX for
    Docker-style deployments.
    """
    keys: list[ApiKey] = []
    entries = list(auth_config.api_keys) if auth_config is not None else []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        token = str(entry.get("token") or "").strip()
        if not token:
            continue
        name = str(entry.get("name") or "")
        raw_scopes = entry.get("scopes") or [_WILDCARD_SCOPE]
        # Guard against a YAML scalar (``scopes: "write"``) that would
        # otherwise iterate as characters. Pydantic validation rejects
        # this, but the dataclass path doesn't go through Pydantic.
        if isinstance(raw_scopes, (str, bytes)):
            raw_scopes = [raw_scopes]
        scopes = tuple(str(s) for s in raw_scopes if str(s))
        if not scopes:
            scopes = (_WILDCARD_SCOPE,)
        keys.append(ApiKey(token=token, name=name, scopes=scopes))

    if not keys:
        env_token = os.environ.get(_ENV_KEY, "").strip()
        if env_token:
            keys.append(
                ApiKey(token=env_token, name="env", scopes=(_WILDCARD_SCOPE,))
            )

    return keys


def _extract_bearer(authorization: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer …`` header.

    Scheme matching is case-insensitive (RFC 7235 §2.1). Returns
    ``None`` when the header is missing, uses a different scheme, or
    has an empty token.
    """
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


def check_request(
    authorization: str | None,
    required_scope: str,
    keys: list[ApiKey],
) -> ApiKey | None:
    """Validate a request's ``Authorization`` header.

    - Returns ``None`` when no keys are configured (auth disabled).
    - Returns the matching ``ApiKey`` on success.
    - Raises ``AuthError(401)`` if the header is missing, malformed, or no
      configured token matches.
    - Raises ``AuthError(403)`` if a token matches but lacks the required
      scope.

    Token comparison uses ``hmac.compare_digest`` to avoid timing leaks.
    The scope check runs only after a matching token is found, so a wrong
    token never reveals whether the right token would have had the right
    scope.
    """
    if not keys:
        return None

    provided = _extract_bearer(authorization)
    if provided is None:
        raise AuthError("Missing bearer token", status_code=401)

    matched: ApiKey | None = None
    # Walk the whole list so timing is independent of list position.
    for key in keys:
        if hmac.compare_digest(provided, key.token):
            matched = key
            # Keep iterating to stabilize timing.

    if matched is None:
        raise AuthError("Invalid token", status_code=401)

    if not matched.has_scope(required_scope):
        raise AuthError(
            f"Token missing required scope: {required_scope}",
            status_code=403,
        )

    return matched


def resolve_cors_origins(auth_config: AuthConfig | None) -> list[str]:
    """Return the CORS allow-list. Empty config falls back to ``["*"]``.

    Blank/falsey entries are filtered out; if that leaves no origins at
    all, fall back to ``["*"]`` too — otherwise CORS would reject every
    browser request, which is almost certainly not what the operator
    intended. Callers should log a warning when this fallback applies.
    """
    if auth_config is None or not auth_config.cors_origins:
        return ["*"]
    filtered = [str(o) for o in auth_config.cors_origins if str(o)]
    return filtered if filtered else ["*"]


def describe_auth(keys: list[ApiKey]) -> str:
    """One-line human description for startup logs."""
    if not keys:
        return (
            "auth: disabled (set auth.api_keys in voicegw.yaml or "
            f"{_ENV_KEY} env var to enable)"
        )
    return f"auth: enabled ({len(keys)} key(s) configured)"


# Re-export so callers don't need to import __all__ separately.
__all__ = [
    "ApiKey",
    "AuthError",
    "check_request",
    "describe_auth",
    "load_api_keys",
    "resolve_cors_origins",
]
