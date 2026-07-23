"""YAML config loader with environment variable substitution."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from voicegateway.schemas.config_schema import (
    ClickHouseConfig,
    IngestConfig,
    RetentionConfig,
    VoiceGatewayConfig,
    WorkersConfig,
)

# Re-export ClickHouseConfig so callers can import it from here without going
# through the schema module directly.
__all__ = ["ClickHouseConfig", "GatewayConfig"]

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")

_CONFIG_PATHS = [
    Path("./voicegw.yaml"),
    Path.home() / ".config" / "voicegateway" / "voicegw.yaml",
    Path("/etc/voicegateway/voicegw.yaml"),
]


class ConfigError(Exception):
    """Raised when configuration is invalid or missing."""


def _substitute_env_vars(value: Any) -> Any:
    """Recursively substitute ${ENV_VAR} patterns in config values."""
    if isinstance(value, str):

        def _replace(match: re.Match) -> str:
            var_name = match.group(1)
            env_val = os.environ.get(var_name, "")
            return env_val

        return _ENV_VAR_PATTERN.sub(_replace, value)
    elif isinstance(value, dict):
        return {k: _substitute_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_substitute_env_vars(item) for item in value]
    return value


@dataclass
class MetricsConfig:
    """Voice-conversation metrics knobs."""

    dead_air_threshold_seconds: float = 3.0
    talk_over_min_overlap_ms: int = 100
    turn_buffer_flush_size: int = 25


@dataclass
class ReplayConfig:
    """Conversation-replay capture + retention knobs."""

    enabled: bool = True
    retention_days: int = 90
    buffer_size_events: int = 5000
    flush_size_events: int = 500


@dataclass
class RoutingConfig:
    """Cross-modality routing knobs."""

    budget_ms: int = 1500
    rosters: dict[str, list[str]] = field(default_factory=dict)
    fallback_to_fastest: bool = True


@dataclass
class BrandingConfig:
    """White-label branding knobs."""

    logo_url: str | None = None
    accent_color: str | None = None
    product_name: str | None = None


@dataclass
class ProjectConfig:
    """Configuration for a single project."""

    id: str
    name: str
    description: str = ""
    default_stack: str = ""
    daily_budget: float = 0.0
    budget_action: str = "warn"
    tags: list[str] = field(default_factory=list)
    source: str = "yaml"
    # Per-project provider keys override the top-level ``providers:`` block
    # for inference factory calls inside this project.
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    replay: ReplayConfig = field(default_factory=ReplayConfig)
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    branding: BrandingConfig = field(default_factory=BrandingConfig)

    @property
    def accent(self) -> str:
        """Return an accent color based on the project's first tag."""
        if not self.tags:
            return "blue"
        first = self.tags[0].lower()
        if "prod" in first:
            return "green"
        if "stag" in first:
            return "yellow"
        if "dev" in first or "test" in first:
            return "blue"
        return "pink"


@dataclass
class AuthConfig:
    """HTTP API authentication settings."""

    api_keys: list[dict[str, Any]] = field(default_factory=list)
    cors_origins: list[str] = field(default_factory=list)


@dataclass
class GatewayConfig:
    """Parsed VoiceGateway configuration."""

    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    models: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    fallbacks: dict[str, list[str]] = field(default_factory=dict)
    cost_tracking: dict[str, Any] = field(default_factory=dict)
    latency: dict[str, Any] = field(default_factory=dict)
    rate_limits: dict[str, dict[str, Any]] = field(default_factory=dict)
    dashboard: dict[str, Any] = field(default_factory=dict)
    serve: dict[str, Any] = field(default_factory=dict)
    projects: dict[str, ProjectConfig] = field(default_factory=dict)
    default_project: str | None = None
    stacks: dict[str, dict[str, str]] = field(default_factory=dict)
    auth: AuthConfig = field(default_factory=AuthConfig)
    observability: dict[str, Any] = field(
        default_factory=lambda: {
            "latency_tracking": True,
            "cost_tracking": True,
            "request_logging": True,
        }
    )
    ingest: IngestConfig = field(default_factory=IngestConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    workers: WorkersConfig = field(default_factory=WorkersConfig)
    clickhouse: ClickHouseConfig = field(default_factory=ClickHouseConfig)
    # Billing: the ``rate_card:`` seed (default_markup + rules). Kept as a raw
    # dict; the gateway builds a RateCard via RateCard.from_config at wiring.
    rate_card: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> GatewayConfig:
        """Load configuration from a YAML file."""
        if config_path is None:
            config_path = os.environ.get("VOICEGW_CONFIG")

        path = cls._resolve_path(config_path)
        raw = cls._read_yaml(path)
        raw = _substitute_env_vars(raw)
        cls._validate(raw)
        return cls._parse(raw)

    @classmethod
    def _validate(cls, raw: dict) -> None:
        """Validate raw config dict against the Pydantic schema."""
        from pydantic import ValidationError

        try:
            VoiceGatewayConfig.model_validate(raw)
        except ValidationError as e:
            lines = ["Configuration validation failed:"]
            for err in e.errors():
                loc = ".".join(str(p) for p in err["loc"])
                msg = err["msg"]
                lines.append(f"  - {loc}: {msg}")
            lines.append("")
            lines.append("Check your voicegw.yaml for typos or invalid values.")
            raise ConfigError("\n".join(lines)) from None

    @classmethod
    def _resolve_path(cls, config_path: str | Path | None) -> Path:
        if config_path is not None:
            p = Path(config_path)
            if not p.exists():
                raise ConfigError(f"Config file not found: {p}")
            return p

        for p in _CONFIG_PATHS:
            if p.exists():
                return p

        raise ConfigError("No voicegw.yaml found. Create one with: voicegw init")

    @classmethod
    def _read_yaml(cls, path: Path) -> dict:
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigError(f"Invalid YAML in {path}: {e}") from e

        if not isinstance(data, dict):
            raise ConfigError(f"Config file must be a YAML mapping, got {type(data)}")
        return data

    @classmethod
    def _parse(cls, raw: dict) -> GatewayConfig:
        projects_raw = raw.get("projects", {}) or {}
        projects: dict[str, ProjectConfig] = {}
        if isinstance(projects_raw, dict):
            for pid, pcfg in projects_raw.items():
                if not isinstance(pcfg, dict):
                    continue
                project_providers_raw = pcfg.get("providers") or {}
                project_providers: dict[str, dict[str, Any]] = {}
                if isinstance(project_providers_raw, dict):
                    for prov_name, prov_cfg in project_providers_raw.items():
                        if isinstance(prov_cfg, dict):
                            project_providers[prov_name] = dict(prov_cfg)
                metrics_raw = pcfg.get("metrics") or {}
                metrics_cfg = MetricsConfig(
                    dead_air_threshold_seconds=float(
                        metrics_raw.get("dead_air_threshold_seconds", 3.0)
                    ),
                    talk_over_min_overlap_ms=int(
                        metrics_raw.get("talk_over_min_overlap_ms", 100)
                    ),
                    turn_buffer_flush_size=int(
                        metrics_raw.get("turn_buffer_flush_size", 25)
                    ),
                )
                replay_raw = pcfg.get("replay") or {}
                replay_cfg = ReplayConfig(
                    enabled=bool(replay_raw.get("enabled", True)),
                    retention_days=int(replay_raw.get("retention_days", 90)),
                    buffer_size_events=int(replay_raw.get("buffer_size_events", 5000)),
                    flush_size_events=int(replay_raw.get("flush_size_events", 500)),
                )
                routing_raw = pcfg.get("routing") or {}
                rosters_raw = routing_raw.get("rosters") or {}
                rosters: dict[str, list[str]] = {}
                if isinstance(rosters_raw, dict):
                    for modality, providers in rosters_raw.items():
                        if isinstance(providers, list):
                            rosters[str(modality)] = [str(p) for p in providers]
                routing_cfg = RoutingConfig(
                    budget_ms=int(routing_raw.get("budget_ms", 1500)),
                    rosters=rosters,
                    fallback_to_fastest=bool(
                        routing_raw.get("fallback_to_fastest", True)
                    ),
                )
                branding_raw = pcfg.get("branding") or {}
                branding_cfg = BrandingConfig(
                    logo_url=(
                        str(branding_raw["logo_url"])
                        if branding_raw.get("logo_url")
                        else None
                    ),
                    accent_color=(
                        str(branding_raw["accent_color"])
                        if branding_raw.get("accent_color")
                        else None
                    ),
                    product_name=(
                        str(branding_raw["product_name"])
                        if branding_raw.get("product_name")
                        else None
                    ),
                )
                projects[pid] = ProjectConfig(
                    id=pid,
                    name=str(pcfg.get("name") or pid),
                    description=str(pcfg.get("description") or ""),
                    default_stack=str(pcfg.get("default_stack") or ""),
                    daily_budget=float(pcfg.get("daily_budget", 0.0) or 0.0),
                    budget_action=str(pcfg.get("budget_action") or "warn"),
                    tags=list(pcfg.get("tags") or []),
                    providers=project_providers,
                    metrics=metrics_cfg,
                    replay=replay_cfg,
                    routing=routing_cfg,
                    branding=branding_cfg,
                )

        auth_raw = raw.get("auth", {}) or {}
        auth = AuthConfig(
            api_keys=[
                dict(entry)
                for entry in (auth_raw.get("api_keys") or [])
                if isinstance(entry, dict)
            ],
            cors_origins=[str(o) for o in (auth_raw.get("cors_origins") or []) if o],
        )

        default_project_raw = raw.get("default_project")
        default_project = str(default_project_raw) if default_project_raw else None

        return cls(
            providers=raw.get("providers", {}) or {},
            models=raw.get("models", {}) or {},
            fallbacks=raw.get("fallbacks", {}) or {},
            cost_tracking=raw.get("cost_tracking", {}) or {},
            latency=raw.get("latency", {}) or {},
            rate_limits=raw.get("rate_limits", {}) or {},
            dashboard=raw.get("dashboard", {}) or {},
            serve=raw.get("serve", {}) or {},
            projects=projects,
            default_project=default_project,
            stacks=raw.get("stacks", {}) or {},
            auth=auth,
            observability=raw.get(
                "observability",
                {
                    "latency_tracking": True,
                    "cost_tracking": True,
                    "request_logging": True,
                },
            ),
            ingest=IngestConfig.model_validate(raw.get("ingest") or {}),
            retention=RetentionConfig.model_validate(raw.get("retention") or {}),
            workers=WorkersConfig.model_validate(raw.get("workers") or {}),
            clickhouse=ClickHouseConfig.model_validate(raw.get("clickhouse") or {}),
            rate_card=raw.get("rate_card", {}) or {},
        )

    def get_provider_config(self, provider_name: str) -> dict[str, Any]:
        """Get the top-level (global) configuration for a provider."""
        return self.providers.get(provider_name, {})

    def get_provider_config_for_project(
        self, provider_name: str, project_id: str | None
    ) -> dict[str, Any]:
        """Resolve provider config with project-level precedence."""
        if project_id and project_id in self.projects:
            project = self.projects[project_id]
            if provider_name in project.providers:
                return dict(project.providers[provider_name])
        return self.providers.get(provider_name, {})

    def get_model_config(self, modality: str, model_key: str) -> dict[str, Any] | None:
        """Get configuration for a specific model."""
        modality_models = self.models.get(modality, {})
        return modality_models.get(model_key)

    def get_project(self, project_id: str) -> ProjectConfig | None:
        """Return a project by id, or None if not configured."""
        return self.projects.get(project_id)
