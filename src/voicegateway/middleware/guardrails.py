"""LLM-side guardrail prompt and tool plumbing."""

from __future__ import annotations

import asyncio
import json
import logging
from importlib import resources
from typing import TYPE_CHECKING, Any

from voicegateway.schemas.guardrail_policy_schema import (
    GUARDRAIL_CATEGORIES,
    GUARDRAIL_CATEGORY_DESCRIPTIONS,
    GUARDRAIL_PROMPT_VERSION,
    REPORT_GUARDRAIL_TOOL_NAME,
    GuardrailPolicy,
)

if TYPE_CHECKING:
    from voicegateway.services.storage_service import StorageService


_PROMPT_PACKAGE = "voicegateway.data.guardrail_prompts"
_MARKER = "<voicegateway_guardrails"
logger = logging.getLogger(__name__)


def load_guardrail_prompt(category: str) -> str:
    """Load the curated prompt text for a category."""
    if category not in GUARDRAIL_CATEGORIES:
        raise ValueError(f"unknown guardrail category: {category}")
    return (
        resources.files(_PROMPT_PACKAGE)
        .joinpath(f"{category}.txt")
        .read_text(encoding="utf-8")
        .strip()
    )


def compose_guardrail_block(policy: GuardrailPolicy) -> str:
    """Return the versioned prompt block for an active policy."""
    active = policy.active_categories
    if not active:
        return ""
    template = (
        resources.files(_PROMPT_PACKAGE)
        .joinpath("_template.txt")
        .read_text(encoding="utf-8")
    )
    category_blocks = []
    for category, action in active.items():
        text = load_guardrail_prompt(category)
        category_blocks.append(
            f"## {category}\n"
            f"Description: {GUARDRAIL_CATEGORY_DESCRIPTIONS[category]}\n"
            f"Action: {action}\n"
            f"{text}"
        )
    return template.format(
        version=GUARDRAIL_PROMPT_VERSION,
        categories="\n\n".join(category_blocks),
        tool_name=REPORT_GUARDRAIL_TOOL_NAME,
    ).strip()


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
        category = str(raw_arguments.get("category") or "")
        action = str(raw_arguments.get("action") or "")
        context_excerpt = str(raw_arguments.get("context_excerpt") or "")
        from voicegateway.repository import (
            guardrail_events_repository as guardrail_events,
        )

        await storage._ensure_initialized()
        async with storage._conn.session() as db:
            await guardrail_events.create_event(
                db,
                session_id=session_id,
                tenant_id=tenant_id,
                event_type="fired",
                category=category,
                action=action,
                context_excerpt=context_excerpt,
            )
            await db.commit()
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
        from voicegateway.repository import (
            guardrail_events_repository as guardrail_events,
        )

        try:
            await storage._ensure_initialized()
            async with storage._conn.session() as db:
                await guardrail_events.create_event(
                    db,
                    session_id=session_id,
                    tenant_id=tenant_id,
                    event_type="bypassed",
                    context_excerpt="guardrail injection bypassed for this session",
                )
                await db.commit()
        except Exception:
            logger.warning(
                "failed to record guardrail bypass event session_id=%s tenant_id=%s",
                session_id,
                tenant_id,
                exc_info=True,
            )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_record())
    else:
        loop.create_task(_record())


def guardrail_policy_json(policy: GuardrailPolicy) -> str:
    """Canonical compact JSON for session snapshots."""
    return json.dumps(policy.to_storage_dict(), sort_keys=True, separators=(",", ":"))


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
    "compose_guardrail_block",
    "create_report_guardrail_action_tool",
    "guardrail_policy_json",
    "inject_guardrail_block",
    "load_guardrail_prompt",
    "schedule_bypass_event",
    "tools_contain_reserved_report_tool",
]
