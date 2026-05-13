"""Pydantic schema for voicegw.yaml config validation."""

from __future__ import annotations

import difflib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from voicegateway.schemas.guardrail_policy_schema import GuardrailPolicy


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


class MetricsConfig(_StrictBase):
    """Voice-conversation metrics knobs."""

    dead_air_threshold_seconds: float = Field(default=3.0, gt=0)
    talk_over_min_overlap_ms: int = Field(default=100, gt=0)
    turn_buffer_flush_size: int = Field(default=25, gt=0)


class ReplayConfig(_StrictBase):
    """Conversation-replay capture and retention knobs."""

    enabled: bool = True
    retention_days: int = Field(default=90, ge=1)
    buffer_size_events: int = Field(default=5000, ge=1)
    flush_size_events: int = Field(default=500, ge=1)


class RoutingConfig(_StrictBase):
    """Cross-modality routing knobs."""

    budget_ms: int = Field(default=1500, ge=1)
    rosters: dict[str, list[str]] = Field(default_factory=dict)
    fallback_to_fastest: bool = True


class BrandingConfig(_StrictBase):
    """White-label branding knobs."""

    logo_url: str | None = None
    accent_color: str | None = None
    product_name: str | None = None


class GuardrailsConfig(_StrictBase):
    """Per-project LLM-side guardrail policy."""

    enabled: bool = False
    categories: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_policy(self) -> GuardrailsConfig:
        GuardrailPolicy.model_validate(self.model_dump())
        return self


class TenantConfig(_StrictBase):
    """Multi-tenant attribution knobs."""

    virtual_key_stale_days: int = Field(default=90, ge=1)


class ProjectConfig(_StrictBase):
    name: str
    description: str = ""
    default_stack: str = ""
    daily_budget: float = Field(default=0.0, ge=0)
    budget_action: str = Field(default="warn", pattern=r"^(warn|throttle|block)$")
    tags: list[str] = Field(default_factory=list)
    # Per-project provider keys override the top-level ``providers:`` block
    # when set.
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    replay: ReplayConfig = Field(default_factory=ReplayConfig)
    tenant: TenantConfig = Field(default_factory=TenantConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    branding: BrandingConfig = Field(default_factory=BrandingConfig)
    guardrails: GuardrailsConfig = Field(default_factory=GuardrailsConfig)


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
    percentiles: list[float] = Field(default_factory=lambda: [50.0, 95.0, 99.0])


class RateLimitEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    requests_per_minute: int = 0


class DashboardConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 9090


class ServeConfig(BaseModel):
    """HTTP API serve config (the daemon-first ``voicegw serve`` target)."""

    model_config = ConfigDict(extra="allow")

    host: str = "0.0.0.0"
    port: int = 8080


class ApiKeyEntry(_StrictBase):
    """One entry under auth.api_keys."""

    token: str
    name: str = ""
    scopes: list[str] = Field(default_factory=lambda: ["*"])


class AuthConfig(_StrictBase):
    api_keys: list[ApiKeyEntry] = Field(default_factory=list)
    cors_origins: list[str] = Field(default_factory=list)


class FallbackConfig(BaseModel):
    """Fallback chains — allows any modality key."""

    model_config = ConfigDict(extra="allow")

    stt: list[str] = Field(default_factory=list)
    llm: list[str] = Field(default_factory=list)
    tts: list[str] = Field(default_factory=list)


_VALID_TOP_LEVEL_KEYS = {
    "providers",
    "models",
    "stacks",
    "projects",
    "default_project",
    "fallbacks",
    "observability",
    "cost_tracking",
    "latency",
    "rate_limits",
    "dashboard",
    "serve",
    "auth",
}


class VoiceGatewayConfig(BaseModel):
    """Top-level config schema for voicegw.yaml."""

    model_config = ConfigDict(extra="forbid")

    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    models: dict[str, dict[str, ModelEntryConfig]] = Field(default_factory=dict)
    stacks: dict[str, StackConfig] = Field(default_factory=dict)
    projects: dict[str, ProjectConfig] = Field(default_factory=dict)
    default_project: str | None = None
    fallbacks: FallbackConfig = Field(default_factory=FallbackConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    cost_tracking: CostTrackingConfig = Field(default_factory=CostTrackingConfig)
    latency: LatencyConfig = Field(default_factory=LatencyConfig)
    rate_limits: dict[str, RateLimitEntry] = Field(default_factory=dict)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    serve: ServeConfig = Field(default_factory=ServeConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)

    @model_validator(mode="before")
    @classmethod
    def _suggest_typos(cls, values: Any) -> Any:
        """Add 'did you mean' hints for unknown top-level keys."""
        if not isinstance(values, dict):
            return values
        for key in list(values.keys()):
            if key not in _VALID_TOP_LEVEL_KEYS:
                matches = difflib.get_close_matches(
                    key, _VALID_TOP_LEVEL_KEYS, n=1, cutoff=0.6
                )
                if matches:
                    raise ValueError(
                        f"Unknown config key '{key}' (did you mean '{matches[0]}'?)"
                    )
        return values
