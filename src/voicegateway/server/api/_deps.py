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

import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from voicegateway.core import scopes
from voicegateway.core.auth import (
    ADMIN_SCOPE,
    AuthError,
    check_request,
    is_api_key_token,
    verify_api_key,
)
from voicegateway.inference.session.context import set_tenant
from voicegateway.repository import api_keys_repository as api_keys_repo
from voicegateway.schemas.telemetry.security_schema import PrincipalKind
from voicegateway.server.api._authz import Decision, decide

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway
    from voicegateway.repository.api_keys_repository import VerifiedKey


def get_gateway(request: Request) -> Gateway:
    """Return the Gateway bound to the running app."""
    gw = getattr(request.app.state, "gateway", None)
    if gw is None:
        raise HTTPException(status_code=503, detail="Gateway not bound to app state")
    return cast("Gateway", gw)


async def get_session(
    gateway: Gateway = Depends(get_gateway),
) -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session for the duration of one request.

    Delegates to :meth:`StorageService.session`, which is the only public
    path to a session and which runs migrations before yielding. Routes
    declare ``session: AsyncSession = Depends(get_session)`` and never touch
    storage internals, so there is exactly one place between a handler and
    the database for a tenant guard to sit.
    """
    storage = gateway.storage
    if storage is None:
        raise HTTPException(status_code=503, detail="cost tracking storage is disabled")
    async with storage.session() as session:
        yield session


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
    storage = gateway.storage
    if storage is None:  # pragma: no cover - callers guard storage is not None
        raise HTTPException(status_code=503, detail="cost tracking storage is disabled")
    try:
        async with storage.session() as session:
            verified = await verify_api_key(authorization, session)
        if authorize is not None:
            result = authorize(verified)
            if result is not None:
                await result
        async with storage.session() as session:
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
    """The authenticated identity behind a request.

    - ``tenant_id``: the tenant this caller is bound to, or ``None`` for an
      operator/admin who sees every tenant.
    - ``is_admin``: True for an admin vk_ key (role == "admin") or the soft
      operator default (no credential / static config key).
    - ``api_key_id``: the vk_ key id when one authenticated, else ``None``.
    - ``project_ids``: the projects this key may touch, or ``None`` for
      unrestricted within its tenant. Loaded from ``api_keys.project_ids``
      in Task 14; ``None`` until then, which is what every existing key gets.
    - ``kind``: which auth branch produced this principal. Carried so an
      audit record can say what authenticated, not just who.
    """

    tenant_id: str | None
    is_admin: bool
    api_key_id: int | None
    project_ids: frozenset[str] | None = None
    kind: PrincipalKind = PrincipalKind.OPERATOR


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
            kind=(
                PrincipalKind.ADMIN_KEY
                if verified.role == ADMIN_SCOPE
                else PrincipalKind.TENANT_KEY
            ),
        )

    try:
        check_request(authorization, READ_SCOPE, api_keys)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None

    if api_keys:
        # A configured static key matched, or check_request would have raised.
        # The operator who writes the config is the operator.
        return Principal(
            tenant_id=None,
            is_admin=True,
            api_key_id=None,
            kind=PrincipalKind.STATIC_KEY,
        )

    # No keys configured and no credential presented. This is VG-SEC-005: the
    # difference between a locked deployment and an open one used to be a
    # config block rather than a code path, and the code path silently handed
    # out a full admin.
    decision = decide(
        would_refuse=True,
        reason="no credential and no api_keys configured",
        auth=gateway.config.auth,
        request=request,
        principal_kind=PrincipalKind.OPERATOR,
        key_id=None,
    )
    if decision is Decision.REFUSE:
        raise HTTPException(status_code=401, detail="Authentication required")
    return _OPERATOR


async def require_ingest_principal(request: Request) -> Principal:
    """Resolve the principal for a telemetry write, requiring ``ingest``.

    Ingest is deliberately narrower than write (VG-SEC-003): an agent key
    that only posts telemetry must not also be able to rewrite provider,
    model and project configuration, which is what sharing one ``write``
    scope across both meant.

    Yields a :class:`Principal` rather than returning ``None`` like
    ``require_scope``, because the handler needs ``principal.tenant_id`` to
    stamp on every row it writes. That is the other half of VG-SEC-001: the
    tenant has to come from the credential, and the handler has to be handed
    it rather than going looking.
    """
    gateway: Gateway = get_gateway(request)
    auth_cfg = gateway.config.auth
    api_keys = getattr(request.app.state, "api_keys", None) or []
    authorization = request.headers.get("Authorization")
    enforce = auth_cfg.enforcement == "enforce"

    if is_api_key_token(authorization) and gateway.storage is not None:

        def _authorize(verified: VerifiedKey) -> None:
            if not verified.has_scope(scopes.INGEST, enforce=enforce):
                raise AuthError(
                    f"Token missing required scope: {scopes.INGEST}",
                    status_code=403,
                )
            granted = {s.strip() for s in verified.scopes.split(",") if s.strip()}
            if scopes.INGEST not in granted and scopes.WRITE in granted:
                _logger.warning(
                    "write scope used for ingest by key_id=%s on %s %s; mint an "
                    "ingest key before 0.27.0, when write stops covering it",
                    verified.id,
                    request.method,
                    request.url.path,
                )

        verified = await _verify_vk_key(request, gateway, _authorize)
        return Principal(
            tenant_id=verified.tenant_id,
            is_admin=(verified.role == ADMIN_SCOPE),
            api_key_id=verified.id,
            kind=(
                PrincipalKind.ADMIN_KEY
                if verified.role == ADMIN_SCOPE
                else PrincipalKind.TENANT_KEY
            ),
        )

    try:
        check_request(authorization, scopes.INGEST, api_keys)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None

    if api_keys:
        return Principal(
            tenant_id=None,
            is_admin=True,
            api_key_id=None,
            kind=PrincipalKind.STATIC_KEY,
        )

    decision = decide(
        would_refuse=True,
        reason=f"no credential for scope {scopes.INGEST}",
        auth=auth_cfg,
        request=request,
        principal_kind=PrincipalKind.OPERATOR,
        key_id=None,
    )
    if decision is Decision.REFUSE:
        raise HTTPException(status_code=401, detail="Authentication required")
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
    "get_session",
    "require_ingest_principal",
    "require_principal",
    "require_scope",
    "resolve_read_tenant",
]
