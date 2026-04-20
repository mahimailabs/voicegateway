"""Tests for voicegateway/middleware/budget_enforcer.py."""

import asyncio
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


class _CountingStorage:
    """Minimal storage stub that counts get_cost_summary calls and lets the
    test control what spend each call reports."""

    def __init__(self, spend: float = 0.0):
        self.calls = 0
        self.spend = spend

    async def get_cost_summary(self, period: str, project: str | None = None):
        self.calls += 1
        # Simulate a slow-ish DB query so concurrent callers have time
        # to pile up behind the lock.
        await asyncio.sleep(0.01)
        return {"total": self.spend}


async def test_concurrent_check_budget_coalesces_storage_calls():
    """N concurrent budget checks for the same project hit storage once.

    Without the per-project lock, each racing task would see an empty
    cache and fire its own storage query; the cache writes would also
    race. Both behaviors are safety-relevant for the 'block' action.
    """
    config = _make_config({
        "expensive-project": ProjectConfig(
            id="expensive-project", name="Expensive",
            daily_budget=1.0, budget_action="warn",
        ),
    })
    storage = _CountingStorage(spend=0.5)
    enforcer = BudgetEnforcer(config, storage, cache_ttl_seconds=60)

    # 50 concurrent callers, all for the same project.
    await asyncio.gather(*(
        enforcer.check_budget("expensive-project") for _ in range(50)
    ))

    # All racing readers coalesce onto one storage query.
    assert storage.calls == 1
    # Cache populated exactly once.
    assert "expensive-project" in enforcer._cache


async def test_record_spend_updates_cache_within_ttl():
    """Post-request spend notifications close the TTL blind spot.

    Before: cache says $0.50 for 30s; a flood of in-flight requests all
    see $0.50 < $1.00 budget and sail through. After: each logged
    request increments the cached spend so the block kicks in promptly.
    """
    config = _make_config({
        "p": ProjectConfig(
            id="p", name="P", daily_budget=1.0, budget_action="block",
        ),
    })
    storage = _CountingStorage(spend=0.5)
    enforcer = BudgetEnforcer(config, storage, cache_ttl_seconds=60)

    # Warm the cache — under budget, so no raise.
    await enforcer.check_budget("p")

    # Simulate 10 logged requests at $0.10 each pushing us over $1.00.
    for _ in range(10):
        await enforcer.record_spend("p", 0.10)

    # Cache TTL has NOT expired, so without record_spend the check would
    # still see $0.50. With record_spend, the cache shows $1.50 and we
    # block. Storage should still only have been queried once.
    with pytest.raises(BudgetExceededError):
        await enforcer.check_budget("p")
    assert storage.calls == 1


async def test_record_spend_no_entry_is_noop():
    """record_spend before any check_budget is a no-op, not an error."""
    config = _make_config({
        "p": ProjectConfig(id="p", name="P", daily_budget=1.0),
    })
    enforcer = BudgetEnforcer(config, None)
    await enforcer.record_spend("p", 0.25)  # must not raise
    assert "p" not in enforcer._cache


async def test_record_spend_skips_when_cache_refreshed_after_write():
    """A refresh that happened AFTER storage.log_request already
    reflects the cost, so a late record_spend must not double-count.
    """
    import time as _time

    config = _make_config({
        "p": ProjectConfig(id="p", name="P", daily_budget=10.0),
    })
    storage = _CountingStorage(spend=2.0)
    enforcer = BudgetEnforcer(config, storage, cache_ttl_seconds=60)

    # Pre-warm cache (ts_old).
    await enforcer.check_budget("p")
    # Simulate the race: storage.log_request completed at t_write, then
    # a concurrent _get_today_spend refreshed the cache at a LATER moment
    # (ts_new > t_write) with the new total already including the cost.
    t_write = _time.monotonic()
    storage.spend = 2.5  # storage now includes the $0.50 write
    await enforcer.invalidate("p")
    await enforcer.check_budget("p")  # refreshes cache at ts_new > t_write
    cached_ts, cached_spend = enforcer._cache["p"]
    assert cached_ts > t_write
    assert cached_spend == 2.5

    # Late record_spend with logged_at=t_write: cached ts > logged_at,
    # so the increment must be skipped.
    await enforcer.record_spend("p", 0.50, logged_at=t_write)
    assert enforcer._cache["p"] == (cached_ts, 2.5)


async def test_record_spend_applies_when_cache_predates_write():
    """Warm cache (ts_old) + later write → increment applies."""
    import time as _time

    config = _make_config({
        "p": ProjectConfig(id="p", name="P", daily_budget=10.0),
    })
    storage = _CountingStorage(spend=2.0)
    enforcer = BudgetEnforcer(config, storage, cache_ttl_seconds=60)

    await enforcer.check_budget("p")
    cached_ts, _ = enforcer._cache["p"]
    later = _time.monotonic() + 1.0  # guaranteed > cached_ts
    await enforcer.record_spend("p", 0.50, logged_at=later)
    assert enforcer._cache["p"] == (cached_ts, 2.5)


async def test_invalidate_drops_cache(storage_with_spend):
    config = _make_config({
        "expensive-project": ProjectConfig(
            id="expensive-project", name="Expensive",
            daily_budget=2.0, budget_action="warn",
        ),
    })
    enforcer = BudgetEnforcer(config, storage_with_spend)
    await enforcer.check_budget("expensive-project")
    assert "expensive-project" in enforcer._cache
    await enforcer.invalidate("expensive-project")
    assert "expensive-project" not in enforcer._cache
