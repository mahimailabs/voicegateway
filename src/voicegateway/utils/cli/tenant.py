"""Helpers for ``voicegateway.cli.tenant``."""

from __future__ import annotations

from typing import Any


def _format_relative(iso: str | None) -> str:
    """Return ``iso`` verbatim or ``-`` when ``None``."""
    return iso if iso else "-"


async def _list_tenants_async(
    storage: Any, *, limit: int, query: str | None
) -> tuple[list[Any], Any]:
    """Run ``tenants_repo.list_tenants`` + the unattributed aggregator."""
    from voicegateway.storage import tenants_repo

    db = await storage._ensure_initialized()
    rows = await tenants_repo.list_tenants(db, limit=limit, query=query)
    unattributed = await tenants_repo.get_unattributed_aggregates(db)
    return rows, unattributed


async def _get_tenant_async(storage: Any, tenant_id: str) -> Any:
    from voicegateway.storage import tenants_repo

    db = await storage._ensure_initialized()
    return await tenants_repo.get_tenant(db, tenant_id)
