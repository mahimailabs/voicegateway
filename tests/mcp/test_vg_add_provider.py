"""Tests for the v0.0.5 ``vg_add_provider`` MCP tool.

Per design.md section 3.4, ``vg_add_provider(project, provider, api_key)``
persists a Fernet-encrypted provider key scoped to a project. The
managed_providers row's primary key is built as
``"<project>:<provider>"`` so multiple projects can each carry their
own ``openai`` key without colliding.

This test file pins the persistence and tool-surface contract. The
companion test ``tests/inference/test_factory_db_managed_keys.py``
covers the resolver wiring end-to-end (a row written here flows into
the next ``inference.STT/LLM/TTS`` factory call's provider config).
"""

from __future__ import annotations

import pytest

from voicegateway.core.gateway import Gateway
from voicegateway.mcp.errors import ProviderAlreadyExistsError, ValidationError
from voicegateway.mcp.tools import ALL_TOOLS


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "vg-add.db"))
    return Gateway(config_path=temp_config)


def _vg_add_tool():
    return next(t for t in ALL_TOOLS if t.name == "vg_add_provider")


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------


def test_tool_is_registered():
    names = {t.name for t in ALL_TOOLS}
    assert "vg_add_provider" in names


def test_tool_input_schema_has_project_field():
    tool = _vg_add_tool()
    schema = tool.input_schema
    # JSON-schema check — ``project`` and ``provider`` are required fields.
    required = set(schema.get("required", []))
    assert "project" in required
    assert "provider" in required
    assert "api_key" in required


# ---------------------------------------------------------------------------
# Storage round-trip
# ---------------------------------------------------------------------------


async def test_persists_row_with_project_and_composite_id(gateway):
    tool = _vg_add_tool()
    result = await tool.handler(
        gateway,
        {
            "project": "tony-pizza",
            "provider": "deepgram",
            "api_key": "sk-tony-deepgram",
        },
    )
    assert result["project"] == "tony-pizza"
    assert result["provider"] == "deepgram"
    assert result["provider_id"] == "tony-pizza:deepgram"
    assert result["created"] is True
    assert result["source"] == "db"

    # Verify it landed in the managed_providers table with the project
    # column populated.
    rows = await gateway.storage.list_managed_providers()
    by_id = {r["provider_id"]: r for r in rows}
    assert "tony-pizza:deepgram" in by_id
    saved = by_id["tony-pizza:deepgram"]
    assert saved["project"] == "tony-pizza"
    assert saved["provider_type"] == "deepgram"


async def test_api_key_is_fernet_encrypted_at_rest(gateway):
    """The api_key field passed in via the MCP tool must NOT round-trip
    as plaintext through the database — the existing managed_providers
    encryption layer applies to the new project-scoped rows too.
    """
    from voicegateway.core.crypto import is_fernet_token

    tool = _vg_add_tool()
    await tool.handler(
        gateway,
        {
            "project": "tony-pizza",
            "provider": "deepgram",
            "api_key": "sk-plaintext-secret-for-storage-test",
        },
    )

    rows = await gateway.storage.list_managed_providers()
    saved = next(r for r in rows if r["provider_id"] == "tony-pizza:deepgram")
    assert saved["api_key_encrypted"] != "sk-plaintext-secret-for-storage-test"
    assert is_fernet_token(saved["api_key_encrypted"])


async def test_two_projects_can_each_carry_their_own_openai_key(gateway):
    tool = _vg_add_tool()
    await tool.handler(
        gateway,
        {
            "project": "tony-pizza",
            "provider": "openai",
            "api_key": "sk-tony-openai",
        },
    )
    await tool.handler(
        gateway,
        {
            "project": "mama-diner",
            "provider": "openai",
            "api_key": "sk-mama-openai",
        },
    )

    rows = await gateway.storage.list_managed_providers()
    ids = {r["provider_id"] for r in rows}
    assert {"tony-pizza:openai", "mama-diner:openai"}.issubset(ids)


async def test_re_adding_same_project_provider_updates_in_place(gateway):
    """Calling vg_add_provider twice with the same (project, provider)
    upserts — only one row should remain, with the latest api_key.
    """
    tool = _vg_add_tool()
    await tool.handler(
        gateway,
        {
            "project": "tony-pizza",
            "provider": "openai",
            "api_key": "sk-original",
        },
    )
    await tool.handler(
        gateway,
        {
            "project": "tony-pizza",
            "provider": "openai",
            "api_key": "sk-rotated",
        },
    )

    rows = await gateway.storage.list_managed_providers()
    matching = [r for r in rows if r["provider_id"] == "tony-pizza:openai"]
    assert len(matching) == 1
    # Keys are encrypted, but the encryption is non-deterministic so we
    # cannot compare ciphertexts. Decrypt and compare plaintext.
    from voicegateway.core.crypto import decrypt

    assert decrypt(matching[0]["api_key_encrypted"]) == "sk-rotated"


async def test_base_url_persisted(gateway):
    tool = _vg_add_tool()
    await tool.handler(
        gateway,
        {
            "project": "tony-pizza",
            "provider": "openai",
            "api_key": "k",
            "base_url": "https://my-openai-proxy.example",
        },
    )
    rows = await gateway.storage.list_managed_providers()
    saved = next(r for r in rows if r["provider_id"] == "tony-pizza:openai")
    assert saved["base_url"] == "https://my-openai-proxy.example"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_unknown_provider_rejected(gateway):
    tool = _vg_add_tool()
    with pytest.raises(ValidationError, match="Unknown provider"):
        await tool.handler(
            gateway,
            {
                "project": "tony-pizza",
                "provider": "not-a-provider",
                "api_key": "k",
            },
        )


async def test_missing_required_args_rejected(gateway):
    tool = _vg_add_tool()
    with pytest.raises(ValidationError):
        await tool.handler(gateway, {"project": "tony-pizza"})


async def test_storage_disabled_rejected(temp_config, tmp_path, monkeypatch):
    """Without cost_tracking enabled, there's no SQLite to persist
    into — the tool must error rather than silently accept.
    """
    import yaml as _yaml

    cfg_path = tmp_path / "no-storage.yaml"
    cfg_path.write_text(
        _yaml.dump(
            {
                "providers": {"openai": {"api_key": "k"}},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "stacks": {},
                "fallbacks": {"stt": [], "llm": [], "tts": []},
                "cost_tracking": {"enabled": False},
                "observability": {"latency_tracking": True},
            }
        )
    )
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    gw = Gateway(config_path=str(cfg_path))
    assert gw.storage is None

    tool = _vg_add_tool()
    with pytest.raises(ValidationError, match="cost_tracking"):
        await tool.handler(
            gw,
            {"project": "x", "provider": "openai", "api_key": "k"},
        )


async def test_yaml_provider_id_collision_rejected(tmp_path, monkeypatch):
    """If voicegw.yaml already defines a provider whose id matches the
    composite key (rare but possible if a user names their YAML
    provider literally "tony-pizza:openai"), the MCP tool refuses to
    overwrite it. Matches the semantics of the existing add_provider
    YAML guard.
    """
    import yaml as _yaml

    cfg = {
        # Pre-define a yaml provider with the exact composite id.
        "providers": {"tony-pizza:openai": {"api_key": "from-yaml"}},
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "stacks": {},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
        "cost_tracking": {"enabled": True},
        "observability": {"latency_tracking": True},
    }
    cfg_path = tmp_path / "voicegw.yaml"
    cfg_path.write_text(_yaml.dump(cfg))
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "v.db"))
    gw = Gateway(config_path=str(cfg_path))

    tool = _vg_add_tool()
    with pytest.raises(ProviderAlreadyExistsError):
        await tool.handler(
            gw,
            {
                "project": "tony-pizza",
                "provider": "openai",
                "api_key": "from-mcp",
            },
        )


async def test_yaml_per_project_collision_rejected(tmp_path, monkeypatch):
    """Codex P2 #1 (iter 41): a YAML projects.<id>.providers.<name>
    entry must block vg_add_provider from writing a shadow DB row
    for the same (project, provider) pair. The inference resolver
    reads YAML before DB-managed rows, so the DB write would silently
    do nothing while the caller thinks they rotated the key.
    """
    import yaml as _yaml

    cfg = {
        "providers": {},
        "projects": {
            "tony-pizza": {
                "name": "Tony",
                "providers": {
                    "openai": {"api_key": "yaml-tony-openai"},
                },
            },
        },
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "stacks": {},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
        "cost_tracking": {"enabled": True},
        "observability": {"latency_tracking": True},
    }
    cfg_path = tmp_path / "voicegw.yaml"
    cfg_path.write_text(_yaml.dump(cfg))
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "v.db"))
    gw = Gateway(config_path=str(cfg_path))

    tool = _vg_add_tool()
    with pytest.raises(ProviderAlreadyExistsError, match="voicegw.yaml"):
        await tool.handler(
            gw,
            {
                "project": "tony-pizza",
                "provider": "openai",
                "api_key": "would-silently-shadow",
            },
        )

    # No DB row was created.
    rows = await gw.storage.list_managed_providers()
    assert all(r["provider_id"] != "tony-pizza:openai" for r in rows)


async def test_yaml_per_project_collision_only_blocks_matching_provider(
    tmp_path, monkeypatch
):
    """The YAML projects.tony-pizza.providers.openai entry must NOT
    block vg_add_provider for a different (project, provider) pair.
    The guard is precise.
    """
    import yaml as _yaml

    cfg = {
        "providers": {},
        "projects": {
            "tony-pizza": {
                "name": "Tony",
                "providers": {"openai": {"api_key": "yaml-key"}},
            },
        },
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "stacks": {},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
        "cost_tracking": {"enabled": True},
        "observability": {"latency_tracking": True},
    }
    cfg_path = tmp_path / "voicegw.yaml"
    cfg_path.write_text(_yaml.dump(cfg))
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "v.db"))
    gw = Gateway(config_path=str(cfg_path))

    tool = _vg_add_tool()
    # Different provider on the same project: should succeed.
    result = await tool.handler(
        gw,
        {
            "project": "tony-pizza",
            "provider": "deepgram",
            "api_key": "sk-tony-dg",
        },
    )
    assert result["created"] is True

    # Same provider on a different project: should also succeed.
    result = await tool.handler(
        gw,
        {
            "project": "mama-diner",
            "provider": "openai",
            "api_key": "sk-mama",
        },
    )
    assert result["created"] is True
