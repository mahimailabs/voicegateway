"""FastAPI router for virtual-key management."""

from __future__ import annotations

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query

from voicegateway.core.container import Container
from voicegateway.core.exceptions import NotFoundError
from voicegateway.schemas.api.virtual_key_schema import (
    CreatedVirtualKey,
    VirtualKeyCreate,
    VirtualKeyListResponse,
    VirtualKeyResponse,
)
from voicegateway.services.virtual_key_service import VirtualKeyService

router = APIRouter(prefix="/v1/virtual-keys", tags=["virtual-keys"])


@router.post("", response_model=CreatedVirtualKey, status_code=201)
@inject
async def create_virtual_key(
    payload: VirtualKeyCreate,
    service: VirtualKeyService = Depends(Provide[Container.virtual_key_service]),
) -> CreatedVirtualKey:
    """Mint a new virtual key. The plaintext is returned exactly once."""
    created = await service.create_key(
        name=payload.name,
        tenant_id=payload.tenant_id,
        issued_by=payload.issued_by,
    )
    return CreatedVirtualKey(
        plaintext=created.plaintext,
        key=VirtualKeyResponse.model_validate(created.row),
    )


@router.get("", response_model=VirtualKeyListResponse)
@inject
async def list_virtual_keys(
    include_revoked: bool = Query(True),
    service: VirtualKeyService = Depends(Provide[Container.virtual_key_service]),
) -> VirtualKeyListResponse:
    """List every virtual key. The bcrypt hash is never exposed."""
    rows = await service.list_keys(include_revoked=include_revoked)
    items = [VirtualKeyResponse.model_validate(r) for r in rows]
    return VirtualKeyListResponse(items=items, total=len(items))


@router.get("/{key_id}", response_model=VirtualKeyResponse)
@inject
async def get_virtual_key(
    key_id: int,
    service: VirtualKeyService = Depends(Provide[Container.virtual_key_service]),
) -> VirtualKeyResponse:
    """Fetch one key by id. 404 when missing."""
    row = await service.get_by_id(key_id)
    return VirtualKeyResponse.model_validate(row)


@router.delete("/{key_id}", status_code=204)
@inject
async def revoke_virtual_key(
    key_id: int,
    service: VirtualKeyService = Depends(Provide[Container.virtual_key_service]),
) -> None:
    """Soft-revoke a key. Idempotent: 204 whether or not the row was active."""
    try:
        await service.revoke(key_id)
    except NotFoundError:
        raise
    return None
