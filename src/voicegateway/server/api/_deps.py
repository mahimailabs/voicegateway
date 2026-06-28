"""Shared FastAPI dependencies for every router under server/api/.

Two top-level helpers:

- :func:`get_gateway` reads ``request.app.state.gateway`` and is the
  single way every endpoint reaches the Gateway. ``main.py`` is
  responsible for setting that attribute exactly once during
  ``build_app``.
- :func:`require_scope` returns a dependency that enforces a named
  scope on the incoming request. Identical behavior to the closure
  it replaces in ``main.py``: api-key tokens take the storage path,
  static API keys take the ``check_request`` path.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from fastapi import Depends, HTTPException, Request

from voicegateway.core.auth import (
    ADMIN_SCOPE,
    AuthError,
    check_request,
    is_api_key_token,
    verify_api_key,
)
from voicegateway.inference.session.context import set_tenant
from voicegateway.repository import api_keys_repository as api_keys_repo

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway
    from voicegateway.repository.api_keys_repository import VerifiedKey


def get_gateway(request: Request) -> Gateway:
    """Return the Gateway bound to the running app."""
    gw = getattr(request.app.state, "gateway", None)
    if gw is None:
        raise HTTPException(status_code=503, detail="Gateway not bound to app state")
    return cast("Gateway", gw)


async def _verify_vk_key(
    request: Request,
    gateway: Gateway,
    authorize: Callable[[VerifiedKey], Awaitable[None] | None] | None = None,
) -> VerifiedKey:
    """Verify a ``vk_`` token, run ``authorize``, mark it used, stamp state.

    Shared by :func:`require_scope` and :func:`require_principal` so the
    vk_-verification path is defined once. ``authorize`` (if given) runs
    AFTER verification but BEFORE ``mark_used``, so a denied key never has
    its ``last_used_at`` bumped. It may raise :class:`AuthError`. On success
    the verified identity is written to ``request.state`` and the tenant is
    stamped into the request-scoped context.
    """
    authorization = request.headers.get("Authorization")
    try:
        await gateway.storage._ensure_initialized()
        async with gateway.storage._conn.session() as session:
            verified = await verify_api_key(authorization, session)
        if authorize is not None:
            result = authorize(verified)
            if result is not None:
                await result
        async with gateway.storage._conn.session() as session:
            await api_keys_repo.mark_used(session, verified.id)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    request.state.api_key_id = verified.id
    request.state.api_key_tenant_id = verified.tenant_id
    request.state.api_key_role = verified.role
    if verified.tenant_id is not None:
        set_tenant(verified.tenant_id)
    return verified


def require_scope(scope: str):
    """Return a FastAPI dependency enforcing ``scope`` on a request."""

    async def _dep(request: Request) -> None:
        gateway: Gateway = get_gateway(request)
        api_keys = getattr(request.app.state, "api_keys", None) or []
        authorization = request.headers.get("Authorization")

        if is_api_key_token(authorization) and gateway.storage is not None:

            def _authorize(verified: VerifiedKey) -> None:
                # Authorization checks run BEFORE mark_used so a denied key
                # does not get its last_used_at bumped.
                if scope == ADMIN_SCOPE and verified.role != ADMIN_SCOPE:
                    raise AuthError(
                        f"Token role {verified.role!r} cannot access admin scope",
                        status_code=403,
                    )
                if not verified.has_scope(scope):
                    raise AuthError(
                        f"Token missing required scope: {scope}",
                        status_code=403,
                    )

            await _verify_vk_key(request, gateway, _authorize)
            return

        try:
            check_request(authorization, scope, api_keys)
        except AuthError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.message
            ) from None

    return _dep


# Read scope used by the dashboard read endpoints. There is no per-endpoint
# scope today, so "read" matches the always-allowed case when no static keys
# are configured (check_request returns None) and a wildcard vk_ key passes.
READ_SCOPE = "read"


@dataclass(frozen=True)
class Principal:
    """The authenticated identity behind a read request.

    - ``tenant_id``: the tenant this caller is bound to, or ``None`` for an
      operator/admin who sees every tenant.
    - ``is_admin``: True for an admin vk_ key (role == "admin") or the soft
      operator default (no credential / static config key).
    - ``api_key_id``: the vk_ key id when one authenticated, else ``None``.
    """

    tenant_id: str | None
    is_admin: bool
    api_key_id: int | None


# The soft default for the self-hosted operator: no credential (or a static
# config key) yields an admin principal with no tenant binding, so the
# operator who controls the config keeps seeing everything, exactly as before.
_OPERATOR = Principal(tenant_id=None, is_admin=True, api_key_id=None)


async def require_principal(request: Request) -> Principal:
    """Resolve the read :class:`Principal` for a dashboard request.

    Mirrors :func:`require_scope`'s two-branch shape so behavior is identical
    for the no-credential and static-key cases:

    - **vk_ token + storage present:** verify the key and build a Principal
      from it. A ``role == "admin"`` key is admin (its ``tenant_id`` may be
      None = sees all). A ``role == "tenant"`` key is non-admin, scoped to
      its ``tenant_id``.
    - **else (static config key or no credential):** run the same
      ``check_request`` gate (so a request still 401s when static keys ARE
      configured and stays open when none are) and return the soft operator
      principal.
    """
    gateway: Gateway = get_gateway(request)
    api_keys = getattr(request.app.state, "api_keys", None) or []
    authorization = request.headers.get("Authorization")

    if is_api_key_token(authorization) and gateway.storage is not None:
        verified = await _verify_vk_key(request, gateway)
        return Principal(
            tenant_id=verified.tenant_id,
            is_admin=(verified.role == ADMIN_SCOPE),
            api_key_id=verified.id,
        )

    try:
        check_request(authorization, READ_SCOPE, api_keys)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    return _OPERATOR


def resolve_read_tenant(principal: Principal, requested: str | None) -> str | None:
    """Derive the tenant a read may touch from the authenticated principal.

    - **admin:** return ``requested`` (filter to one tenant) or ``None`` (all
      tenants). Admin keeps the ability to filter by tenant.
    - **non-admin:** a foreign ``requested`` value is refused with 403. On
      omission, scope to the principal's own tenant. The ``or ""`` guard means
      a non-admin key with a NULL tenant_id scopes to the unattributed bucket
      (""), NEVER to None/all-tenants. A non-admin must never resolve to None.
    """
    if principal.is_admin:
        return requested
    if requested is not None and requested != principal.tenant_id:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to read another tenant's data",
        )
    return principal.tenant_id or ""


__all__ = [
    "Depends",
    "Principal",
    "get_gateway",
    "require_principal",
    "require_scope",
    "resolve_read_tenant",
]
