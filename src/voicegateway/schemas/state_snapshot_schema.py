"""Pydantic shape for one captured conversation-state snapshot."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class StateSnapshot(BaseModel):
    """One captured conversation-state snapshot."""

    system_prompt: str = ""
    message_history: list[dict[str, Any]] = Field(default_factory=list)
    tool_call_in_flight: dict[str, Any] | None = None
    structured_output_collected: dict[str, Any] | None = None
