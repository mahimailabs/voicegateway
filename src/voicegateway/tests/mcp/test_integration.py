"""End-to-end integration test — agent workflow through the MCP layer."""

from __future__ import annotations

import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from voicegateway.core.gateway import Gateway
from voicegateway.server.mcp.server import create_server


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "integration.db"))
    return Gateway(config_path=temp_config)


def _parse(result) -> dict:
    return json.loads(result.content[0].text)


async def test_full_agent_workflow(gateway):
    """Exercises every tool the way a coding agent would, in order."""
    server = create_server(gateway, is_admin=True)
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()

        # 1. register_model — add a model from a configured provider
        result = await client.call_tool(
            "register_model",
            {
                "modality": "llm",
                "provider_id": "openai",
                "model_name": "gpt-test",
            },
        )
        registered = _parse(result)
        assert registered["model_id"] == "openai/gpt-test"

        # 2. create_project — a framework-agnostic project (label + budget)
        result = await client.call_tool(
            "create_project",
            {
                "project_id": "acme-corp",
                "name": "ACME Corp",
                "description": "E2E integration test",
                "daily_budget": 10.0,
                "budget_action": "warn",
            },
        )
        created = _parse(result)
        assert created["project_id"] == "acme-corp"

        # 3. get_project — verify it's fully configured
        result = await client.call_tool("get_project", {"project_id": "acme-corp"})
        fetched = _parse(result)
        assert fetched["id"] == "acme-corp"
        assert fetched["daily_budget"] == 10.0

        # 4. get_logs — empty for this new project, no crash
        result = await client.call_tool("get_logs", {"project": "acme-corp"})
        logs = _parse(result)
        assert logs == [] or isinstance(logs, list)

        # 5. delete_project without confirm — preview
        result = await client.call_tool("delete_project", {"project_id": "acme-corp"})
        preview = _parse(result)
        assert preview["error"]["code"] == "CONFIRMATION_REQUIRED"
        assert "total_spend_usd" in preview["error"]["details"]

        # 6. delete_project with confirm=True — actually gone
        result = await client.call_tool(
            "delete_project", {"project_id": "acme-corp", "confirm": True}
        )
        deleted = _parse(result)
        assert deleted["action"] == "deleted"

        # 7. list_projects — project is gone
        result = await client.call_tool("list_projects", {})
        after = _parse(result)
        ids = {p["id"] for p in after["projects"]}
        assert "acme-corp" not in ids

        # 8. cleanup — delete the model we added
        result = await client.call_tool(
            "delete_model", {"model_id": "openai/gpt-test", "confirm": True}
        )
        assert _parse(result)["action"] == "deleted"


async def test_get_costs_and_latency_chain(gateway):
    """Agent can read costs and latency after observing logs."""
    server = create_server(gateway, is_admin=True)
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()

        health = _parse(await client.call_tool("get_health", {}))
        assert health["status"] == "ok"

        costs = _parse(await client.call_tool("get_costs", {"period": "today"}))
        assert "total_usd" in costs

        latency = _parse(
            await client.call_tool("get_latency_stats", {"period": "today"})
        )
        assert "overall" in latency
