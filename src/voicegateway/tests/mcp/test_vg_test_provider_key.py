"""Tests for the v0.0.5 ``vg_test_provider_key`` MCP tool.

Per design.md section 3.4. Resolves a per-project key (YAML first,
then DB), constructs the matching provider plugin, runs its
health_check, and returns ``{status, latency_ms, message}``.

The tests stub ``create_provider`` so we don't actually hit upstream
APIs — the unit being tested is the resolution + invocation glue,
not the provider's network behavior.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from voicegateway.core.gateway import Gateway
from voicegateway.mcp.errors import ProviderNotFoundError, ValidationError
from voicegateway.mcp.tools import ALL_TOOLS


def _tool(name: str):
    return next(t for t in ALL_TOOLS if t.name == name)


def _write_config(path, content: dict) -> str:
    cfg_path = path / "voicegw.yaml"
    cfg_path.write_text(yaml.dump(content))
    return str(cfg_path)


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "vg-test.db"))
    return Gateway(config_path=temp_config)


class _FakeProviderHealthy:
    last_config: dict[str, Any] | None = None

    def __init__(self, config: dict[str, Any]) -> None:
        type(self).last_config = dict(config)

    async def health_check(self) -> bool:
        return True


class _FakeProviderUnhealthy:
    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    async def health_check(self) -> bool:
        return False


class _FakeProviderRaises:
    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    async def health_check(self) -> bool:
        raise RuntimeError("simulated network failure")


@pytest.fixture
def healthy_provider(monkeypatch):
    _FakeProviderHealthy.last_config = None

    def _create(_provider_name: str, config: dict[str, Any]) -> _FakeProviderHealthy:
        return _FakeProviderHealthy(config)

    monkeypatch.setattr("voicegateway.core.registry.create_provider", _create)
    return _FakeProviderHealthy


@pytest.fixture
def unhealthy_provider(monkeypatch):
    def _create(_provider_name: str, config: dict[str, Any]) -> _FakeProviderUnhealthy:
        return _FakeProviderUnhealthy(config)

    monkeypatch.setattr("voicegateway.core.registry.create_provider", _create)


@pytest.fixture
def raising_provider(monkeypatch):
    def _create(_provider_name: str, config: dict[str, Any]) -> _FakeProviderRaises:
        return _FakeProviderRaises(config)

    monkeypatch.setattr("voicegateway.core.registry.create_provider", _create)


async def _seed_db_key(gateway: Gateway, project: str, provider: str, key: str):
    add = _tool("vg_add_provider")
    await add.handler(
        gateway,
        {"project": project, "provider": provider, "api_key": key},
    )


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------


def test_tool_is_registered():
    names = {t.name for t in ALL_TOOLS}
    assert "vg_test_provider_key" in names


def test_tool_input_schema_has_required_fields():
    tool = _tool("vg_test_provider_key")
    required = set(tool.input_schema.get("required", []))
    assert "project" in required
    assert "provider" in required


# ---------------------------------------------------------------------------
# Successful health check (DB-managed key)
# ---------------------------------------------------------------------------


async def test_healthy_db_key_returns_ok(gateway, healthy_provider):
    await _seed_db_key(gateway, "tony-pizza", "openai", "sk-tony")

    result = await _tool("vg_test_provider_key").handler(
        gateway, {"project": "tony-pizza", "provider": "openai"}
    )
    assert result["project"] == "tony-pizza"
    assert result["provider"] == "openai"
    assert result["provider_id"] == "tony-pizza:openai"
    assert result["status"] == "ok"
    assert result["message"] == "reachable"
    assert isinstance(result["latency_ms"], int)


async def test_db_key_decrypted_and_passed_to_provider(gateway, healthy_provider):
    """The plaintext key (decrypted from Fernet) reaches the provider's
    constructor as ``api_key``.
    """
    await _seed_db_key(gateway, "tony-pizza", "openai", "sk-tony-secret")
    await _tool("vg_test_provider_key").handler(
        gateway, {"project": "tony-pizza", "provider": "openai"}
    )
    assert healthy_provider.last_config is not None
    assert healthy_provider.last_config["api_key"] == "sk-tony-secret"


# ---------------------------------------------------------------------------
# Successful health check (YAML-defined key)
# ---------------------------------------------------------------------------


async def test_yaml_per_project_key_used(tmp_path, monkeypatch, healthy_provider):
    cfg_path = _write_config(
        tmp_path,
        {
            "providers": {"openai": {"api_key": "global-fallback"}},
            "projects": {
                "tony-pizza": {
                    "name": "Tony",
                    "providers": {"openai": {"api_key": "yaml-tony-key"}},
                }
            },
            "models": {"stt": {}, "llm": {}, "tts": {}},
            "stacks": {},
            "fallbacks": {"stt": [], "llm": [], "tts": []},
            "cost_tracking": {"enabled": True},
            "observability": {"latency_tracking": True},
        },
    )
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "v.db"))
    gw = Gateway(config_path=cfg_path)

    result = await _tool("vg_test_provider_key").handler(
        gw, {"project": "tony-pizza", "provider": "openai"}
    )
    assert result["status"] == "ok"
    assert healthy_provider.last_config is not None
    assert healthy_provider.last_config["api_key"] == "yaml-tony-key"


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


async def test_unhealthy_provider_returns_failed(gateway, unhealthy_provider):
    await _seed_db_key(gateway, "tony-pizza", "openai", "sk-bad")

    result = await _tool("vg_test_provider_key").handler(
        gateway, {"project": "tony-pizza", "provider": "openai"}
    )
    assert result["status"] == "failed"
    assert "unhealthy" in result["message"]


async def test_health_check_exception_caught(gateway, raising_provider):
    """A network/auth error during health_check must NOT crash the
    handler — return failed with the exception type/message.
    """
    await _seed_db_key(gateway, "tony-pizza", "openai", "sk-broken")

    result = await _tool("vg_test_provider_key").handler(
        gateway, {"project": "tony-pizza", "provider": "openai"}
    )
    assert result["status"] == "failed"
    assert "RuntimeError" in result["message"]
    assert "simulated network failure" in result["message"]


async def test_unknown_pair_raises_provider_not_found(gateway, healthy_provider):
    with pytest.raises(ProviderNotFoundError, match="Use vg_add_provider"):
        await _tool("vg_test_provider_key").handler(
            gateway, {"project": "ghost-project", "provider": "openai"}
        )


async def test_storage_disabled_with_no_yaml_match_raises_not_found(
    tmp_path, monkeypatch, healthy_provider
):
    cfg = {
        "providers": {"openai": {"api_key": "k"}},
        "projects": {"tony": {"name": "Tony"}},
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "stacks": {},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
        "cost_tracking": {"enabled": False},
        "observability": {"latency_tracking": True},
    }
    cfg_path = tmp_path / "no-storage.yaml"
    cfg_path.write_text(yaml.dump(cfg))
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    gw = Gateway(config_path=str(cfg_path))
    assert gw.storage is None

    with pytest.raises(ProviderNotFoundError):
        await _tool("vg_test_provider_key").handler(
            gw, {"project": "tony", "provider": "openai"}
        )


async def test_missing_required_args_rejected(gateway):
    with pytest.raises(ValidationError):
        await _tool("vg_test_provider_key").handler(gateway, {"project": "tony-pizza"})


async def test_unknown_provider_type_returns_failed(
    gateway, monkeypatch, healthy_provider
):
    """Even if the row exists with an obscure provider_type, the tool
    should return a failed status (not crash) so callers can show the
    user the actionable message.
    """
    # Inject a row whose provider_type is not in the registry. We
    # bypass vg_add_provider's validation by talking to storage
    # directly.
    if gateway.storage is None:
        pytest.skip("storage required")
    await gateway.storage.upsert_managed_provider(
        provider_id="ghost:weirdo",
        provider_type="weirdo",
        api_key="sk-weird",
        project="ghost",
    )
    await gateway.refresh_config()

    result = await _tool("vg_test_provider_key").handler(
        gateway, {"project": "ghost", "provider": "weirdo"}
    )
    assert result["status"] == "failed"
    assert "Unknown provider type" in result["message"]
