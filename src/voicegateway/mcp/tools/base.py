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
    """A single MCP tool — metadata plus the async handler."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler


def schema_for(model: type[BaseModel]) -> dict[str, Any]:
    """Return a JSON Schema dict for a Pydantic model, stripped of Pydantic-isms."""
    return model.model_json_schema()


def make_tool(
    name: str,
    description: str,
    input_model: type[BaseModel],
    handler: ToolHandler,
) -> ToolDef:
    return ToolDef(
        name=name,
        description=description,
        input_schema=schema_for(input_model),
        handler=handler,
    )
