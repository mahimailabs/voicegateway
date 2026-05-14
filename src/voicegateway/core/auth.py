"""Optional bearer-token authentication for the HTTP API."""

from __future__ import annotations

import hmac
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from voicegateway.core.config import AuthConfig
    from voicegateway.repository.virtual_keys_repository import VerifiedKey


logger = logging.getLogger(__name__)

VIRTUAL_KEY_PREFIX = "vk_"
ENV_KEY = "VOICEGW_API_KEY"
WILDCARD_SCOPE = "*"


class AuthError(Exception):
    """Raised when a request is missing a token or the token is invalid."""

    def __init__(self, message: str, status_code: int = 401):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class ApiKey:
    """A single configured API key with a scope allowlist."""

    token: str
    name: str = ""
    scopes: tuple[str, ...] = field(default_factory=lambda: (WILDCARD_SCOPE,))

    def has_scope(self, required: str) -> bool:
        return WILDCARD_SCOPE in self.scopes or required in self.scopes


def load_api_keys(auth_config: AuthConfig | None) -> list[ApiKey]:
    """Build the ApiKey list from config + env fallback."""
    keys: list[ApiKey] = []
    entries = list(auth_config.api_keys) if auth_config is not None else []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        token = str(entry.get("token") or "").strip()
        if not token:
            continue
        name = str(entry.get("name") or "")
        raw_scopes = entry.get("scopes") or [WILDCARD_SCOPE]

        if isinstance(raw_scopes, (str, bytes)):
            raw_scopes = [raw_scopes]
        scopes = tuple(str(s) for s in raw_scopes if str(s))
        if not scopes:
            scopes = (WILDCARD_SCOPE,)
        keys.append(ApiKey(token=token, name=name, scopes=scopes))

    if not keys:
        env_token = os.environ.get(ENV_KEY, "").strip()
        if env_token:
            keys.append(ApiKey(token=env_token, name="env", scopes=(WILDCARD_SCOPE,)))

    return keys


def _extract_bearer(authorization: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer …`` header."""
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
    """Validate a request's ``Authorization`` header."""
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
    """Return the CORS allow-list. Empty config falls back to ``["*"]``."""
    if auth_config is None or not auth_config.cors_origins:
        return ["*"]
    filtered = [str(o) for o in auth_config.cors_origins if str(o)]
    return filtered if filtered else ["*"]


def describe_auth(keys: list[ApiKey]) -> str:
    """One-line human description for startup logs."""
    if not keys:
        return (
            "auth: disabled (set auth.api_keys in voicegw.yaml or "
            f"{ENV_KEY} env var to enable)"
        )
    return f"auth: enabled ({len(keys)} key(s) configured)"


def is_virtual_key_token(authorization: str | None) -> bool:
    """Return True if the bearer token uses the ``vk_`` prefix."""
    token = _extract_bearer(authorization)
    return token is not None and token.startswith(VIRTUAL_KEY_PREFIX)


async def verify_virtual_key(
    authorization: str | None, session: AsyncSession
) -> VerifiedKey:
    """Validate a ``Bearer vk_…`` header against ``virtual_keys``."""
    from voicegateway.repository import virtual_keys_repository as virtual_keys

    token = _extract_bearer(authorization)
    if token is None:
        raise AuthError("Missing bearer token", status_code=401)
    if not token.startswith(VIRTUAL_KEY_PREFIX):
        # Caller should have routed to ``check_request`` for static keys.
        raise AuthError("Invalid virtual key", status_code=401)
    verified = await virtual_keys.verify(session, token)
    if verified is None:
        raise AuthError("Invalid virtual key", status_code=401)
    return verified


def check_tenant_body_conflict(
    *, key_tenant_id: str | None, body_tenant_id: str | None
) -> None:
    """Reject when the body's tenant_id contradicts the key's scope."""
    if key_tenant_id is None or body_tenant_id is None:
        return
    if key_tenant_id == body_tenant_id:
        return
    raise AuthError(
        (
            "Virtual key is scoped to tenant "
            f"{key_tenant_id!r} but request body declared tenant "
            f"{body_tenant_id!r}"
        ),
        status_code=403,
    )


__all__ = [
    "ApiKey",
    "AuthError",
    "check_request",
    "check_tenant_body_conflict",
    "describe_auth",
    "is_virtual_key_token",
    "load_api_keys",
    "resolve_cors_origins",
    "verify_virtual_key",
]
