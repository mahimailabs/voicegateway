"""Versioned immutable-pricing and durable usage-accounting API."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from voicegateway.accounting.contracts import (
    AccountingCapabilities,
    OwnershipAssignment,
    PreparationRequest,
    PricingBinding,
    PricingRevisionCreate,
    PricingSide,
    UsageBatchResponse,
    UsageEnvelope,
)
from voicegateway.repository.request_log_repository import log_audit_event
from voicegateway.server.api._deps import (
    Principal,
    get_session,
    require_ingest_principal,
    require_principal,
)
from voicegateway.services.accounting_service import AccountingService, RevisionConflict

router = APIRouter(prefix="/accounting", tags=["accounting"])


class ActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: str
    expected_current_revision_id: str | None = None


def _tenant(principal: Principal, requested: str | None = None) -> str:
    if principal.is_admin:
        return requested or ""
    if requested is not None and requested != principal.tenant_id:
        raise HTTPException(status_code=403, detail="tenant_not_authorized")
    return principal.tenant_id or ""


def _project(principal: Principal, project_id: str) -> None:
    if principal.project_ids is not None and project_id not in principal.project_ids:
        raise HTTPException(status_code=403, detail="project_not_authorized")


def _revision_response(row: object, *, include_content: bool) -> dict[str, object]:
    result: dict[str, object] = {
        "revision_id": row.revision_id,
        "side": row.side,
        "content_hash": row.content_hash,
        "contract_version": row.contract_version,
        "currency": row.currency,
        "active": row.active,
    }
    if include_content:
        result["content"] = json.loads(row.content_json)
    return result


@router.get("/capabilities", response_model=AccountingCapabilities)
async def capabilities(
    _principal: Principal = Depends(require_principal),
) -> AccountingCapabilities:
    return AccountingCapabilities()


@router.post("/revisions", status_code=201)
async def create_revision(
    payload: PricingRevisionCreate,
    tenant: str | None = Query(None),
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="operator_required")
    target_tenant = _tenant(principal, tenant or payload.scope.tenant_id)
    if payload.scope.tenant_id is not None and payload.scope.tenant_id != target_tenant:
        raise HTTPException(status_code=422, detail="scope_tenant_mismatch")
    service = AccountingService(session, tenant_id=target_tenant)
    try:
        row, created = await service.create_revision(payload)
    except RevisionConflict as exc:
        raise HTTPException(
            status_code=409, detail="revision_content_conflict"
        ) from exc
    result = _revision_response(row, include_content=True)
    result["created"] = created
    await log_audit_event(
        session,
        "pricing_revision",
        payload.revision_id,
        "create" if created else "idempotent_create",
        {"side": payload.side.value, "content_hash": row.content_hash},
        "accounting_api",
    )
    return result


@router.get("/revisions/{side}/{revision_id}")
async def get_revision(
    side: PricingSide,
    revision_id: str,
    tenant: str | None = Query(None),
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    if side is PricingSide.ACQUISITION and not principal.is_admin:
        raise HTTPException(status_code=403, detail="operator_required")
    service = AccountingService(session, tenant_id=_tenant(principal, tenant))
    row = await service.get_revision(side, revision_id)
    if row is None:
        raise HTTPException(status_code=404, detail="revision_not_found")
    if side is PricingSide.ACQUISITION:
        await log_audit_event(
            session,
            "pricing_revision",
            revision_id,
            "read_acquisition",
            {"tenant": _tenant(principal, tenant)},
            "accounting_api",
        )
    return _revision_response(row, include_content=principal.is_admin)


@router.post("/revisions/{side}/activate")
async def activate_revision(
    side: PricingSide,
    payload: ActivationRequest,
    tenant: str | None = Query(None),
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="operator_required")
    service = AccountingService(session, tenant_id=_tenant(principal, tenant))
    try:
        row = await service.activate_revision(
            side,
            payload.revision_id,
            expected_current_revision_id=payload.expected_current_revision_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="revision_not_found") from exc
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail="revision_hash_mismatch") from exc
    await log_audit_event(
        session,
        "pricing_revision",
        payload.revision_id,
        "activate",
        {"side": side.value},
        "accounting_api",
    )
    return _revision_response(row, include_content=True)


@router.put("/ownership")
async def set_ownership(
    payload: OwnershipAssignment,
    tenant: str | None = Query(None),
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="operator_required")
    row = await AccountingService(
        session, tenant_id=_tenant(principal, tenant)
    ).set_ownership(payload)
    return {"project_id": row.project_id, "component": row.component, "mode": row.mode}


@router.post("/prepare", response_model=PricingBinding)
async def prepare(
    payload: PreparationRequest,
    principal: Principal = Depends(require_ingest_principal),
    session: AsyncSession = Depends(get_session),
) -> PricingBinding:
    _project(principal, payload.project_id)
    return await AccountingService(session, tenant_id=_tenant(principal)).prepare(
        payload
    )


@router.post("/usage", response_model=UsageBatchResponse)
async def ingest_usage(
    payload: tuple[UsageEnvelope, ...],
    principal: Principal = Depends(require_ingest_principal),
    session: AsyncSession = Depends(get_session),
) -> UsageBatchResponse:
    if len(payload) > 1000:
        raise HTTPException(status_code=413, detail="batch_too_large")
    service = AccountingService(session, tenant_id=_tenant(principal))
    receipts = []
    for envelope in payload:
        _project(principal, envelope.project_id)
        receipts.append(await service.ingest(envelope))
    return UsageBatchResponse(receipts=tuple(receipts))


@router.get("/report")
async def report(
    project: str | None = Query(None),
    tenant: str | None = Query(None),
    group_by: list[str] = Query(default=[]),
    include_acquisition: bool = Query(False),
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    if project is not None:
        _project(principal, project)
    if include_acquisition and not principal.is_admin:
        raise HTTPException(status_code=403, detail="operator_required")
    if include_acquisition:
        await log_audit_event(
            session,
            "accounting_report",
            tenant or "all",
            "read_acquisition",
            {"project": project},
            "accounting_api",
        )
    try:
        return await AccountingService(
            session, tenant_id=_tenant(principal, tenant)
        ).report(
            project_id=project,
            allowed_project_ids=principal.project_ids,
            group_by=tuple(group_by),
            include_acquisition=include_acquisition,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="unsupported_group") from exc
