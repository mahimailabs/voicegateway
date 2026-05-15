"""Tests for the v0.0.5 active-project resolution order."""

from __future__ import annotations

import asyncio
import contextvars

import pytest
import yaml

from voicegateway.core import gateway_factory as factory
from voicegateway.core import active_project as project
from voicegateway.core.active_project import (
    get_active_project,
    reset_project,
    set_project,
)


def _build_gateway(tmp_path, *, projects: dict, default_project: str | None = None):
    cfg: dict = {
        "providers": {"openai": {"api_key": "k"}},
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "stacks": {},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
        "cost_tracking": {"enabled": False},
        "observability": {"latency_tracking": True},
    }
    if projects is not None:
        cfg["projects"] = projects
    if default_project:
        cfg["default_project"] = default_project
    config_path = tmp_path / "voicegw.yaml"
    config_path.write_text(yaml.dump(cfg))

    from voicegateway.core.gateway import Gateway

    return Gateway(config_path=str(config_path))


@pytest.fixture
def empty_projects_gw(tmp_path, monkeypatch):
    """A gateway whose voicegw.yaml has NO projects configured."""
    gw = _build_gateway(tmp_path, projects={})
    monkeypatch.setattr(factory, "_gateway", gw)
    return gw


@pytest.fixture
def projects_no_default_gw(tmp_path, monkeypatch):
    """A gateway with projects but no default_project configured."""
    gw = _build_gateway(
        tmp_path,
        projects={
            "tony-pizza": {"name": "Tony"},
            "mama-diner": {"name": "Mama"},
        },
        default_project=None,
    )
    monkeypatch.setattr(factory, "_gateway", gw)
    return gw


@pytest.fixture
def projects_with_default_gw(tmp_path, monkeypatch):
    """A gateway with projects AND default_project: tony-pizza."""
    gw = _build_gateway(
        tmp_path,
        projects={
            "tony-pizza": {"name": "Tony"},
            "mama-diner": {"name": "Mama"},
        },
        default_project="tony-pizza",
    )
    monkeypatch.setattr(factory, "_gateway", gw)
    return gw


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.delenv("VOICEGW_ACTIVE_PROJECT", raising=False)
    reset_project()
    yield
    reset_project()


# ---------------------------------------------------------------------------
# Step 1: explicit set_project()
# ---------------------------------------------------------------------------


def test_set_project_wins_over_everything(monkeypatch, projects_with_default_gw):
    monkeypatch.setenv("VOICEGW_ACTIVE_PROJECT", "from-env")

    def _scenario():
        set_project("from-set-project")
        return get_active_project()

    result = contextvars.copy_context().run(_scenario)
    assert result == "from-set-project"


def test_set_project_validates_input():
    with pytest.raises(ValueError):
        set_project("")
    with pytest.raises(ValueError):
        set_project(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Step 2: env var
# ---------------------------------------------------------------------------


def test_env_var_wins_over_yaml_default(monkeypatch, projects_with_default_gw):
    monkeypatch.setenv("VOICEGW_ACTIVE_PROJECT", "from-env")
    assert get_active_project() == "from-env"


def test_env_var_used_when_no_set_project(monkeypatch, empty_projects_gw):
    monkeypatch.setenv("VOICEGW_ACTIVE_PROJECT", "from-env")
    assert get_active_project() == "from-env"


# ---------------------------------------------------------------------------
# Step 3: yaml default_project
# ---------------------------------------------------------------------------


def test_yaml_default_project_used_when_no_override(projects_with_default_gw):
    assert get_active_project() == "tony-pizza"


# ---------------------------------------------------------------------------
# Step 4: fallback to the auto-created "default" project
# ---------------------------------------------------------------------------


def test_fallback_when_projects_configured_without_default(
    projects_no_default_gw,
):
    """A user who configures ``projects:`` without ``default_project``"""
    assert get_active_project() == "default"


def test_fallback_when_no_projects_configured(empty_projects_gw):
    """Fresh install with no projects: block. Gateway init creates"""
    assert get_active_project() == "default"


# ---------------------------------------------------------------------------
# ContextVar isolation
# ---------------------------------------------------------------------------


async def test_set_project_isolated_per_context(projects_with_default_gw):
    """Two independently-copied contexts each manage their own"""

    async def _ctx_a():
        set_project("from-ctx-a")
        return get_active_project()

    async def _ctx_b():
        set_project("from-ctx-b")
        return get_active_project()

    task_a = asyncio.create_task(_ctx_a(), context=contextvars.copy_context())
    task_b = asyncio.create_task(_ctx_b(), context=contextvars.copy_context())
    a, b = await asyncio.gather(task_a, task_b)
    assert a == "from-ctx-a"
    assert b == "from-ctx-b"
    # The outer test context never had set_project called, so it
    # falls back to the YAML default.
    assert get_active_project() == "tony-pizza"


def test_reset_project_clears_set_project(projects_with_default_gw):
    """``reset_project`` returns the resolver to step-2 / step-3."""

    def _scenario():
        set_project("from-set-project")
        assert get_active_project() == "from-set-project"
        reset_project()
        return get_active_project()

    # After reset, falls through to YAML default.
    assert contextvars.copy_context().run(_scenario) == "tony-pizza"


# ---------------------------------------------------------------------------
# Module wiring
# ---------------------------------------------------------------------------


def test_inference_package_re_exports_set_and_get_active_project():
    """``voicegateway.inference.set_project`` and"""
    from voicegateway import inference

    assert inference.set_project is project.set_project
    assert inference.get_active_project is project.get_active_project
    assert "set_project" in inference.__all__
    assert "get_active_project" in inference.__all__
