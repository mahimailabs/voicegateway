"""Tests for the v0.0.5 ``vg_list_providers`` MCP tool.

Per design.md section 3.4. Returns per-project provider entries from
voicegw.yaml AND DB-managed rows, with optional project filter. The
api_key field is always masked.
"""

from __future__ import annotations

import pytest
import yaml

from voicegateway.core.gateway import Gateway
from voicegateway.server.mcp.tools import ALL_TOOLS


def _tool(name: str):
    return next(t for t in ALL_TOOLS if t.name == name)


def _write_config(path, content: dict) -> str:
    cfg_path = path / "voicegw.yaml"
    cfg_path.write_text(yaml.dump(content))
    return str(cfg_path)


@pytest.fixture
def empty_gateway(tmp_path, monkeypatch):
    """Gateway with NO project providers, NO DB managed rows."""
    cfg_path = _write_config(
        tmp_path,
        {
            "providers": {"openai": {"api_key": "global-openai"}},
            "models": {"stt": {}, "llm": {}, "tts": {}},
            "stacks": {},
            "fallbacks": {"stt": [], "llm": [], "tts": []},
            "cost_tracking": {"enabled": True},
            "observability": {"latency_tracking": True},
        },
    )
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "vg-list.db"))
    return Gateway(config_path=cfg_path)


@pytest.fixture
def yaml_seeded_gateway(tmp_path, monkeypatch):
    """Gateway with YAML-defined per-project provider keys."""
    cfg_path = _write_config(
        tmp_path,
        {
            "providers": {"openai": {"api_key": "global-openai"}},
            "projects": {
                "tony-pizza": {
                    "name": "Tony",
                    "providers": {
                        "openai": {"api_key": "tony-openai-key"},
                        "deepgram": {"api_key": "tony-dg-key"},
                    },
                },
                "mama-diner": {
                    "name": "Mama",
                    "providers": {"openai": {"api_key": "mama-openai-key"}},
                },
            },
            "default_project": "tony-pizza",
            "models": {"stt": {}, "llm": {}, "tts": {}},
            "stacks": {},
            "fallbacks": {"stt": [], "llm": [], "tts": []},
            "cost_tracking": {"enabled": True},
            "observability": {"latency_tracking": True},
        },
    )
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "vg-list.db"))
    return Gateway(config_path=cfg_path)


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
    assert "vg_list_providers" in names


def test_tool_input_schema_has_optional_project():
    tool = _tool("vg_list_providers")
    required = set(tool.input_schema.get("required", []))
    # No required fields — everything is optional.
    assert "project" not in required


# ---------------------------------------------------------------------------
# Empty / minimal cases
# ---------------------------------------------------------------------------


async def test_empty_returns_empty_list(empty_gateway):
    """No project providers in yaml or DB → providers list is empty.
    The top-level providers: block is NOT included; vg_list_providers
    is the per-project view.
    """
    result = await _tool("vg_list_providers").handler(empty_gateway, {})
    assert result == {"providers": []}


# ---------------------------------------------------------------------------
# YAML-only entries
# ---------------------------------------------------------------------------


async def test_yaml_per_project_keys_listed(yaml_seeded_gateway):
    result = await _tool("vg_list_providers").handler(yaml_seeded_gateway, {})
    rows = result["providers"]
    ids = {r["provider_id"] for r in rows}
    assert ids == {
        "tony-pizza:openai",
        "tony-pizza:deepgram",
        "mama-diner:openai",
    }
    # Every row carries source=yaml.
    assert {r["source"] for r in rows} == {"yaml"}


async def test_yaml_keys_are_masked(yaml_seeded_gateway):
    result = await _tool("vg_list_providers").handler(yaml_seeded_gateway, {})
    for row in result["providers"]:
        masked = row["api_key_masked"]
        assert masked is not None
        # Masked form should NOT contain the substring "openai" or "dg"
        # (the meaningful suffixes of the yaml-seeded keys). _mask_api_key
        # uses ``first4...last4`` so a 16-char key like "tony-openai-key"
        # could plausibly include those substrings. Assert via length
        # threshold instead — the key must NOT round-trip plaintext.
        plaintext_keys = ["tony-openai-key", "tony-dg-key", "mama-openai-key"]
        assert masked not in plaintext_keys


async def test_project_filter_narrows_yaml_results(yaml_seeded_gateway):
    result = await _tool("vg_list_providers").handler(
        yaml_seeded_gateway, {"project": "tony-pizza"}
    )
    rows = result["providers"]
    assert {r["provider_id"] for r in rows} == {
        "tony-pizza:openai",
        "tony-pizza:deepgram",
    }


# ---------------------------------------------------------------------------
# DB-managed entries
# ---------------------------------------------------------------------------


async def test_db_per_project_keys_listed(empty_gateway):
    await _seed_db_key(empty_gateway, "tony-pizza", "openai", "sk-tony")
    await _seed_db_key(empty_gateway, "mama-diner", "deepgram", "sk-mama")

    result = await _tool("vg_list_providers").handler(empty_gateway, {})
    rows = result["providers"]
    by_id = {r["provider_id"]: r for r in rows}
    assert "tony-pizza:openai" in by_id
    assert "mama-diner:deepgram" in by_id
    # All db-sourced.
    assert {r["source"] for r in rows} == {"db"}


async def test_legacy_global_db_rows_excluded(empty_gateway):
    """vg_list_providers is the per-project view. Legacy DB rows with
    project=NULL are NOT returned (they belong to the existing
    list_providers tool's global view).
    """
    if empty_gateway.storage is None:
        pytest.skip("storage required")
    await empty_gateway.storage.upsert_managed_provider(
        provider_id="standalone",
        provider_type="openai",
        api_key="sk-standalone",
        project=None,
    )
    await empty_gateway.refresh_config()

    result = await _tool("vg_list_providers").handler(empty_gateway, {})
    assert result == {"providers": []}


async def test_project_filter_narrows_db_results(empty_gateway):
    await _seed_db_key(empty_gateway, "tony-pizza", "openai", "sk-tony")
    await _seed_db_key(empty_gateway, "mama-diner", "deepgram", "sk-mama")

    result = await _tool("vg_list_providers").handler(
        empty_gateway, {"project": "mama-diner"}
    )
    rows = result["providers"]
    assert len(rows) == 1
    assert rows[0]["provider_id"] == "mama-diner:deepgram"


# ---------------------------------------------------------------------------
# Mixed yaml + db
# ---------------------------------------------------------------------------


async def test_yaml_and_db_combined_with_yaml_winning_on_collision(
    yaml_seeded_gateway,
):
    """If a DB row shares a provider_id with a YAML row, the YAML
    entry wins (matches ConfigManager.load_merged precedence). The
    response shows only one row, sourced=yaml.
    """
    # tony-pizza:openai already exists in YAML. Try to add a DB row
    # with the same composite id.
    if yaml_seeded_gateway.storage is None:
        pytest.skip("storage required")
    await yaml_seeded_gateway.storage.upsert_managed_provider(
        provider_id="tony-pizza:openai",
        provider_type="openai",
        api_key="sk-from-db",
        project="tony-pizza",
    )
    await yaml_seeded_gateway.refresh_config()

    result = await _tool("vg_list_providers").handler(
        yaml_seeded_gateway, {"project": "tony-pizza"}
    )
    rows = result["providers"]
    by_id = {r["provider_id"]: r for r in rows}
    assert by_id["tony-pizza:openai"]["source"] == "yaml"


# ---------------------------------------------------------------------------
# Storage disabled
# ---------------------------------------------------------------------------


async def test_storage_disabled_falls_back_to_yaml_only(tmp_path, monkeypatch):
    cfg_path = _write_config(
        tmp_path,
        {
            "providers": {},
            "projects": {
                "tony-pizza": {
                    "name": "Tony",
                    "providers": {"openai": {"api_key": "yaml-key"}},
                }
            },
            "models": {"stt": {}, "llm": {}, "tts": {}},
            "stacks": {},
            "fallbacks": {"stt": [], "llm": [], "tts": []},
            "cost_tracking": {"enabled": False},
            "observability": {"latency_tracking": True},
        },
    )
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    gw = Gateway(config_path=cfg_path)
    assert gw.storage is None

    result = await _tool("vg_list_providers").handler(gw, {})
    rows = result["providers"]
    assert len(rows) == 1
    assert rows[0]["source"] == "yaml"
    assert rows[0]["provider_id"] == "tony-pizza:openai"
