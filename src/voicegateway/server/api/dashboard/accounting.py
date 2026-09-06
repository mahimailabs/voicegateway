"""Tenant-scoped exact-accounting status for the dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from voicegateway.server.api._deps import (
    Principal,
    get_session,
    require_principal,
    resolve_read_tenant,
)
from voicegateway.services.accounting_service import AccountingService

router = APIRouter(
    prefix="/accounting",
    tags=["dashboard-accounting"],
    dependencies=[Depends(require_principal)],
)


@router.get("")
async def accounting_status(
    project: str | None = Query(None),
    tenant: str | None = Query(None),
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    resolved = resolve_read_tenant(principal, tenant)
    if (
        project is not None
        and principal.project_ids is not None
        and project not in principal.project_ids
    ):
        raise HTTPException(status_code=403, detail="project_not_authorized")
    # The service intentionally exposes selling totals and completeness only.
    # Acquisition and margin require an operator-specific endpoint.
    return await AccountingService(session, tenant_id=resolved or "").report(
        project_id=project,
        allowed_project_ids=principal.project_ids,
    )
