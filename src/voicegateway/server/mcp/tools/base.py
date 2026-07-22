"""Shared building blocks for MCP tool definitions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

ToolHandler = Callable[["Gateway", dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class ToolDef:
    """A single MCP tool — metadata plus the async handler.

    ``required_scope`` gates visibility and invocation: ``None`` means the tool
    is part of the default framework-agnostic surface (reads + project/budget/
    rate-card config) that any connected agent sees. A non-``None`` scope (e.g.
    ``ADMIN_SCOPE``) hides the tool unless the server was started in admin mode
    — used for the legacy provider-config tools and destructive deletes.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    required_scope: str | None = None


def schema_for(model: type[BaseModel]) -> dict[str, Any]:
    """Return a JSON Schema dict for a Pydantic model, stripped of Pydantic-isms."""
    return model.model_json_schema()


def make_tool(
    name: str,
    description: str,
    input_model: type[BaseModel],
    handler: ToolHandler,
    *,
    required_scope: str | None = None,
) -> ToolDef:
    return ToolDef(
        name=name,
        description=description,
        input_schema=schema_for(input_model),
        handler=handler,
        required_scope=required_scope,
    )
