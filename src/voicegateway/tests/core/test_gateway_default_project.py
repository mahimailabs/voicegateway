"""Tests for the v0.0.5 auto-created ``default`` project."""

from __future__ import annotations

import yaml

from voicegateway.core import active_project as project
from voicegateway.core import gateway_factory as factory
from voicegateway.core.active_project import get_active_project
from voicegateway.core.gateway import Gateway


def _write_config(tmp_path, **overrides):
    cfg = {
        "providers": {"openai": {"api_key": "k"}},
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "stacks": {},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
        "cost_tracking": {"enabled": False},
        "observability": {"latency_tracking": True},
    }
    cfg.update(overrides)
    path = tmp_path / "voicegw.yaml"
    path.write_text(yaml.dump(cfg))
    return str(path)


# ---------------------------------------------------------------------------
# Empty YAML
# ---------------------------------------------------------------------------


def test_empty_yaml_storage_disabled_inserts_in_memory(tmp_path):
    cfg_path = _write_config(tmp_path)
    gw = Gateway(config_path=cfg_path)

    assert "default" in gw.config.projects
    project = gw.config.projects["default"]
    assert project.id == "default"
    assert project.name == "Default"
    assert project.source == "auto"


async def test_empty_yaml_storage_enabled_persists_managed_row(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "vg.db"))
    cfg_path = _write_config(tmp_path, cost_tracking={"enabled": True})
    gw = Gateway(config_path=cfg_path)

    # Project shows up in the merged config.
    assert "default" in gw.config.projects
    assert gw.config.projects["default"].source == "db"

    # ...and a row was actually written to managed_projects.
    rows = await gw.storage.list_managed_projects()
    by_id = {r["project_id"]: r for r in rows}
    assert "default" in by_id
    assert by_id["default"]["name"] == "Default"
    assert by_id["default"]["budget_action"] == "warn"
    assert by_id["default"]["daily_budget"] == 0.0


# ---------------------------------------------------------------------------
# YAML with other projects but no default_project
# ---------------------------------------------------------------------------


def test_yaml_projects_without_default_project_still_get_default(tmp_path):
    """A user who configures ``projects: {tony, mama}`` without a"""
    cfg_path = _write_config(
        tmp_path,
        projects={
            "tony-pizza": {"name": "Tony"},
            "mama-diner": {"name": "Mama"},
        },
    )
    gw = Gateway(config_path=cfg_path)

    assert "tony-pizza" in gw.config.projects
    assert "mama-diner" in gw.config.projects
    assert "default" in gw.config.projects
    assert gw.config.projects["default"].source == "auto"


def test_config_falls_through_to_global_key_for_default_project(tmp_path, monkeypatch):
    """Config resolution: with ``projects: {tony}`` and no per-project key for
    ``default``, ``get_provider_config_for_project`` on the default project falls
    through to the global provider key.
    """
    cfg_path = _write_config(
        tmp_path,
        projects={
            "tony-pizza": {
                "name": "Tony",
                "providers": {"openai": {"api_key": "tony-only"}},
            },
        },
        providers={"openai": {"api_key": "global-fallback"}},
    )
    gw = Gateway(config_path=cfg_path)
    monkeypatch.setattr(factory, "_gateway", gw)
    monkeypatch.delenv("VOICEGW_ACTIVE_PROJECT", raising=False)
    project.reset_project()

    assert get_active_project() == "default"
    resolved = gw.config.get_provider_config_for_project("openai", "default")
    assert resolved is not None
    assert resolved.get("api_key") == "global-fallback"


# ---------------------------------------------------------------------------
# YAML pre-configures a "default" project (no auto-create)
# ---------------------------------------------------------------------------


def test_yaml_default_project_preserved(tmp_path):
    """When the user explicitly defines ``projects.default`` in YAML,"""
    cfg_path = _write_config(
        tmp_path,
        projects={
            "default": {
                "name": "My Custom Default",
                "description": "User-defined.",
                "daily_budget": 5.00,
                "budget_action": "block",
                "tags": ["custom"],
            },
        },
    )
    gw = Gateway(config_path=cfg_path)

    proj = gw.config.projects["default"]
    assert proj.name == "My Custom Default"
    assert proj.description == "User-defined."
    assert proj.daily_budget == 5.00
    assert proj.budget_action == "block"
    assert proj.source == "yaml"


async def test_db_default_project_preserved(tmp_path, monkeypatch):
    """When a managed_projects row already exists for ``default``,"""
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "vg.db"))
    cfg_path = _write_config(tmp_path, cost_tracking={"enabled": True})

    # Pre-seed a managed_projects row before constructing the Gateway.
    from voicegateway.services.storage_service import StorageService

    storage = StorageService(str(tmp_path / "vg.db"))
    await storage.upsert_managed_project(
        project_id="default",
        name="Pre-seeded Default",
        description="Already in storage.",
        daily_budget=2.5,
        budget_action="throttle",
    )

    gw = Gateway(config_path=cfg_path)

    proj = gw.config.projects["default"]
    assert proj.name == "Pre-seeded Default"
    # The pre-seeded row's daily_budget survives the auto-create no-op.
    rows = await gw.storage.list_managed_projects()
    by_id = {r["project_id"]: r for r in rows}
    assert by_id["default"]["daily_budget"] == 2.5
    assert by_id["default"]["budget_action"] == "throttle"
