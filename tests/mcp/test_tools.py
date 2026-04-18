"""Unit tests for all MCP tool handlers."""

from __future__ import annotations

import time
import uuid

import pytest

from voicegateway.core.gateway import Gateway
from voicegateway.mcp.errors import ModelNotFoundError, ValidationError
from voicegateway.mcp.tools import ALL_TOOLS
from voicegateway.storage.models import RequestRecord


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "mcp-test.db"))
    return Gateway(config_path=temp_config)


@pytest.fixture
async def seeded_gateway(gateway):
    storage = gateway.storage
    now = time.time()
    rows = [
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=now - 30,
            modality="stt",
            model_id="deepgram/nova-3",
            provider="deepgram",
            project="test-project",
            input_units=1.0,
            cost_usd=0.005,
            ttfb_ms=120.0,
            total_latency_ms=250.0,
        ),
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=now - 10,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            project="test-project",
            input_units=100,
            output_units=50,
            cost_usd=0.012,
            ttfb_ms=200.0,
            total_latency_ms=800.0,
        ),
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=now - 5,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            project="default",
            cost_usd=0.003,
            ttfb_ms=180.0,
            total_latency_ms=600.0,
            status="error",
            error_message="network timeout",
        ),
    ]
    for r in rows:
        await storage.log_request(r)
    return gateway


def _tool(name: str):
    for t in ALL_TOOLS:
        if t.name == name:
            return t
    raise AssertionError(f"Tool {name!r} not registered")


# --------------------------------------------------------------------
# Observability
# --------------------------------------------------------------------


async def test_get_health(gateway):
    tool = _tool("get_health")
    result = await tool.handler(gateway, {})
    assert result["status"] == "ok"
    assert "version" in result
    assert "uptime_seconds" in result
    assert result["project_count"] >= 2


async def test_get_health_rejects_extra_args(gateway):
    tool = _tool("get_health")
    with pytest.raises(ValidationError):
        await tool.handler(gateway, {"bogus": 1})


async def test_get_provider_status_all(gateway):
    tool = _tool("get_provider_status")
    result = await tool.handler(gateway, {})
    assert "providers" in result
    assert "openai" in result["providers"]
    assert result["providers"]["openai"]["type"] == "cloud"


async def test_get_provider_status_specific(gateway):
    tool = _tool("get_provider_status")
    result = await tool.handler(gateway, {"provider_id": "deepgram"})
    assert set(result["providers"].keys()) == {"deepgram"}


async def test_get_provider_status_unknown(gateway):
    tool = _tool("get_provider_status")
    result = await tool.handler(gateway, {"provider_id": "nonexistent"})
    assert "nonexistent" in result.get("missing", [])


async def test_get_costs_empty(gateway):
    tool = _tool("get_costs")
    result = await tool.handler(gateway, {"period": "today"})
    assert result["total_usd"] == 0.0
    assert result["by_provider"] == {}


async def test_get_costs_with_data(seeded_gateway):
    tool = _tool("get_costs")
    result = await tool.handler(seeded_gateway, {"period": "today"})
    assert result["total_usd"] > 0.0
    assert "by_project" in result


async def test_get_costs_filtered_by_project(seeded_gateway):
    tool = _tool("get_costs")
    result = await tool.handler(seeded_gateway, {"project": "test-project"})
    assert result["project"] == "test-project"
    assert result["total_usd"] > 0.0


async def test_get_costs_invalid_period(gateway):
    tool = _tool("get_costs")
    with pytest.raises(ValidationError):
        await tool.handler(gateway, {"period": "forever"})


async def test_get_latency_stats(seeded_gateway):
    tool = _tool("get_latency_stats")
    result = await tool.handler(seeded_gateway, {"period": "today"})
    assert "overall" in result
    assert "by_model" in result
    assert result["overall"]["request_count"] >= 1


async def test_get_latency_stats_invalid_modality(gateway):
    tool = _tool("get_latency_stats")
    with pytest.raises(ValidationError):
        await tool.handler(gateway, {"modality": "video"})


async def test_get_logs_empty(gateway):
    tool = _tool("get_logs")
    result = await tool.handler(gateway, {})
    assert result == []


async def test_get_logs_with_data(seeded_gateway):
    tool = _tool("get_logs")
    result = await tool.handler(seeded_gateway, {"limit": 10})
    assert len(result) == 3


async def test_get_logs_filter_by_modality(seeded_gateway):
    tool = _tool("get_logs")
    result = await tool.handler(seeded_gateway, {"modality": "stt"})
    assert all(r["modality"] == "stt" for r in result)


async def test_get_logs_filter_by_status(seeded_gateway):
    tool = _tool("get_logs")
    result = await tool.handler(seeded_gateway, {"status": "error"})
    assert all(r["status"] == "error" for r in result)


async def test_get_logs_limit_too_high(gateway):
    tool = _tool("get_logs")
    with pytest.raises(ValidationError):
        await tool.handler(gateway, {"limit": 99999})


# --------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------


async def test_list_providers_from_yaml(gateway):
    tool = _tool("list_providers")
    result = await tool.handler(gateway, {})
    ids = {p["provider_id"] for p in result["providers"]}
    assert "openai" in ids
    assert "deepgram" in ids


async def test_get_provider_yaml(gateway):
    tool = _tool("get_provider")
    result = await tool.handler(gateway, {"provider_id": "openai"})
    assert result["provider_id"] == "openai"
    assert result["source"] == "yaml"


async def test_get_provider_not_found(gateway):
    from voicegateway.mcp.errors import ProviderNotFoundError
    tool = _tool("get_provider")
    with pytest.raises(ProviderNotFoundError):
        await tool.handler(gateway, {"provider_id": "doesnt-exist"})


async def test_add_provider_yaml_conflict(gateway):
    from voicegateway.mcp.errors import ProviderAlreadyExistsError
    tool = _tool("add_provider")
    with pytest.raises(ProviderAlreadyExistsError):
        await tool.handler(gateway, {
            "provider_id": "openai",
            "provider_type": "openai",
            "api_key": "sk-test",
        })


async def test_add_provider_unknown_type(gateway):
    tool = _tool("add_provider")
    with pytest.raises(ValidationError):
        await tool.handler(gateway, {
            "provider_id": "bogus",
            "provider_type": "nonexistent-provider",
            "api_key": "x",
        })


async def test_add_provider_local_succeeds(gateway):
    """Local providers don't need credentials tested."""
    tool = _tool("add_provider")
    result = await tool.handler(gateway, {
        "provider_id": "my-ollama",
        "provider_type": "ollama",
        "api_key": "",
        "base_url": "http://localhost:11434",
    })
    assert result["created"] is True
    assert result["source"] == "db"


async def test_delete_provider_yaml_readonly(gateway):
    from voicegateway.mcp.errors import ReadOnlyResourceError
    tool = _tool("delete_provider")
    with pytest.raises(ReadOnlyResourceError):
        await tool.handler(gateway, {"provider_id": "openai", "confirm": True})


async def test_delete_provider_not_found(gateway):
    from voicegateway.mcp.errors import ProviderNotFoundError
    tool = _tool("delete_provider")
    with pytest.raises(ProviderNotFoundError):
        await tool.handler(gateway, {"provider_id": "never-added", "confirm": True})


async def test_delete_provider_requires_confirm(gateway):
    from voicegateway.mcp.errors import ConfirmationRequiredError
    # First add a managed one
    add_tool = _tool("add_provider")
    await add_tool.handler(gateway, {
        "provider_id": "ollama-custom",
        "provider_type": "ollama",
        "api_key": "",
    })
    del_tool = _tool("delete_provider")
    with pytest.raises(ConfirmationRequiredError):
        await del_tool.handler(gateway, {"provider_id": "ollama-custom", "confirm": False})


async def test_delete_provider_with_confirm(gateway):
    add_tool = _tool("add_provider")
    await add_tool.handler(gateway, {
        "provider_id": "ollama-one",
        "provider_type": "ollama",
        "api_key": "",
    })
    del_tool = _tool("delete_provider")
    result = await del_tool.handler(gateway, {"provider_id": "ollama-one", "confirm": True})
    assert result["action"] == "deleted"
    assert result["provider_id"] == "ollama-one"


async def test_test_provider_not_found(gateway):
    from voicegateway.mcp.errors import ProviderNotFoundError
    tool = _tool("test_provider")
    with pytest.raises(ProviderNotFoundError):
        await tool.handler(gateway, {"provider_id": "never-added"})


# --------------------------------------------------------------------
# Models
# --------------------------------------------------------------------


async def test_list_models_all(gateway):
    tool = _tool("list_models")
    result = await tool.handler(gateway, {})
    ids = {m["model_id"] for m in result["models"]}
    assert "deepgram/nova-3" in ids
    assert "openai/gpt-4o-mini" in ids


async def test_list_models_filter_modality(gateway):
    tool = _tool("list_models")
    result = await tool.handler(gateway, {"modality": "llm"})
    assert all(m["modality"] == "llm" for m in result["models"])


async def test_list_models_filter_provider(gateway):
    tool = _tool("list_models")
    result = await tool.handler(gateway, {"provider_id": "openai"})
    assert all(m["provider_id"] == "openai" for m in result["models"])


async def test_register_model_new(gateway):
    # First need a provider — use an existing YAML one.
    tool = _tool("register_model")
    result = await tool.handler(gateway, {
        "modality": "llm",
        "provider_id": "openai",
        "model_name": "gpt-5-turbo",
    })
    assert result["created"] is True
    assert result["model_id"] == "openai/gpt-5-turbo"


async def test_register_model_yaml_conflict(gateway):
    from voicegateway.mcp.errors import ModelAlreadyExistsError
    tool = _tool("register_model")
    with pytest.raises(ModelAlreadyExistsError):
        await tool.handler(gateway, {
            "modality": "llm",
            "provider_id": "openai",
            "model_name": "gpt-4o-mini",
        })


async def test_register_model_unknown_provider(gateway):
    from voicegateway.mcp.errors import ProviderNotFoundError
    tool = _tool("register_model")
    with pytest.raises(ProviderNotFoundError):
        await tool.handler(gateway, {
            "modality": "llm",
            "provider_id": "nonexistent",
            "model_name": "foo",
        })


async def test_delete_model_yaml_readonly(gateway):
    from voicegateway.mcp.errors import ReadOnlyResourceError
    tool = _tool("delete_model")
    with pytest.raises(ReadOnlyResourceError):
        await tool.handler(gateway, {"model_id": "deepgram/nova-3", "confirm": True})


async def test_delete_model_confirm_flow(gateway):
    # Register → preview → delete
    reg = _tool("register_model")
    await reg.handler(gateway, {
        "modality": "llm",
        "provider_id": "openai",
        "model_name": "custom-llm",
    })

    from voicegateway.mcp.errors import ConfirmationRequiredError
    del_tool = _tool("delete_model")
    with pytest.raises(ConfirmationRequiredError):
        await del_tool.handler(gateway, {"model_id": "openai/custom-llm", "confirm": False})

    result = await del_tool.handler(gateway, {"model_id": "openai/custom-llm", "confirm": True})
    assert result["action"] == "deleted"


async def test_delete_model_not_found(gateway):
    from voicegateway.mcp.errors import ModelNotFoundError
    tool = _tool("delete_model")
    with pytest.raises(ModelNotFoundError):
        await tool.handler(gateway, {"model_id": "never/existed", "confirm": True})


# --------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------


async def test_list_projects_yaml(gateway):
    tool = _tool("list_projects")
    result = await tool.handler(gateway, {})
    ids = {p["id"] for p in result["projects"]}
    assert "test-project" in ids
    assert "blocked-project" in ids


async def test_get_project_yaml(gateway):
    tool = _tool("get_project")
    result = await tool.handler(gateway, {"project_id": "test-project"})
    assert result["id"] == "test-project"
    assert result["daily_budget"] == 10.0


async def test_get_project_not_found(gateway):
    from voicegateway.mcp.errors import ProjectNotFoundError
    tool = _tool("get_project")
    with pytest.raises(ProjectNotFoundError):
        await tool.handler(gateway, {"project_id": "never-created"})


async def test_create_project_happy_path(gateway):
    tool = _tool("create_project")
    result = await tool.handler(gateway, {
        "project_id": "new-proj",
        "name": "New Project",
        "description": "Testing",
        "daily_budget": 2.5,
        "budget_action": "warn",
    })
    assert result["created"] is True
    assert result["project_id"] == "new-proj"


async def test_create_project_conflict(gateway):
    from voicegateway.mcp.errors import ProjectAlreadyExistsError
    tool = _tool("create_project")
    with pytest.raises(ProjectAlreadyExistsError):
        await tool.handler(gateway, {
            "project_id": "test-project",
            "name": "dup",
        })


async def test_create_project_unknown_model(gateway):
    tool = _tool("create_project")
    with pytest.raises(ModelNotFoundError):
        await tool.handler(gateway, {
            "project_id": "broken-proj",
            "name": "Broken",
            "llm_model": "openai/gpt-99",
        })


async def test_create_project_default_stack_and_model_conflict(gateway):
    tool = _tool("create_project")
    with pytest.raises(ValidationError):
        await tool.handler(gateway, {
            "project_id": "conflict",
            "name": "X",
            "default_stack": "default",
            "llm_model": "openai/gpt-4o-mini",
        })


async def test_create_project_negative_budget(gateway):
    tool = _tool("create_project")
    with pytest.raises(ValidationError):
        await tool.handler(gateway, {
            "project_id": "neg",
            "name": "X",
            "daily_budget": -1.0,
        })


async def test_delete_project_yaml_readonly(gateway):
    from voicegateway.mcp.errors import ReadOnlyResourceError
    tool = _tool("delete_project")
    with pytest.raises(ReadOnlyResourceError):
        await tool.handler(gateway, {"project_id": "test-project", "confirm": True})


async def test_delete_project_confirm_flow(gateway):
    from voicegateway.mcp.errors import ConfirmationRequiredError
    create = _tool("create_project")
    await create.handler(gateway, {"project_id": "delete-me", "name": "Del"})

    delete = _tool("delete_project")
    with pytest.raises(ConfirmationRequiredError):
        await delete.handler(gateway, {"project_id": "delete-me", "confirm": False})

    result = await delete.handler(gateway, {"project_id": "delete-me", "confirm": True})
    assert result["action"] == "deleted"


async def test_delete_project_not_found(gateway):
    from voicegateway.mcp.errors import ProjectNotFoundError
    tool = _tool("delete_project")
    with pytest.raises(ProjectNotFoundError):
        await tool.handler(gateway, {"project_id": "not-real", "confirm": True})


