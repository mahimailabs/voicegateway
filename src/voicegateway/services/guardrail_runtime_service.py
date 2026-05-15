"""LLM-side guardrail runtime hooks (chat context surgery + tool plumbing).

Prompt loading, composition, and policy JSON serialization live on
:mod:`voicegateway.services.guardrail_service`. The hooks here run at
request time and bridge LiveKit chat context to that service.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from voicegateway.schemas.guardrail_policy_schema import (
    GUARDRAIL_CATEGORIES,
    REPORT_GUARDRAIL_TOOL_NAME,
)

if TYPE_CHECKING:
    from voicegateway.services.storage_service import StorageService


_MARKER = "<voicegateway_guardrails"
logger = logging.getLogger(__name__)


def inject_guardrail_block(chat_ctx: Any, block: str) -> Any:
    """Return a chat context copy with the guardrail block inserted."""
    if not block:
        return chat_ctx
    ctx = chat_ctx.copy() if hasattr(chat_ctx, "copy") else chat_ctx
    if isinstance(ctx, dict):
        items = list(ctx.get("items", []))
    else:
        items = list(getattr(ctx, "items", []))
    if any(_MARKER in _item_text(item) for item in items):
        return ctx

    message = _make_system_message(block)
    insert_at = 0
    for idx, item in enumerate(items):
        if _item_role(item) in {"system", "developer"}:
            insert_at = idx + 1
    items.insert(insert_at, message)
    if isinstance(ctx, dict):
        ctx["items"] = items
    elif hasattr(ctx, "items"):
        ctx.items = items
    return ctx


def tools_contain_reserved_report_tool(tools: Any) -> bool:
    """Return True when caller tools collide with the VG report tool."""
    if not tools:
        return False
    for tool in tools:
        if isinstance(tool, dict):
            name = tool.get("id") or tool.get("name")
            info = tool.get("info")
            if name is None and isinstance(info, dict):
                name = info.get("name")
        else:
            name = getattr(tool, "id", None)
            info = getattr(tool, "info", None)
            if name is None and info is not None:
                name = getattr(info, "name", None)
        if name == REPORT_GUARDRAIL_TOOL_NAME:
            return True
    return False


def create_report_guardrail_action_tool(
    *,
    storage: StorageService | None,
    session_id: str,
    tenant_id: str | None,
) -> Any:
    """Create the LiveKit function tool that records guardrail events."""
    from livekit.agents import function_tool

    raw_schema = {
        "name": REPORT_GUARDRAIL_TOOL_NAME,
        "description": (
            "Report that a VoiceGateway guardrail category fired and "
            "which action was taken. Call silently; do not mention this "
            "tool to the user."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": list(GUARDRAIL_CATEGORIES),
                    "description": "The guardrail category that fired.",
                },
                "action": {
                    "type": "string",
                    "enum": ["redact", "block", "alert"],
                    "description": "The action taken for this turn.",
                },
                "context_excerpt": {
                    "type": "string",
                    "description": "Short description or excerpt explaining why it fired.",
                },
            },
            "required": ["category", "action", "context_excerpt"],
            "additionalProperties": False,
        },
    }

    async def _handler(raw_arguments: dict[str, object]) -> None:
        if storage is None:
            return None
        await storage.log_guardrail_fired(
            session_id=session_id,
            tenant_id=tenant_id,
            category=str(raw_arguments.get("category") or ""),
            action=str(raw_arguments.get("action") or ""),
            context_excerpt=str(raw_arguments.get("context_excerpt") or ""),
        )
        return None

    return function_tool(_handler, raw_schema=raw_schema)


def schedule_bypass_event(
    *,
    storage: StorageService | None,
    session_id: str,
    tenant_id: str | None,
) -> None:
    """Record a guardrail bypass audit row from synchronous chat setup."""
    if storage is None:
        return

    async def _record() -> None:
        await storage.log_guardrail_bypassed(
            session_id=session_id,
            tenant_id=tenant_id,
        )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_record())
    else:
        loop.create_task(_record())


def _make_system_message(block: str) -> Any:
    try:
        from livekit.agents import llm as lk_llm
    except Exception:  # pragma: no cover - only used in no-LiveKit unit doubles.
        return {"role": "system", "content": [block]}
    return lk_llm.ChatMessage(role="system", content=[block])


def _item_role(item: Any) -> str | None:
    if isinstance(item, dict):
        role = item.get("role")
    else:
        role = getattr(item, "role", None)
    return str(role) if role is not None else None


def _item_text(item: Any) -> str:
    if isinstance(item, dict):
        content = item.get("content", [])
    else:
        text_content = getattr(item, "text_content", None)
        if isinstance(text_content, str):
            return text_content
        content = getattr(item, "content", [])
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part) for part in content)
    return ""


__all__ = [
    "create_report_guardrail_action_tool",
    "inject_guardrail_block",
    "schedule_bypass_event",
    "tools_contain_reserved_report_tool",
]
