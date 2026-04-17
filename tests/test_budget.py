"""Tests for voicegateway/middleware/budget_enforcer.py."""

import time
import uuid

import pytest

from voicegateway.core.config import GatewayConfig, ProjectConfig
from voicegateway.middleware.budget_enforcer import (
    BudgetEnforcer,
    BudgetExceededError,
    BudgetThrottleSignal,
)
from voicegateway.storage.models import RequestRecord
from voicegateway.storage.sqlite import SQLiteStorage


def _make_config(projects: dict[str, ProjectConfig]) -> GatewayConfig:
    return GatewayConfig(projects=projects)


@pytest.fixture
async def storage_with_spend(tmp_path):
    """Create storage with $5 spent today on 'expensive-project'."""
    storage = SQLiteStorage(str(tmp_path / "budget.db"))
    now = time.time()
    for i in range(5):
        await storage.log_request(RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=now - i,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            project="expensive-project",
            cost_usd=1.00,
        ))
    return storage


async def test_no_budget_no_action():
    """Project without daily_budget never raises."""
    config = _make_config({
        "free": ProjectConfig(id="free", name="Free", daily_budget=0.0),
    })
    enforcer = BudgetEnforcer(config, None)
    await enforcer.check_budget("free")  # should not raise


async def test_under_budget_no_action(tmp_path):
    """Under budget — no action taken."""
    storage = SQLiteStorage(str(tmp_path / "under.db"))
    config = _make_config({
        "test": ProjectConfig(id="test", name="Test", daily_budget=100.0),
    })
    enforcer = BudgetEnforcer(config, storage)
    await enforcer.check_budget("test")  # should not raise


async def test_warn_logs_warning(storage_with_spend, caplog):
    """Exceeded budget with action=warn logs a warning."""
    config = _make_config({
        "expensive-project": ProjectConfig(
            id="expensive-project", name="Expensive",
            daily_budget=2.0, budget_action="warn",
        ),
    })
    enforcer = BudgetEnforcer(config, storage_with_spend)
    import logging
    with caplog.at_level(logging.WARNING):
        await enforcer.check_budget("expensive-project")
    assert "exceeded daily budget" in caplog.text


async def test_block_raises_error(storage_with_spend):
    """Exceeded budget with action=block raises BudgetExceededError."""
    config = _make_config({
        "expensive-project": ProjectConfig(
            id="expensive-project", name="Expensive",
            daily_budget=2.0, budget_action="block",
        ),
    })
    enforcer = BudgetEnforcer(config, storage_with_spend)
    with pytest.raises(BudgetExceededError) as exc_info:
        await enforcer.check_budget("expensive-project")
    assert exc_info.value.project == "expensive-project"
    assert exc_info.value.spent_usd >= 5.0
    assert exc_info.value.budget_usd == 2.0


async def test_throttle_raises_signal(storage_with_spend):
    """Exceeded budget with action=throttle raises BudgetThrottleSignal."""
    config = _make_config({
        "expensive-project": ProjectConfig(
            id="expensive-project", name="Expensive",
            daily_budget=2.0, budget_action="throttle",
        ),
    })
    enforcer = BudgetEnforcer(config, storage_with_spend)
    with pytest.raises(BudgetThrottleSignal):
        await enforcer.check_budget("expensive-project")


async def test_cache_ttl_honored(storage_with_spend):
    """Cache avoids repeated DB queries within TTL."""
    config = _make_config({
        "expensive-project": ProjectConfig(
            id="expensive-project", name="Expensive",
            daily_budget=2.0, budget_action="warn",
        ),
    })
    enforcer = BudgetEnforcer(config, storage_with_spend, cache_ttl_seconds=60)

    # First call populates cache
    await enforcer.check_budget("expensive-project")
    assert "expensive-project" in enforcer._cache

    # Second call uses cache (no DB hit)
    cached_ts = enforcer._cache["expensive-project"][0]
    await enforcer.check_budget("expensive-project")
    assert enforcer._cache["expensive-project"][0] == cached_ts  # same timestamp = cache hit


def test_budget_status():
    config = _make_config({
        "test": ProjectConfig(id="test", name="Test", daily_budget=10.0),
    })
    enforcer = BudgetEnforcer(config, None)
    assert enforcer.get_budget_status("test", 5.0) == "ok"
    assert enforcer.get_budget_status("test", 8.5) == "warning"
    assert enforcer.get_budget_status("test", 12.0) == "exceeded"


def test_budget_status_no_budget():
    config = _make_config({
        "free": ProjectConfig(id="free", name="Free", daily_budget=0.0),
    })
    enforcer = BudgetEnforcer(config, None)
    assert enforcer.get_budget_status("free", 999.0) == "ok"


async def test_unknown_project_no_action():
    """Unknown project (not in config) is silently allowed."""
    config = _make_config({})
    enforcer = BudgetEnforcer(config, None)
    await enforcer.check_budget("unknown")  # should not raise
