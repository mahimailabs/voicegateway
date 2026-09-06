"""Read-only exact-accounting status tool."""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, ConfigDict

from voicegateway.core.auth import ADMIN_SCOPE
from voicegateway.server.mcp.tools.base import ToolDef, make_tool
from voicegateway.services.accounting_service import AccountingService


class GetAccountingStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: str | None = None


async def _handle_get_accounting_status(
    gateway: Any, arguments: dict[str, Any]
) -> dict[str, object]:
    payload = GetAccountingStatusInput.model_validate(arguments)
    tenant = os.environ.get("VOICEGW_MCP_ACCOUNTING_TENANT", "").strip()
    if not tenant:
        return {
            "error": "accounting_tenant_not_configured",
            "message": (
                "Set VOICEGW_MCP_ACCOUNTING_TENANT on the MCP server to bind "
                "accounting reads to one tenant."
            ),
        }
    if gateway.storage is None:
        return {
            "selling_total_usd": "0",
            "incomplete_selling_total_usd": "0",
            "unrated_selling_total_usd": "0",
            "records": 0,
            "counts": {
                "rated": 0,
                "unrated": 0,
                "incomplete": 0,
                "rejected": 0,
            },
        }
    async with gateway.storage.session() as session:
        return await AccountingService(session, tenant_id=tenant).report(
            project_id=payload.project
        )


GET_ACCOUNTING_STATUS_DOC = """Return exact-accounting selling totals and health.

This read-only operator tool is bound to the tenant configured by
VOICEGW_MCP_ACCOUNTING_TENANT and reports rated, unrated and incomplete counts.
It never returns acquisition rates, acquisition totals, margins, raw
usage envelopes, prompts, transcripts, or tool arguments.
"""

ACCOUNTING_TOOLS: list[ToolDef] = [
    make_tool(
        "get_accounting_status",
        GET_ACCOUNTING_STATUS_DOC,
        GetAccountingStatusInput,
        _handle_get_accounting_status,
        required_scope=ADMIN_SCOPE,
    )
]
