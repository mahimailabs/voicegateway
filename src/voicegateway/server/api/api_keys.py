"""FastAPI router for api-key management.

Auth is declared ONCE, here, for every route on this router. A minted key
carries the wildcard scope (``api_keys_repository`` defaults ``scopes='*'``),
so an ungated mint hands the caller write access to every ``/v1`` endpoint.
``require_scope(ADMIN_SCOPE)`` is a no-op while no API keys are configured
(the self-hosted single-operator default: ``core.auth.check_request``
returns None on an empty key list), and enforces the admin scope once auth
is enabled. Declaring it on the router rather than per handler means a new
route cannot forget to opt in.
"""

from __future__ import annotations

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query

from voicegateway.core.auth import ADMIN_SCOPE
from voicegateway.core.container import Container
from voicegateway.core.exceptions import NotFoundError
from voicegateway.schemas.api.api_key_schema import (
    ApiKeyCreate,
    ApiKeyListResponse,
    ApiKeyResponse,
    CreatedApiKey,
)
from voicegateway.server.api._deps import require_scope
from voicegateway.services.api_key_service import ApiKeyService

router = APIRouter(
    prefix="/api-keys",
    tags=["api-keys"],
    dependencies=[Depends(require_scope(ADMIN_SCOPE))],
)


@router.post("", response_model=CreatedApiKey, status_code=201)
@inject
async def create_api_key(
    payload: ApiKeyCreate,
    service: ApiKeyService = Depends(Provide[Container.api_key_service]),
) -> CreatedApiKey:
    """Mint a new virtual key. The plaintext is returned exactly once."""
    created = await service.create_key(
        name=payload.name,
        tenant_id=payload.tenant_id,
        issued_by=payload.issued_by,
    )
    return CreatedApiKey(
        plaintext=created.plaintext,
        key=ApiKeyResponse.model_validate(created.row),
    )


@router.get("", response_model=ApiKeyListResponse)
@inject
async def list_api_keys(
    include_revoked: bool = Query(True),
    service: ApiKeyService = Depends(Provide[Container.api_key_service]),
) -> ApiKeyListResponse:
    """List every virtual key. The bcrypt hash is never exposed."""
    rows = await service.list_keys(include_revoked=include_revoked)
    items = [ApiKeyResponse.model_validate(r) for r in rows]
    return ApiKeyListResponse(items=items, total=len(items))


@router.get("/{key_id}", response_model=ApiKeyResponse)
@inject
async def get_api_key(
    key_id: int,
    service: ApiKeyService = Depends(Provide[Container.api_key_service]),
) -> ApiKeyResponse:
    """Fetch one key by id. 404 when missing."""
    row = await service.get_by_id(key_id)
    return ApiKeyResponse.model_validate(row)


@router.delete("/{key_id}", status_code=204)
@inject
async def revoke_api_key(
    key_id: int,
    service: ApiKeyService = Depends(Provide[Container.api_key_service]),
) -> None:
    """Soft-revoke a key. Idempotent: 204 whether or not the row was active."""
    try:
        await service.revoke(key_id)
    except NotFoundError:
        raise
    return None
