"""Input schemas for the managed-models MCP tool group."""

from __future__ import annotations

from typing import Literal

from voicegateway.schemas.mcp import StrictMcpInput


class ListModelsInput(StrictMcpInput):
    """Input for list_models: optional modality/provider/enabled filters."""

    modality: Literal["stt", "llm", "tts"] | None = None
    provider_id: str | None = None
    enabled_only: bool = True


class RegisterModelInput(StrictMcpInput):
    """Input for register_model: full model definition."""

    modality: Literal["stt", "llm", "tts"]
    provider_id: str
    model_name: str
    display_name: str | None = None
    default_language: str | None = None
    default_voice: str | None = None
    config: dict | None = None


class DeleteModelInput(StrictMcpInput):
    """Input for delete_model: requires confirm flag for destructive write."""

    model_id: str
    confirm: bool = False


__all__ = [
    "DeleteModelInput",
    "ListModelsInput",
    "RegisterModelInput",
]
