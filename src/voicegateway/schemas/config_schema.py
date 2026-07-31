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


class TenantConfig(_StrictBase):
    """Multi-tenant attribution knobs."""

    api_key_stale_days: int = Field(default=90, ge=1)


class IngestConfig(_StrictBase):
    """Rate-limit knobs for POST /v1/ingest (fleet collector)."""

    enabled: bool = True
    requests_per_minute: int = Field(default=120, ge=0)
    burst: int = Field(default=240, ge=0)
    max_batch_size: int = Field(default=1000, ge=1)


class RetentionConfig(_StrictBase):
    """Collector-wide retention default for requests and sessions."""

    enabled: bool = True
    default_days: int = Field(default=90, ge=1)


class WorkersConfig(_StrictBase):
    """Background-worker cadence for the collector (rollups, retention, scrape)."""

    enabled: bool = True
    rollup_interval_seconds: int = Field(default=900, ge=1)
    retention_interval_seconds: int = Field(default=3600, ge=1)
    # Cadence of the Prometheus node scrape. 15 s is Prometheus' own default and
    # the resolution the node_samples row budget is sized against. Setting it
    # does NOT turn the scrape on: the worker exists only when
    # VOICEGW_NODE_SCRAPE_TARGETS names at least one target.
    node_scrape_interval_seconds: int = Field(default=15, ge=1)


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


class LiveKitConfig(BaseModel):
    """Optional LiveKit deployment credentials for the diagnostics probes.

    Read by ``voicegw livekit`` and the dashboard Diagnostics page through
    ``livekit_diag``. All fields are optional; the ``LIVEKIT_URL`` /
    ``LIVEKIT_API_KEY`` / ``LIVEKIT_API_SECRET`` env vars take precedence when
    set. Accepting this block here is what lets an operator configure LiveKit
    in ``voicegw.yaml`` (the config the daemon already serves) instead of the
    daemon's environment.
    """

    model_config = ConfigDict(extra="allow")

    url: str = ""
    api_key: str = ""
    api_secret: str = ""


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


class ClickHouseConfig(_StrictBase):
    """Optional ClickHouse telemetry sink config.

    When ``host`` is non-empty the collector constructs a ``ClickHouseSink``
    instead of writing to the embedded SQLite store.  Migrations are applied
    automatically on startup.
    """

    host: str = ""
    port: int = Field(default=8123, ge=1, le=65535)
    username: str = "default"
    password: str = ""
    database: str = "telemetry"


class RateRuleConfig(_StrictBase):
    """One rate-card rule. Scope defaults to "any" (``*`` / null).

    A rule is either cost-plus (``markup``) or fixed (``fixed`` + ``unit``);
    :meth:`voicegateway.billing.rate_card.RateCard.from_config` picks the kind
    from the same fields at wiring time.
    """

    modality: str = "*"
    provider: str = "*"
    model: str = "*"
    tenant: str | None = None
    plan: str | None = None
    markup: float | None = Field(default=None, gt=0)
    fixed: float | None = Field(default=None, ge=0)
    unit: str | None = None

    @model_validator(mode="after")
    def _check_kind(self) -> RateRuleConfig:
        """Reject ambiguous or incomplete rules at config-load time.

        Without this the errors only surface later as a raw ``ValueError``
        inside ``RateCard.from_config`` (at gateway construction or on
        ``GET /v1/billing/rate-card``), bypassing the friendly ``ConfigError``.
        """
        from voicegateway.billing.rate_card import VALID_UNITS

        if self.fixed is not None:
            if self.markup is not None:
                raise ValueError(
                    "a rate rule sets either 'markup' (cost-plus) or 'fixed' "
                    "($/unit), not both"
                )
            if self.unit not in VALID_UNITS:
                raise ValueError(
                    "a fixed rate rule needs a valid 'unit' (one of "
                    f"{sorted(VALID_UNITS)})"
                )
        elif self.unit is not None:
            raise ValueError("'unit' is only valid on a fixed rule (set 'fixed')")
        return self


class RateCardConfig(_StrictBase):
    """The ``rate_card:`` block: a global default markup plus override rules."""

    default_markup: float = Field(default=1.0, gt=0)
    rules: list[RateRuleConfig] = Field(default_factory=list)


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
    "livekit",
    "auth",
    "ingest",
    "retention",
    "workers",
    "clickhouse",
    "rate_card",
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
    livekit: LiveKitConfig = Field(default_factory=LiveKitConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    workers: WorkersConfig = Field(default_factory=WorkersConfig)
    clickhouse: ClickHouseConfig = Field(default_factory=ClickHouseConfig)
    rate_card: RateCardConfig = Field(default_factory=RateCardConfig)

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
