"""Input schemas for the observability MCP tools."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from voicegateway.schemas.mcp import StrictMcpInput


class GetHealthInput(StrictMcpInput):
    """Input for the get_health tool."""


class GetProviderStatusInput(StrictMcpInput):
    """Input for get_provider_status: optional provider scope."""

    provider_id: str | None = None


class GetCostsInput(StrictMcpInput):
    """Input for get_costs: period + optional project scope."""

    period: Literal["today", "week", "month", "all"] = "today"
    project: str | None = None


class GetLatencyStatsInput(StrictMcpInput):
    """Input for get_latency_stats: period + optional project/modality scope."""

    period: Literal["today", "week", "month"] = "today"
    project: str | None = None
    modality: Literal["stt", "llm", "tts"] | None = None


class GetLogsInput(StrictMcpInput):
    """Input for get_logs: pagination + optional filters."""

    project: str | None = None
    modality: Literal["stt", "llm", "tts"] | None = None
    model_id: str | None = None
    status: Literal["success", "error", "fallback"] | None = None
    limit: int = Field(default=50, ge=1, le=1000)


__all__ = [
    "GetCostsInput",
    "GetHealthInput",
    "GetLatencyStatsInput",
    "GetLogsInput",
    "GetProviderStatusInput",
]
