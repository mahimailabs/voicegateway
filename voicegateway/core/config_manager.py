"""Merges configuration from YAML, SQLite managed tables, and env vars.

Priority: ENV > SQLite (managed) > YAML (base).
"""

from __future__ import annotations

import copy
import json
import logging
from typing import TYPE_CHECKING, Any

from voicegateway.core.config import GatewayConfig, ProjectConfig
from voicegateway.core.crypto import decrypt

if TYPE_CHECKING:
    from voicegateway.storage.sqlite import SQLiteStorage

_logger = logging.getLogger(__name__)


class ConfigManager:
    """Merge YAML config with managed_* SQLite tables at startup and after writes."""

    def __init__(self, yaml_config: GatewayConfig, storage: SQLiteStorage | None):
        self._yaml = yaml_config
        self._storage = storage

    async def load_merged(self) -> GatewayConfig:
        """Return a GatewayConfig with managed_* rows merged in."""
        # Deep copy the YAML config so we don't mutate the original
        merged = copy.deepcopy(self._yaml)

        if self._storage is None:
            return merged

        # Layer in managed projects FIRST. Provider-scoped rows below
        # may reference a project that exists only in the DB; running
        # this loop first means the providers loop can attach to a
        # fully-populated ProjectConfig (name / description / budget /
        # tags) instead of a stub that swallows the real metadata.
        for row in await self._storage.list_managed_projects():
            pid = row["project_id"]
            if pid in merged.projects:
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
            )

        # Layer in managed providers.
        # Project-scoped rows (v0.0.5+, written by ``vg_add_provider``)
        # carry a non-null ``project`` column and merge into
        # ``merged.projects[<name>].providers[<provider_type>]`` so the
        # inference resolver finds them via
        # ``GatewayConfig.get_provider_config_for_project``. Legacy
        # global-scope rows (``project IS NULL``) keep their previous
        # behavior: they layer into ``merged.providers[<provider_id>]``.
        # YAML always wins on conflict, on either branch.
        for row in await self._storage.list_managed_providers():
            pid = row["provider_id"]
            try:
                plaintext_key = decrypt(row.get("api_key_encrypted", ""))
            except ValueError:
                _logger.warning(
                    "Failed to decrypt key for provider '%s', skipping", pid
                )
                plaintext_key = ""
            # Reserved keys (api_key / base_url / _source) MUST come
            # from the dedicated row columns, not from extra_config.
            # Spreading extra_config first and then overwriting prevents
            # a malformed extra_config from masking the encrypted key
            # path or the "db" source tag.
            provider_cfg: dict[str, Any] = dict(row.get("extra_config") or {})
            provider_cfg["api_key"] = plaintext_key
            provider_cfg["base_url"] = row.get("base_url")
            provider_cfg["_source"] = "db"
            project_name = row.get("project")
            if project_name:
                provider_type = row["provider_type"]
                # Stub a ProjectConfig only when the project doesn't
                # exist in YAML or in managed_projects; the
                # managed_projects loop above ran first, so a stub here
                # genuinely means "DB has providers for this project
                # but no metadata row for it yet."
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

        # Layer in managed models
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
