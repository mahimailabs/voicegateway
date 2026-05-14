"""Helpers for ``voicegateway.cli.tenant``."""

from __future__ import annotations

from typing import Any


def _format_relative(iso: str | None) -> str:
    """Return ``iso`` verbatim or ``-`` when ``None``."""
    return iso if iso else "-"


async def _list_tenants_async(
    storage: Any, *, limit: int, query: str | None
) -> tuple[list[Any], Any]:
    """Run ``tenants.list_tenants`` + the unattributed aggregator."""
    from voicegateway.repository import tenants_repository as tenants

    await storage._ensure_initialized()
    async with storage._conn.session() as s:
        rows = await tenants.list_tenants(s, limit=limit, query=query)
        unattributed = await tenants.get_unattributed_aggregates(s)
    return rows, unattributed


async def _get_tenant_async(storage: Any, tenant_id: str) -> Any:
    from voicegateway.repository import tenants_repository as tenants

    await storage._ensure_initialized()
    async with storage._conn.session() as s:
        return await tenants.get_tenant(s, tenant_id)
