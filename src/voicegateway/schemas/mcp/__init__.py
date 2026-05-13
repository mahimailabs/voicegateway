"""Pydantic input schemas for the MCP tool surface, split by tool group."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StrictMcpInput(BaseModel):
    """Base for every MCP tool input: rejects unknown fields."""

    model_config = ConfigDict(extra="forbid")


__all__ = ["StrictMcpInput"]
