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


class MetricsConfig(_StrictBase):
    """v0.2.0 voice-conversation metrics knobs (REQ-VG-METRICS-001..006).

    All fields are per-project overridable via the ``metrics:`` block
    under each project entry in ``voicegw.yaml``. Defaults match the
    Foundry's stated values (Open Questions 2 and 3 locked at the v0.2.0
    BUILD/T01 step).
    """

    dead_air_threshold_seconds: float = Field(default=3.0, gt=0)
    talk_over_min_overlap_ms: int = Field(default=100, gt=0)
    turn_buffer_flush_size: int = Field(default=25, gt=0)


class ReplayConfig(_StrictBase):
    """v0.3.0 conversation-replay knobs (REQ-VG-REPLAY-001..006).

    All fields are per-project overridable via the ``replay:`` block
    under each project entry in ``voicegw.yaml``. Defaults match the
    Foundry's stated values; OQ1's storage-cost target is enforced by
    T18's smoke test, not at config time.
    """

    enabled: bool = True
    retention_days: int = Field(default=90, ge=1)
    buffer_size_events: int = Field(default=5000, ge=1)
    flush_size_events: int = Field(default=500, ge=1)


class RoutingConfig(_StrictBase):
    """v0.5.0 cross-modality routing knobs (REQ-VG-ROUTE-001..002).

    ``budget_ms`` is the latency budget in milliseconds; the OQ1 lock
    is 1500 ms. ``rosters`` maps modality ('stt'/'llm'/'tts') to an
    ordered list of provider ids the router may pick from. Empty
    rosters force callers to provide overrides; otherwise the router
    raises ``ValueError`` at session-create time.
    """

    budget_ms: int = Field(default=1500, ge=1)
    rosters: dict[str, list[str]] = Field(default_factory=dict)
    fallback_to_fastest: bool = True


class BrandingConfig(_StrictBase):
    """v0.5.0 white-label branding knobs (REQ-VG-ROUTE-004).

    All fields nullable; a project with no branding falls back to
    the default VoiceGateway brand on next dashboard layout mount.
    ``accent_color`` is expected to be a hex string when set; the
    dashboard validates the format on upload but the schema is
    permissive here.
    """

    logo_url: str | None = None
    accent_color: str | None = None
    product_name: str | None = None


class TenantConfig(_StrictBase):
    """v0.4.0 multi-tenant attribution knobs (REQ-VG-TENANT-001..004).

    Per-project overridable via the ``tenant:`` block under each
    project entry in ``voicegw.yaml``. ``virtual_key_stale_days``
    drives the dashboard's stale-key surface (a key whose last_used_at
    or issued_at is older than this threshold is flagged in the
    Virtual Keys page); the default matches the Foundry's 90-day
    lock.
    """

    virtual_key_stale_days: int = Field(default=90, ge=1)


class ProjectConfig(_StrictBase):
    name: str
    description: str = ""
    default_stack: str = ""
    daily_budget: float = Field(default=0.0, ge=0)
    budget_action: str = Field(default="warn", pattern=r"^(warn|throttle|block)$")
    tags: list[str] = Field(default_factory=list)
    # v0.0.5: per-project provider keys override the top-level
    # `providers:` block when set. See design.md section 3.3.
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    # v0.2.0: per-project metrics knobs. The defaults match
    # MetricsConfig's own defaults so projects that do not set
    # ``metrics:`` get the canonical Foundry values.
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    # v0.3.0: per-project replay capture + retention knobs.
    replay: ReplayConfig = Field(default_factory=ReplayConfig)
    # v0.4.0: per-project multi-tenant knobs.
    tenant: TenantConfig = Field(default_factory=TenantConfig)
    # v0.5.0: per-project routing knobs.
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    # v0.5.0: per-project branding knobs.
    branding: BrandingConfig = Field(default_factory=BrandingConfig)


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
    """HTTP API serve config (the daemon-first ``voicegw serve`` target).

    The v0.1.0 onboarding wizard persists the user-selected port here so
    that the platform service unit (which runs bare ``voicegw serve``)
    binds to the same port the user typed during install.
    """

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
