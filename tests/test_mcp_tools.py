"""Unit tests for all MCP tool handlers."""

from __future__ import annotations

import time
import uuid

import pytest

from voicegateway.core.gateway import Gateway
from voicegateway.mcp.errors import ValidationError
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
