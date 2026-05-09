"""Tests for the v0.0.5 auto-created ``default`` project.

Item 2 of the v0.0.5 punch list: every Gateway init must populate a
``"default"`` project so the inference resolver always has something
to charge against. With storage enabled the row goes into the
``managed_projects`` table and shows up as ``source="db"`` after the
merge. Without storage the row is added to the in-memory
``GatewayConfig.projects`` map as ``source="auto"``. When YAML or the
DB already configures a ``default`` project, that takes precedence;
the auto-create is a no-op.
"""

from __future__ import annotations

import yaml

from voicegateway.core.gateway import Gateway
from voicegateway.inference import _factory, _project, _stt
from voicegateway.inference._project import get_active_project


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


async def test_empty_yaml_storage_enabled_persists_managed_row(
    tmp_path, monkeypatch
):
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
    """A user who configures ``projects: {tony, mama}`` without a
    ``default_project`` field gets the auto-created ``default``
    alongside their projects. The resolver falls through to it when
    no override is set.
    """
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


def test_inference_falls_through_to_default_with_named_projects(
    tmp_path, monkeypatch
):
    """End-to-end check: with ``projects: {tony}`` and no override,
    ``inference.STT(...)`` resolves to ``"default"`` and uses the
    top-level provider key, not Tony's per-project one.
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
    monkeypatch.setattr(_factory, "_gateway", gw)
    monkeypatch.delenv("VOICEGW_ACTIVE_PROJECT", raising=False)
    _project.reset_project()

    captured: dict = {}

    class _FakeProvider:
        def __init__(self, config):
            captured.update(config)

        def create_stt(self, model, **kwargs):
            return object()

        def create_llm(self, model, **kwargs):
            return object()

        def create_tts(self, model, voice=None, **kwargs):
            return object()

        async def health_check(self):
            return True

    monkeypatch.setattr(
        _stt, "create_provider", lambda _name, config: _FakeProvider(config)
    )

    assert get_active_project() == "default"
    _stt.STT("openai/whisper-1")
    assert captured["api_key"] == "global-fallback"


# ---------------------------------------------------------------------------
# YAML pre-configures a "default" project (no auto-create)
# ---------------------------------------------------------------------------


def test_yaml_default_project_preserved(tmp_path):
    """When the user explicitly defines ``projects.default`` in YAML,
    Gateway init must NOT overwrite it with the auto-created stub.
    """
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
    """When a managed_projects row already exists for ``default``,
    Gateway init must NOT overwrite it.
    """
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "vg.db"))
    cfg_path = _write_config(tmp_path, cost_tracking={"enabled": True})

    # Pre-seed a managed_projects row before constructing the Gateway.
    from voicegateway.storage.sqlite import SQLiteStorage

    storage = SQLiteStorage(str(tmp_path / "vg.db"))
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
