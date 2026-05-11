"""YAML config loader with environment variable substitution."""

from __future__ import annotations

import os
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")

# Preferred (new) search paths — voicegw.yaml first.
_NEW_CONFIG_PATHS = [
    Path("./voicegw.yaml"),
    Path.home() / ".config" / "voicegateway" / "voicegw.yaml",
    Path("/etc/voicegateway/voicegw.yaml"),
]

# Legacy search paths — still honoured with a deprecation warning.
_LEGACY_CONFIG_PATHS = [
    Path("./gateway.yaml"),
    Path.home() / ".config" / "inference-gateway" / "gateway.yaml",
    Path("/etc/inference-gateway/gateway.yaml"),
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
    """v0.2.0 voice-conversation metrics knobs.

    Defaults match the Foundry-locked values (Open Questions 2 and 3).
    Per-project overridable via the ``metrics:`` block in
    ``voicegw.yaml``. ``MetricsConfig()`` with no args produces the
    canonical configuration that REQ-VG-METRICS-002, -003, -004 wire
    against.
    """

    dead_air_threshold_seconds: float = 3.0
    talk_over_min_overlap_ms: int = 100
    turn_buffer_flush_size: int = 25


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
    # v0.0.5: per-project provider keys. When a project entry sets
    # ``providers:`` in voicegw.yaml, those keys win over the top-level
    # ``providers:`` block for inference factory calls inside this
    # project's context. See design.md section 3.3.
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    # v0.2.0: per-project metric capture knobs (REQ-VG-METRICS-001..006).
    metrics: MetricsConfig = field(default_factory=MetricsConfig)

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
    """HTTP API authentication settings.

    ``api_keys`` is kept as a list of raw dicts (parsed but unresolved)
    — ``voicegateway.core.auth.load_api_keys`` turns them into concrete
    ``ApiKey`` instances, skipping entries with empty tokens (e.g. when
    ``${VOICEGW_API_KEY}`` isn't set).
    """

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
    default_project: str | None = None  # v0.0.5: see design.md section 3.3
    stacks: dict[str, dict[str, str]] = field(default_factory=dict)
    auth: AuthConfig = field(default_factory=AuthConfig)
    observability: dict[str, Any] = field(
        default_factory=lambda: {
            "latency_tracking": True,
            "cost_tracking": True,
            "request_logging": True,
        }
    )

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> GatewayConfig:
        """Load configuration from a YAML file.

        Args:
            config_path: Explicit path to config file. If None, searches
                default locations (voicegw.yaml first, then legacy gateway.yaml).

        Returns:
            Parsed GatewayConfig.

        Raises:
            ConfigError: If no config file found or config is invalid.
        """
        # Allow VOICEGW_CONFIG env var to override if no explicit path given
        if config_path is None:
            config_path = os.environ.get("VOICEGW_CONFIG") or os.environ.get(
                "INFERENCE_GATEWAY_CONFIG"
            )
            if os.environ.get("INFERENCE_GATEWAY_CONFIG") and not os.environ.get(
                "VOICEGW_CONFIG"
            ):
                warnings.warn(
                    "INFERENCE_GATEWAY_CONFIG is deprecated; use VOICEGW_CONFIG instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )

        path = cls._resolve_path(config_path)
        raw = cls._read_yaml(path)
        raw = _substitute_env_vars(raw)
        cls._validate(raw)
        return cls._parse(raw)

    @classmethod
    def _validate(cls, raw: dict) -> None:
        """Validate raw config dict against the Pydantic schema."""
        from pydantic import ValidationError

        from voicegateway.core.schema import VoiceGatewayConfig

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

        for p in _NEW_CONFIG_PATHS:
            if p.exists():
                return p

        for p in _LEGACY_CONFIG_PATHS:
            if p.exists():
                warnings.warn(
                    f"Using legacy config path {p}. Rename to 'voicegw.yaml' and "
                    f"move to ~/.config/voicegateway/ to silence this warning.",
                    DeprecationWarning,
                    stacklevel=3,
                )
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
        )

    def get_provider_config(self, provider_name: str) -> dict[str, Any]:
        """Get the top-level (global) configuration for a provider.

        For project-aware lookups (the v0.0.5 inference path), use
        ``get_provider_config_for_project`` instead.
        """
        return self.providers.get(provider_name, {})

    def get_provider_config_for_project(
        self, provider_name: str, project_id: str | None
    ) -> dict[str, Any]:
        """Resolve provider config with project-level precedence.

        Returns the project's own provider entry if set; otherwise
        falls back to the top-level ``providers`` block (backward
        compat for pre-v0.0.5 configs that don't carry per-project
        keys). When neither exists, returns an empty dict.
        """
        if project_id and project_id in self.projects:
            project = self.projects[project_id]
            if provider_name in project.providers:
                return dict(project.providers[provider_name])
        return self.providers.get(provider_name, {})

    def get_model_config(self, modality: str, model_key: str) -> dict[str, Any] | None:
        """Get configuration for a specific model.

        Args:
            modality: "stt", "llm", or "tts"
            model_key: Model key like "deepgram/nova-3"

        Returns:
            Model config dict or None if not found.
        """
        modality_models = self.models.get(modality, {})
        return modality_models.get(model_key)

    def get_project(self, project_id: str) -> ProjectConfig | None:
        """Return a project by id, or None if not configured."""
        return self.projects.get(project_id)
