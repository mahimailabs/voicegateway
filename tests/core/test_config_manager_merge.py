"""Tests for ``ConfigManager.load_merged`` ordering invariants.

The merge loop must run managed_projects BEFORE managed_providers so a
project-scoped provider write doesn't blank the DB-managed project's
metadata (name, description, daily_budget, tags, budget_action). Also
guards the reserved-keys-vs-extra_config precedence: api_key /
base_url / _source must always come from the dedicated columns.
"""

from __future__ import annotations

import pytest
import yaml as _yaml

from voicegateway.core.gateway import Gateway


@pytest.fixture
def yaml_no_projects(tmp_path):
    cfg = {
        "providers": {"openai": {"api_key": "global"}},
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "stacks": {},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
        "cost_tracking": {"enabled": True},
        "observability": {"latency_tracking": True},
    }
    cfg_path = tmp_path / "voicegw.yaml"
    cfg_path.write_text(_yaml.dump(cfg))
    return str(cfg_path)


async def test_managed_project_metadata_survives_provider_scoped_write(
    yaml_no_projects, tmp_path, monkeypatch
):
    """Reproduce the codex P2: a managed_projects row with rich
    metadata must not be replaced by a stub when a managed_providers
    row references the same project.
    """
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "merge.db"))
    gw = Gateway(config_path=yaml_no_projects)
    storage = gw.storage
    assert storage is not None

    await storage.upsert_managed_project(
        project_id="dental-bot",
        name="Dental Bot",
        description="The friendly assistant",
        daily_budget=12.5,
        budget_action="block",
        tags=["voice", "production"],
    )
    await storage.upsert_managed_provider(
        provider_id="dental-bot:openai",
        provider_type="openai",
        api_key="sk-dental",
        project="dental-bot",
    )
    await gw.refresh_config()

    project = gw.config.projects["dental-bot"]
    assert project.name == "Dental Bot"
    assert project.description == "The friendly assistant"
    assert project.daily_budget == 12.5
    assert project.budget_action == "block"
    assert sorted(project.tags) == ["production", "voice"]
    assert "openai" in project.providers
    assert project.providers["openai"]["api_key"] == "sk-dental"
    assert project.providers["openai"]["_source"] == "db"


async def test_db_only_project_with_no_metadata_row_falls_back_to_stub(
    yaml_no_projects, tmp_path, monkeypatch
):
    """When the DB only has a project-scoped provider row but no
    matching managed_projects row, the stub kicks in: the project
    appears with name == project_id and source == 'db'.
    """
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "merge-stub.db"))
    gw = Gateway(config_path=yaml_no_projects)
    storage = gw.storage
    assert storage is not None

    await storage.upsert_managed_provider(
        provider_id="orphan:openai",
        provider_type="openai",
        api_key="sk-orphan",
        project="orphan",
    )
    await gw.refresh_config()

    project = gw.config.projects["orphan"]
    assert project.name == "orphan"
    assert project.source == "db"
    assert project.providers["openai"]["api_key"] == "sk-orphan"


async def test_extra_config_cannot_override_reserved_keys(
    yaml_no_projects, tmp_path, monkeypatch
):
    """``extra_config`` is a freeform JSON column. A malformed entry
    that names ``api_key`` / ``base_url`` / ``_source`` must NOT win
    over the dedicated columns — those carry the encrypted-key path.
    """
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "merge-extra.db"))
    gw = Gateway(config_path=yaml_no_projects)
    storage = gw.storage
    assert storage is not None

    await storage.upsert_managed_provider(
        provider_id="standalone-openai",
        provider_type="openai",
        api_key="sk-canonical",
        base_url="https://canonical.example",
        extra_config={
            "api_key": "sk-EVIL",
            "base_url": "https://evil.example",
            "_source": "yaml",
            "organization": "org-real",
        },
        project=None,
    )
    await gw.refresh_config()

    cfg = gw.config.providers["standalone-openai"]
    assert cfg["api_key"] == "sk-canonical"
    assert cfg["base_url"] == "https://canonical.example"
    assert cfg["_source"] == "db"
    # Non-reserved keys still flow through.
    assert cfg["organization"] == "org-real"


async def test_managed_guardrails_overlay_yaml_project(
    yaml_no_projects, tmp_path, monkeypatch
):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "guardrails-overlay.db"))
    gw = Gateway(config_path=yaml_no_projects)
    storage = gw.storage
    assert storage is not None

    await storage.upsert_managed_project(
        project_id="default",
        name="Default",
        guardrail_policy={
            "enabled": True,
            "categories": {"pii": "redact", "medical": "alert"},
        },
    )
    await gw.refresh_config()

    policy = gw.config.projects["default"].guardrails
    assert policy.is_active is True
    assert policy.active_categories == {"pii": "redact", "medical": "alert"}
