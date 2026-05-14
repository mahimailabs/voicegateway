"""Guardrail domain service: prompts, project policy CRUD, and event logging.

Module-level pure functions handle prompt loading and composition (no DB).
The :class:`GuardrailService` class owns the DB-backed work: project policy
writes and guardrail event reads/writes.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import TYPE_CHECKING, Any

from voicegateway.repository import managed_project_repository as project_repo
from voicegateway.schemas.guardrail_policy_schema import (
    GUARDRAIL_CATEGORIES,
    GUARDRAIL_CATEGORY_DESCRIPTIONS,
    GUARDRAIL_PROMPT_VERSION,
    REPORT_GUARDRAIL_TOOL_NAME,
    GuardrailPolicy,
)

if TYPE_CHECKING:
    from voicegateway.core.database import Database


_PROMPT_PACKAGE = "voicegateway.data.prompts"


def load_prompt(category: str) -> str:
    """Load the curated prompt text for a guardrail category."""
    if category not in GUARDRAIL_CATEGORIES:
        raise ValueError(f"unknown guardrail category: {category}")
    return (
        resources.files(_PROMPT_PACKAGE)
        .joinpath(f"{category}.md")
        .read_text(encoding="utf-8")
        .strip()
    )


def compose_block(policy: GuardrailPolicy) -> str:
    """Return the versioned prompt block for an active policy."""
    active = policy.active_categories
    if not active:
        return ""
    template = (
        resources.files(_PROMPT_PACKAGE)
        .joinpath("_template.md")
        .read_text(encoding="utf-8")
    )
    category_blocks = []
    for category, action in active.items():
        text = load_prompt(category)
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


def policy_to_json(policy: GuardrailPolicy) -> str:
    """Canonical compact JSON for session snapshots."""
    return json.dumps(policy.to_storage_dict(), sort_keys=True, separators=(",", ":"))


class GuardrailService:
    """DB-backed guardrail surface: project policy CRUD + event logging."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def set_project_policy(
        self,
        *,
        project_id: str,
        policy: dict[str, Any] | None,
        name: str,
        description: str = "",
        daily_budget: float = 0.0,
        budget_action: str = "warn",
        default_stack: str | None = None,
        stt_model: str | None = None,
        llm_model: str | None = None,
        tts_model: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """Write or update the guardrail policy on one managed project."""
        async with self._db.session() as s:
            await project_repo.set_project_guardrails(
                s,
                project_id=project_id,
                policy=policy,
                name=name,
                description=description,
                daily_budget=daily_budget,
                budget_action=budget_action,
                default_stack=default_stack,
                stt_model=stt_model,
                llm_model=llm_model,
                tts_model=tts_model,
                tags=tags,
            )


__all__ = [
    "GuardrailService",
    "compose_block",
    "load_prompt",
    "policy_to_json",
]
