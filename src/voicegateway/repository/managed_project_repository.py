"""Async repo for the managed_projects table + branding validator (ORM).

The UPSERTs use ``text()`` clauses because the legacy SQL relied on a
COALESCE-with-excluded pattern that has battle-tested semantics: when
an upsert call passes ``branding=None`` the existing branding column
must NOT be overwritten with NULL. Expressing this in pure ORM is
verbose and harder to audit-by-diff; ``text()`` keeps the SQL bytes
identical to the legacy migration.
"""

from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlmodel import delete, select

from voicegateway.models.managed_project_model import ManagedProject
from voicegateway.schemas.guardrail_policy_schema import GuardrailPolicy

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_PROJECT_UPSERT = text(
    """
    INSERT INTO managed_projects (
        project_id, name, description, daily_budget, budget_action,
        default_stack, stt_model, llm_model, tts_model, tags,
        created_at, updated_at, branding_json, guardrail_policy_json
    ) VALUES (
        :project_id, :name, :description, :daily_budget, :budget_action,
        :default_stack, :stt_model, :llm_model, :tts_model, :tags,
        :now, :now, :branding_json, :guardrail_json
    )
    ON CONFLICT(project_id) DO UPDATE SET
        name=excluded.name,
        description=excluded.description,
        daily_budget=excluded.daily_budget,
        budget_action=excluded.budget_action,
        default_stack=excluded.default_stack,
        stt_model=excluded.stt_model,
        llm_model=excluded.llm_model,
        tts_model=excluded.tts_model,
        tags=excluded.tags,
        branding_json=COALESCE(excluded.branding_json, branding_json),
        guardrail_policy_json=COALESCE(
            excluded.guardrail_policy_json, guardrail_policy_json
        ),
        updated_at=excluded.updated_at
    """
)


_GUARDRAILS_UPSERT = text(
    """
    INSERT INTO managed_projects (
        project_id, name, description, daily_budget, budget_action,
        default_stack, stt_model, llm_model, tts_model, tags,
        created_at, updated_at, guardrail_policy_json
    ) VALUES (
        :project_id, :name, :description, :daily_budget, :budget_action,
        :default_stack, :stt_model, :llm_model, :tts_model, :tags,
        :now, :now, :guardrail_json
    )
    ON CONFLICT(project_id) DO UPDATE SET
        guardrail_policy_json=excluded.guardrail_policy_json,
        updated_at=excluded.updated_at
    """
)


def validate_branding(
    branding: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Validate the branding payload before write."""
    if branding is None:
        return None
    if not isinstance(branding, dict):
        raise ValueError(
            f"branding must be a dict or None, got {type(branding).__name__}"
        )
    if not branding:
        return None
    out: dict[str, Any] = {}
    allowed = {"logo_url", "accent_color", "product_name"}
    for key in branding:
        if key not in allowed:
            raise ValueError(
                f"branding has unknown key {key!r}; allowed: {sorted(allowed)}"
            )
    logo_url = branding.get("logo_url")
    if logo_url is not None:
        if not isinstance(logo_url, str) or len(logo_url) > 2048:
            raise ValueError("branding.logo_url must be a string up to 2048 chars")
        out["logo_url"] = logo_url
    accent_color = branding.get("accent_color")
    if accent_color is not None:
        if not isinstance(accent_color, str) or not re.fullmatch(
            r"#[0-9A-Fa-f]{3}([0-9A-Fa-f]{3})?", accent_color
        ):
            raise ValueError(
                "branding.accent_color must be a hex string (#RGB or #RRGGBB)"
            )
        out["accent_color"] = accent_color
    product_name = branding.get("product_name")
    if product_name is not None:
        if not isinstance(product_name, str) or len(product_name) > 64:
            raise ValueError("branding.product_name must be a string up to 64 chars")
        out["product_name"] = product_name
    return out or None


def _row_to_dict(p: ManagedProject) -> dict[str, Any]:
    branding = None
    if p.branding_json:
        try:
            branding = json.loads(p.branding_json)
        except (ValueError, TypeError):
            branding = None
    guardrail_policy = None
    if p.guardrail_policy_json:
        try:
            guardrail_policy = json.loads(p.guardrail_policy_json)
        except (ValueError, TypeError):
            guardrail_policy = None
    return {
        "project_id": p.project_id,
        "name": p.name,
        "description": p.description,
        "daily_budget": p.daily_budget,
        "budget_action": p.budget_action,
        "default_stack": p.default_stack,
        "stt_model": p.stt_model,
        "llm_model": p.llm_model,
        "tts_model": p.tts_model,
        "tags": json.loads(p.tags or "[]"),
        "created_at": p.created_at,
        "updated_at": p.updated_at,
        "branding": branding,
        "guardrail_policy": guardrail_policy,
    }


async def list_projects(session: AsyncSession) -> list[dict[str, Any]]:
    """Return every managed_projects row with branding + guardrail parsed."""
    result = await session.execute(
        select(ManagedProject).order_by(ManagedProject.created_at.asc())
    )
    return [_row_to_dict(p) for p in result.scalars().all()]


async def get_project(session: AsyncSession, project_id: str) -> dict[str, Any] | None:
    """Find one managed project by id."""
    p = await session.get(ManagedProject, project_id)
    return _row_to_dict(p) if p is not None else None


async def upsert_project(
    session: AsyncSession,
    project_id: str,
    name: str,
    description: str = "",
    daily_budget: float = 0.0,
    budget_action: str = "warn",
    default_stack: str | None = None,
    stt_model: str | None = None,
    llm_model: str | None = None,
    tts_model: str | None = None,
    tags: list[str] | None = None,
    branding: dict[str, Any] | None = None,
    guardrail_policy: dict[str, Any] | None = None,
) -> None:
    """Insert or update one managed_projects row.

    ``branding=None`` and ``guardrail_policy=None`` preserve the
    existing column values via SQLite ``COALESCE(excluded.x, x)``.
    """
    validated_branding = validate_branding(branding)
    branding_json = json.dumps(validated_branding) if validated_branding else None
    validated_guardrails = (
        GuardrailPolicy.from_raw(guardrail_policy).to_storage_dict()
        if guardrail_policy is not None
        else None
    )
    guardrail_json = (
        json.dumps(validated_guardrails, sort_keys=True)
        if validated_guardrails is not None
        else None
    )
    await session.execute(
        _PROJECT_UPSERT,
        {
            "project_id": project_id,
            "name": name,
            "description": description,
            "daily_budget": daily_budget,
            "budget_action": budget_action,
            "default_stack": default_stack,
            "stt_model": stt_model,
            "llm_model": llm_model,
            "tts_model": tts_model,
            "tags": json.dumps(tags or []),
            "now": time.time(),
            "branding_json": branding_json,
            "guardrail_json": guardrail_json,
        },
    )
    await session.commit()


async def set_project_guardrails(
    session: AsyncSession,
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
    """Set or clear a project's guardrail policy overlay."""
    guardrail_json = None
    if policy is not None:
        validated = GuardrailPolicy.from_raw(policy).to_storage_dict()
        guardrail_json = json.dumps(validated, sort_keys=True)
    await session.execute(
        _GUARDRAILS_UPSERT,
        {
            "project_id": project_id,
            "name": name,
            "description": description,
            "daily_budget": daily_budget,
            "budget_action": budget_action,
            "default_stack": default_stack,
            "stt_model": stt_model,
            "llm_model": llm_model,
            "tts_model": tts_model,
            "tags": json.dumps(tags or []),
            "now": time.time(),
            "guardrail_json": guardrail_json,
        },
    )
    await session.commit()


async def delete_project(session: AsyncSession, project_id: str) -> bool:
    """Delete one managed_projects row. True when a row was removed."""
    result = await session.execute(
        delete(ManagedProject).where(ManagedProject.project_id == project_id)
    )
    await session.commit()
    return (result.rowcount or 0) > 0


__all__ = [
    "delete_project",
    "get_project",
    "list_projects",
    "set_project_guardrails",
    "upsert_project",
    "validate_branding",
]
