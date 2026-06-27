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

from typing import TYPE_CHECKING, cast

from fastapi import Depends, HTTPException, Request

from voicegateway.core.auth import (
    AuthError,
    check_request,
    is_api_key_token,
    verify_api_key,
)
from voicegateway.inference.session.context import set_tenant
from voicegateway.repository import api_keys_repository as api_keys_repo

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway


def get_gateway(request: Request) -> Gateway:
    """Return the Gateway bound to the running app."""
    gw = getattr(request.app.state, "gateway", None)
    if gw is None:
        raise HTTPException(status_code=503, detail="Gateway not bound to app state")
    return cast("Gateway", gw)


def require_scope(scope: str):
    """Return a FastAPI dependency enforcing ``scope`` on a request."""

    async def _dep(request: Request) -> None:
        gateway: Gateway = get_gateway(request)
        api_keys = getattr(request.app.state, "api_keys", None) or []
        authorization = request.headers.get("Authorization")

        if is_api_key_token(authorization) and gateway.storage is not None:
            try:
                await gateway.storage._ensure_initialized()
                async with gateway.storage._conn.session() as session:
                    verified = await verify_api_key(authorization, session)
                    await api_keys_repo.mark_used(session, verified.id)
                if scope == "admin" and verified.role != "admin":
                    raise AuthError(
                        f"Token role {verified.role!r} cannot access admin scope",
                        status_code=403,
                    )
                if not verified.has_scope(scope):
                    raise AuthError(
                        f"Token missing required scope: {scope}",
                        status_code=403,
                    )
            except AuthError as exc:
                raise HTTPException(
                    status_code=exc.status_code, detail=exc.message
                ) from None
            request.state.api_key_id = verified.id
            request.state.api_key_tenant_id = verified.tenant_id
            request.state.api_key_role = verified.role
            if verified.tenant_id is not None:
                set_tenant(verified.tenant_id)
            return

        try:
            check_request(authorization, scope, api_keys)
        except AuthError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.message
            ) from None

    return _dep


__all__ = ["Depends", "get_gateway", "require_scope"]
