"""Tests for DB-managed per-project provider key resolution."""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from voicegateway.inference import factory, llm, project, stt, tts


class _FakeProvider:
    """Records the config dict its constructor receives."""

    last_config: dict[str, Any] | None = None

    def __init__(self, config: dict[str, Any]) -> None:
        _FakeProvider.last_config = dict(config)
        self._config = config

    def create_stt(self, model: str, **kwargs: Any) -> Any:
        return _Stub(model, **kwargs)

    def create_llm(self, model: str, **kwargs: Any) -> Any:
        return _Stub(model, **kwargs)

    def create_tts(self, model: str, voice: str | None = None, **kwargs: Any) -> Any:
        return _Stub(model, voice=voice, **kwargs)

    async def health_check(self) -> bool:
        return True


class _Stub:
    """Provider-side stub the wrapper subclasses receive at construction."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        from livekit.agents.stt import STTCapabilities
        from livekit.agents.tts import TTSCapabilities

        self.capabilities: Any = STTCapabilities(streaming=False, interim_results=False)
        self._tts_capabilities: Any = TTSCapabilities(streaming=False)
        self.sample_rate = 24000
        self.num_channels = 1
        self.args = args
        self.kwargs = kwargs

    def on(self, _event: str, _cb: Any) -> None:
        # No-op; the wrapper registers an event bridge but the stub
        # never emits.
        pass


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.delenv("VOICEGW_ACTIVE_PROJECT", raising=False)
    project.reset_project()
    yield
    project.reset_project()


@pytest.fixture
def fake_providers(monkeypatch):
    _FakeProvider.last_config = None

    def _create(_provider_name: str, config: dict[str, Any]) -> _FakeProvider:
        return _FakeProvider(config)

    monkeypatch.setattr(stt, "create_provider", _create)
    monkeypatch.setattr(llm, "create_provider", _create)
    monkeypatch.setattr(tts, "create_provider", _create)
    return _FakeProvider


def _write_config(tmp_path, config: dict[str, Any]) -> str:
    path = tmp_path / "voicegw.yaml"
    path.write_text(yaml.dump(config))
    return str(path)


def _build_gateway(tmp_path, monkeypatch, extra: dict[str, Any] | None = None):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "vg.db"))
    cfg = {
        "providers": {
            "openai": {"api_key": "global-openai-key"},
            "deepgram": {"api_key": "global-dg-key"},
            "cartesia": {"api_key": "global-cartesia-key"},
        },
        "projects": {},
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "stacks": {},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
        "cost_tracking": {"enabled": True},
        "observability": {"latency_tracking": True},
    }
    if extra:
        for k, v in extra.items():
            cfg[k] = v
    cfg_path = _write_config(tmp_path, cfg)

    from voicegateway.core.gateway import Gateway

    gw = Gateway(config_path=cfg_path)
    monkeypatch.setattr(factory, "_gateway", gw)
    return gw


# ---------------------------------------------------------------------------
# Basic resolution: DB row written, refresh, factory picks it up
# ---------------------------------------------------------------------------


async def test_db_managed_key_resolves_after_refresh(
    tmp_path, monkeypatch, fake_providers
):
    gw = _build_gateway(tmp_path, monkeypatch)

    await gw.storage.upsert_managed_provider(
        provider_id="tony:openai",
        provider_type="openai",
        api_key="sk-tony-db",
        project="tony",
    )
    await gw.refresh_config()

    project.set_project("tony")
    llm.LLM("openai/gpt-4o-mini")
    assert fake_providers.last_config["api_key"] == "sk-tony-db"


async def test_db_managed_key_creates_stub_project(
    tmp_path, monkeypatch, fake_providers
):
    """A DB row whose project does not appear in voicegw.yaml must"""
    gw = _build_gateway(tmp_path, monkeypatch)

    await gw.storage.upsert_managed_provider(
        provider_id="mama:deepgram",
        provider_type="deepgram",
        api_key="sk-mama-dg-db",
        project="mama",
    )
    await gw.refresh_config()

    assert "mama" in gw.config.projects
    assert gw.config.projects["mama"].source == "db"
    assert (
        gw.config.projects["mama"].providers["deepgram"]["api_key"] == "sk-mama-dg-db"
    )

    project.set_project("mama")
    stt.STT("deepgram/nova-3")
    assert fake_providers.last_config["api_key"] == "sk-mama-dg-db"


async def test_db_managed_key_does_not_leak_to_global_providers(tmp_path, monkeypatch):
    """The fix moves project-scoped DB rows OUT of merged.providers and"""
    gw = _build_gateway(tmp_path, monkeypatch)

    await gw.storage.upsert_managed_provider(
        provider_id="tony:openai",
        provider_type="openai",
        api_key="sk-tony-db",
        project="tony",
    )
    await gw.refresh_config()

    assert "tony:openai" not in gw.config.providers
    assert "openai" in gw.config.projects["tony"].providers


# ---------------------------------------------------------------------------
# Precedence: per-call api_key kwarg beats DB
# ---------------------------------------------------------------------------


async def test_per_call_api_key_kwarg_beats_db(tmp_path, monkeypatch, fake_providers):
    gw = _build_gateway(tmp_path, monkeypatch)

    await gw.storage.upsert_managed_provider(
        provider_id="tony:openai",
        provider_type="openai",
        api_key="sk-tony-db",
        project="tony",
    )
    await gw.refresh_config()

    project.set_project("tony")
    llm.LLM("openai/gpt-4o-mini", api_key="per-call-override")
    assert fake_providers.last_config["api_key"] == "per-call-override"


# ---------------------------------------------------------------------------
# Precedence: YAML per-project entry beats DB
# ---------------------------------------------------------------------------


async def test_yaml_per_project_entry_beats_db(tmp_path, monkeypatch, fake_providers):
    """When voicegw.yaml has projects.tony.providers.openai, that"""
    gw = _build_gateway(
        tmp_path,
        monkeypatch,
        extra={
            "projects": {
                "tony": {
                    "name": "Tony",
                    "providers": {"openai": {"api_key": "yaml-tony-openai"}},
                }
            }
        },
    )

    await gw.storage.upsert_managed_provider(
        provider_id="tony:openai",
        provider_type="openai",
        api_key="sk-tony-db-shadowed",
        project="tony",
    )
    await gw.refresh_config()

    project.set_project("tony")
    llm.LLM("openai/gpt-4o-mini")
    assert fake_providers.last_config["api_key"] == "yaml-tony-openai"


# ---------------------------------------------------------------------------
# Rotation: vg_set_provider_key flow reflected after refresh
# ---------------------------------------------------------------------------


async def test_db_key_rotation_visible_after_refresh(
    tmp_path, monkeypatch, fake_providers
):
    gw = _build_gateway(tmp_path, monkeypatch)

    await gw.storage.upsert_managed_provider(
        provider_id="tony:openai",
        provider_type="openai",
        api_key="sk-original",
        project="tony",
    )
    await gw.refresh_config()

    project.set_project("tony")
    llm.LLM("openai/gpt-4o-mini")
    assert fake_providers.last_config["api_key"] == "sk-original"

    # Rotate via the same upsert path vg_set_provider_key uses.
    await gw.storage.upsert_managed_provider(
        provider_id="tony:openai",
        provider_type="openai",
        api_key="sk-rotated",
        project="tony",
    )
    await gw.refresh_config()

    llm.LLM("openai/gpt-4o-mini")
    assert fake_providers.last_config["api_key"] == "sk-rotated"


# ---------------------------------------------------------------------------
# Multi-project isolation: each project resolves its own DB key
# ---------------------------------------------------------------------------


async def test_two_projects_with_distinct_db_openai_keys(
    tmp_path, monkeypatch, fake_providers
):
    gw = _build_gateway(tmp_path, monkeypatch)

    await gw.storage.upsert_managed_provider(
        provider_id="tony:openai",
        provider_type="openai",
        api_key="sk-tony",
        project="tony",
    )
    await gw.storage.upsert_managed_provider(
        provider_id="mama:openai",
        provider_type="openai",
        api_key="sk-mama",
        project="mama",
    )
    await gw.refresh_config()

    project.set_project("tony")
    llm.LLM("openai/gpt-4o-mini")
    assert fake_providers.last_config["api_key"] == "sk-tony"

    project.set_project("mama")
    llm.LLM("openai/gpt-4o-mini")
    assert fake_providers.last_config["api_key"] == "sk-mama"


# ---------------------------------------------------------------------------
# Legacy global-scope DB rows still land at top-level merged.providers
# ---------------------------------------------------------------------------


async def test_legacy_global_db_row_lands_at_top_level(
    tmp_path, monkeypatch, fake_providers
):
    """Pre-v0.0.5 add_provider tool wrote rows with no project column."""
    gw = _build_gateway(tmp_path, monkeypatch)

    # `project=None` mirrors the pre-v0.0.5 schema.
    await gw.storage.upsert_managed_provider(
        provider_id="anthropic",
        provider_type="anthropic",
        api_key="sk-legacy-anthropic",
        project=None,
    )
    await gw.refresh_config()

    assert "anthropic" in gw.config.providers
    assert gw.config.providers["anthropic"]["api_key"] == "sk-legacy-anthropic"
