"""Pydantic schema for voicegw.yaml config validation."""

from __future__ import annotations

import difflib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictBase(BaseModel):
    """Base with extra='forbid' for catching typos."""
    model_config = ConfigDict(extra="forbid")


class ProviderConfig(BaseModel):
    """Provider config — allows arbitrary provider-specific keys."""
    model_config = ConfigDict(extra="allow")

    api_key: str | None = None
    base_url: str | None = None
    enabled: bool = True


class ModelEntryConfig(BaseModel):
    """Single model entry under models.{stt|llm|tts}."""
    model_config = ConfigDict(extra="allow")

    provider: str
    model: str = ""
    default_voice: str | None = None


class StackConfig(_StrictBase):
    stt: str | None = None
    llm: str | None = None
    tts: str | None = None


class ProjectConfig(_StrictBase):
    name: str
    description: str = ""
    default_stack: str = ""
    daily_budget: float = Field(default=0.0, ge=0)
    budget_action: str = Field(default="warn", pattern=r"^(warn|throttle|block)$")
    tags: list[str] = Field(default_factory=list)


class ObservabilityConfig(_StrictBase):
    latency_tracking: bool = True
    cost_tracking: bool = True
    request_logging: bool = True


class CostTrackingConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    db_path: str = ""
    daily_budget_alert: float | None = None


class LatencyConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    ttfb_warning_ms: float = 500.0
    percentiles: list[float] = Field(default_factory=lambda: [50, 95, 99])


class RateLimitEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    requests_per_minute: int = 0


class DashboardConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 9090


class FallbackConfig(BaseModel):
    """Fallback chains — allows any modality key."""
    model_config = ConfigDict(extra="allow")

    stt: list[str] = Field(default_factory=list)
    llm: list[str] = Field(default_factory=list)
    tts: list[str] = Field(default_factory=list)


_VALID_TOP_LEVEL_KEYS = {
    "providers", "models", "stacks", "projects", "fallbacks",
    "observability", "cost_tracking", "latency", "rate_limits",
    "dashboard",
}


class VoiceGatewayConfig(BaseModel):
    """Top-level config schema for voicegw.yaml."""
    model_config = ConfigDict(extra="forbid")

    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    models: dict[str, dict[str, ModelEntryConfig]] = Field(default_factory=dict)
    stacks: dict[str, StackConfig] = Field(default_factory=dict)
    projects: dict[str, ProjectConfig] = Field(default_factory=dict)
    fallbacks: FallbackConfig = Field(default_factory=FallbackConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    cost_tracking: CostTrackingConfig = Field(default_factory=CostTrackingConfig)
    latency: LatencyConfig = Field(default_factory=LatencyConfig)
    rate_limits: dict[str, RateLimitEntry] = Field(default_factory=dict)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)

    @model_validator(mode="before")
    @classmethod
    def _suggest_typos(cls, values: Any) -> Any:
        """Add 'did you mean' hints for unknown top-level keys."""
        if not isinstance(values, dict):
            return values
        for key in list(values.keys()):
            if key not in _VALID_TOP_LEVEL_KEYS:
                matches = difflib.get_close_matches(key, _VALID_TOP_LEVEL_KEYS, n=1, cutoff=0.6)
                if matches:
                    raise ValueError(
                        f"Unknown config key '{key}' (did you mean '{matches[0]}'?)"
                    )
        return values
