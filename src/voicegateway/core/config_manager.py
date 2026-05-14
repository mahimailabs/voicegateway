"""Merges configuration from YAML, SQLite managed tables, and env vars."""

from __future__ import annotations

import copy
import json
import logging
from typing import TYPE_CHECKING, Any

from voicegateway.core.config import GatewayConfig, ProjectConfig
from voicegateway.core.crypto import decrypt
from voicegateway.schemas.guardrail_policy_schema import GuardrailPolicy

if TYPE_CHECKING:
    from voicegateway.services.storage_service import StorageService

_logger = logging.getLogger(__name__)


class ConfigManager:
    """Merge YAML config with managed_* SQLite tables at startup and after writes."""

    def __init__(self, yaml_config: GatewayConfig, storage: StorageService | None):
        self._yaml = yaml_config
        self._storage = storage

    async def load_merged(self) -> GatewayConfig:
        """Return a GatewayConfig with managed_* rows merged in."""
        merged = copy.deepcopy(self._yaml)

        if self._storage is None:
            return merged

        for row in await self._storage.list_managed_projects():
            pid = row["project_id"]
            if pid in merged.projects:
                if row.get("guardrail_policy") is not None:
                    merged.projects[pid].guardrails = GuardrailPolicy.from_raw(
                        row["guardrail_policy"]
                    )
                continue
            tags_raw = row.get("tags")
            if isinstance(tags_raw, str):
                try:
                    tags = json.loads(tags_raw)
                except (json.JSONDecodeError, ValueError):
                    tags = []
            elif isinstance(tags_raw, list):
                tags = tags_raw
            else:
                tags = []
            merged.projects[pid] = ProjectConfig(
                id=pid,
                name=row["name"],
                description=row.get("description", ""),
                daily_budget=float(row.get("daily_budget", 0.0) or 0.0),
                budget_action=str(row.get("budget_action") or "warn"),
                default_stack=str(row.get("default_stack") or ""),
                tags=tags,
                source="db",
                guardrails=GuardrailPolicy.from_raw(row.get("guardrail_policy")),
            )

        for row in await self._storage.list_managed_providers():
            pid = row["provider_id"]
            try:
                plaintext_key = decrypt(row.get("api_key_encrypted", ""))
            except ValueError:
                _logger.warning(
                    "Failed to decrypt key for provider '%s', skipping", pid
                )
                plaintext_key = ""

            provider_cfg: dict[str, Any] = dict(row.get("extra_config") or {})
            provider_cfg["api_key"] = plaintext_key
            provider_cfg["base_url"] = row.get("base_url")
            provider_cfg["_source"] = "db"
            project_name = row.get("project")
            if project_name:
                provider_type = row["provider_type"]

                if project_name not in merged.projects:
                    merged.projects[project_name] = ProjectConfig(
                        id=project_name,
                        name=project_name,
                        source="db",
                    )
                project = merged.projects[project_name]
                if provider_type in project.providers:
                    continue  # YAML per-project entry wins.
                project.providers[provider_type] = provider_cfg
            else:
                if pid in merged.providers:
                    continue  # YAML top-level provider wins.
                merged.providers[pid] = provider_cfg

        for row in await self._storage.list_managed_models():
            mid = row["model_id"]
            modality = row["modality"]
            modality_bucket: dict[str, Any] = merged.models.setdefault(modality, {})
            if mid in modality_bucket:
                continue
            if not row.get("enabled", True):
                continue
            modality_bucket[mid] = {
                "provider": row["provider_id"],
                "model": row["model_name"],
                "_source": "db",
                **(
                    {"default_voice": row["default_voice"]}
                    if row.get("default_voice")
                    else {}
                ),
                **(
                    {"default_language": row["default_language"]}
                    if row.get("default_language")
                    else {}
                ),
            }

        _logger.debug(
            "Config merged: %d providers, %d projects (YAML + DB)",
            len(merged.providers),
            len(merged.projects),
        )
        return merged

    async def refresh(self) -> GatewayConfig:
        """Reload managed resources from SQLite. Used after a write."""
        return await self.load_merged()
