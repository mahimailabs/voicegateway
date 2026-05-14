"""Async repo for the managed_projects table + branding validator."""

from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING, Any

from voicegateway.schemas.guardrail_policy_schema import GuardrailPolicy

if TYPE_CHECKING:
    import aiosqlite


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


async def list_projects(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    """Return every managed_projects row with branding + guardrail JSON parsed."""
    cursor = await db.execute(
        "SELECT project_id, name, description, daily_budget, budget_action, "
        "default_stack, stt_model, llm_model, tts_model, tags, "
        "created_at, updated_at, branding_json, guardrail_policy_json "
        "FROM managed_projects ORDER BY created_at ASC"
    )
    rows: list[dict[str, Any]] = []
    async for row in cursor:
        branding_raw = row[12] if len(row) > 12 else None
        guardrail_raw = row[13] if len(row) > 13 else None
        branding = None
        if branding_raw:
            try:
                branding = json.loads(branding_raw)
            except (ValueError, TypeError):
                branding = None
        guardrail_policy = None
        if guardrail_raw:
            try:
                guardrail_policy = json.loads(guardrail_raw)
            except (ValueError, TypeError):
                guardrail_policy = None
        rows.append(
            {
                "project_id": row[0],
                "name": row[1],
                "description": row[2],
                "daily_budget": row[3],
                "budget_action": row[4],
                "default_stack": row[5],
                "stt_model": row[6],
                "llm_model": row[7],
                "tts_model": row[8],
                "tags": json.loads(row[9] or "[]"),
                "created_at": row[10],
                "updated_at": row[11],
                "branding": branding,
                "guardrail_policy": guardrail_policy,
            }
        )
    return rows


async def get_project(
    db: aiosqlite.Connection, project_id: str
) -> dict[str, Any] | None:
    """Find one managed project by id (linear scan over the small table)."""
    for p in await list_projects(db):
        if p["project_id"] == project_id:
            return p
    return None


async def upsert_project(
    db: aiosqlite.Connection,
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
    """Insert or update one managed_projects row."""
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
    now = time.time()
    await db.execute(
        """INSERT INTO managed_projects
               (project_id, name, description, daily_budget, budget_action,
                default_stack, stt_model, llm_model, tts_model, tags,
                created_at, updated_at, branding_json, guardrail_policy_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
               guardrail_policy_json=COALESCE(excluded.guardrail_policy_json, guardrail_policy_json),
               updated_at=excluded.updated_at""",
        (
            project_id,
            name,
            description,
            daily_budget,
            budget_action,
            default_stack,
            stt_model,
            llm_model,
            tts_model,
            json.dumps(tags or []),
            now,
            now,
            branding_json,
            guardrail_json,
        ),
    )
    await db.commit()


async def set_project_guardrails(
    db: aiosqlite.Connection,
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
    now = time.time()
    await db.execute(
        """INSERT INTO managed_projects
               (project_id, name, description, daily_budget, budget_action,
                default_stack, stt_model, llm_model, tts_model, tags,
                created_at, updated_at, guardrail_policy_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(project_id) DO UPDATE SET
               guardrail_policy_json=excluded.guardrail_policy_json,
               updated_at=excluded.updated_at""",
        (
            project_id,
            name,
            description,
            daily_budget,
            budget_action,
            default_stack,
            stt_model,
            llm_model,
            tts_model,
            json.dumps(tags or []),
            now,
            now,
            guardrail_json,
        ),
    )
    await db.commit()


async def delete_project(db: aiosqlite.Connection, project_id: str) -> bool:
    """Delete one managed_projects row. Returns True when a row was removed."""
    cursor = await db.execute(
        "DELETE FROM managed_projects WHERE project_id = ?", (project_id,)
    )
    await db.commit()
    return (cursor.rowcount or 0) > 0


__all__ = [
    "delete_project",
    "get_project",
    "list_projects",
    "set_project_guardrails",
    "upsert_project",
    "validate_branding",
]
