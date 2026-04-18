"""Pydantic input schemas for every MCP tool — the canonical source of truth."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Observability ---


class GetHealthInput(_Strict):
    pass


class GetProviderStatusInput(_Strict):
    provider_id: str | None = None


class GetCostsInput(_Strict):
    period: Literal["today", "week", "month", "all"] = "today"
    project: str | None = None


class GetLatencyStatsInput(_Strict):
    period: Literal["today", "week", "month"] = "today"
    project: str | None = None
    modality: Literal["stt", "llm", "tts"] | None = None


# --- Providers ---


class ListProvidersInput(_Strict):
    pass


class GetProviderInput(_Strict):
    provider_id: str


class TestProviderInput(_Strict):
    provider_id: str


class AddProviderInput(_Strict):
    provider_id: str
    provider_type: str
    api_key: str = ""
    base_url: str | None = None


class DeleteProviderInput(_Strict):
    provider_id: str
    confirm: bool = False


# --- Models ---


class ListModelsInput(_Strict):
    modality: Literal["stt", "llm", "tts"] | None = None
    provider_id: str | None = None
    enabled_only: bool = True


class RegisterModelInput(_Strict):
    modality: Literal["stt", "llm", "tts"]
    provider_id: str
    model_name: str
    display_name: str | None = None
    default_language: str | None = None
    default_voice: str | None = None
    config: dict | None = None


class DeleteModelInput(_Strict):
    model_id: str
    confirm: bool = False


# --- Projects ---


class ListProjectsInput(_Strict):
    pass


class GetProjectInput(_Strict):
    project_id: str


class CreateProjectInput(_Strict):
    project_id: str
    name: str
    description: str = ""
    daily_budget: float = Field(default=0.0, ge=0.0)
    budget_action: Literal["warn", "throttle", "block"] = "warn"
    stt_model: str | None = None
    llm_model: str | None = None
    tts_model: str | None = None
    default_stack: str | None = None
    tags: list[str] | None = None


class DeleteProjectInput(_Strict):
    project_id: str
    confirm: bool = False


# --- Logs ---


class GetLogsInput(_Strict):
    project: str | None = None
    modality: Literal["stt", "llm", "tts"] | None = None
    model_id: str | None = None
    status: Literal["success", "error", "fallback"] | None = None
    limit: int = Field(default=50, ge=1, le=1000)
