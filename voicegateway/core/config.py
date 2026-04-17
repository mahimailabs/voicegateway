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
class ProjectConfig:
    """Configuration for a single project."""
    id: str
    name: str
    description: str = ""
    default_stack: str = ""
    daily_budget: float = 0.0
    budget_action: str = "warn"
    tags: list[str] = field(default_factory=list)

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
class GatewayConfig:
    """Parsed VoiceGateway configuration."""

    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    models: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    fallbacks: dict[str, list[str]] = field(default_factory=dict)
    cost_tracking: dict[str, Any] = field(default_factory=dict)
    latency: dict[str, Any] = field(default_factory=dict)
    rate_limits: dict[str, dict[str, Any]] = field(default_factory=dict)
    dashboard: dict[str, Any] = field(default_factory=dict)
    projects: dict[str, ProjectConfig] = field(default_factory=dict)
    stacks: dict[str, dict[str, str]] = field(default_factory=dict)
    observability: dict[str, Any] = field(default_factory=lambda: {
        "latency_tracking": True,
        "cost_tracking": True,
        "request_logging": True,
    })

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
            config_path = os.environ.get("VOICEGW_CONFIG") or os.environ.get("INFERENCE_GATEWAY_CONFIG")
            if os.environ.get("INFERENCE_GATEWAY_CONFIG") and not os.environ.get("VOICEGW_CONFIG"):
                warnings.warn(
                    "INFERENCE_GATEWAY_CONFIG is deprecated; use VOICEGW_CONFIG instead.",
                    DeprecationWarning, stacklevel=2,
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
                    DeprecationWarning, stacklevel=3,
                )
                return p

        raise ConfigError(
            "No voicegw.yaml found. Create one with: voicegw init"
        )

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
                projects[pid] = ProjectConfig(
                    id=pid,
                    name=str(pcfg.get("name") or pid),
                    description=str(pcfg.get("description") or ""),
                    default_stack=str(pcfg.get("default_stack") or ""),
                    daily_budget=float(pcfg.get("daily_budget", 0.0) or 0.0),
                    budget_action=str(pcfg.get("budget_action") or "warn"),
                    tags=list(pcfg.get("tags") or []),
                )

        return cls(
            providers=raw.get("providers", {}) or {},
            models=raw.get("models", {}) or {},
            fallbacks=raw.get("fallbacks", {}) or {},
            cost_tracking=raw.get("cost_tracking", {}) or {},
            latency=raw.get("latency", {}) or {},
            rate_limits=raw.get("rate_limits", {}) or {},
            dashboard=raw.get("dashboard", {}) or {},
            projects=projects,
            stacks=raw.get("stacks", {}) or {},
            observability=raw.get("observability", {
                "latency_tracking": True,
                "cost_tracking": True,
                "request_logging": True,
            }),
        )

    def get_provider_config(self, provider_name: str) -> dict[str, Any]:
        """Get configuration for a specific provider."""
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
