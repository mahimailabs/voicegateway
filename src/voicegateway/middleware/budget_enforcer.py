"""Budget enforcement for project-based daily spending limits."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voicegateway.core.config import GatewayConfig, ProjectConfig
    from voicegateway.services.storage_service import StorageService

logger = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """Raised when a project's daily budget is exceeded and action is 'block'."""

    def __init__(self, project: str, spent_usd: float, budget_usd: float):
        self.project = project
        self.spent_usd = spent_usd
        self.budget_usd = budget_usd
        super().__init__(
            f"Project '{project}' exceeded daily budget: "
            f"${spent_usd:.2f} / ${budget_usd:.2f}"
        )


class BudgetThrottleSignal(Exception):
    """Raised to signal the caller should fall back to the local stack."""

    def __init__(self, project: str, spent_usd: float, budget_usd: float):
        self.project = project
        self.spent_usd = spent_usd
        self.budget_usd = budget_usd
        super().__init__(
            f"Project '{project}' exceeded budget, throttling to local stack: "
            f"${spent_usd:.2f} / ${budget_usd:.2f}"
        )


class BudgetEnforcer:
    """Checks project spending against daily budgets."""

    def __init__(
        self,
        config: GatewayConfig,
        storage: StorageService | None,
        cache_ttl_seconds: float = 30.0,
    ):
        self._config = config
        self._storage = storage
        self._ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    def _get_project_config(self, project: str) -> ProjectConfig | None:
        return self._config.get_project(project)

    async def _lock_for(self, project: str) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._locks.get(project)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[project] = lock
            return lock

    async def _get_today_spend(self, project: str) -> float:
        """Get today's spend for a project, using cache if fresh."""
        lock = await self._lock_for(project)
        async with lock:
            now = time.monotonic()
            cached = self._cache.get(project)
            if cached is not None:
                ts, spend = cached
                if now - ts < self._ttl:
                    return spend

            if self._storage is None:
                return 0.0

            summary = await self._storage.get_cost_summary("today", project=project)
            spend = float(summary.get("total", 0.0))
            self._cache[project] = (now, spend)
            return spend

    async def record_spend(
        self,
        project: str,
        cost_usd: float,
        logged_at: float | None = None,
    ) -> None:
        """Optimistically update the cached spend after a request is logged."""
        if cost_usd <= 0:
            return
        if logged_at is None:
            logged_at = time.monotonic()
        lock = await self._lock_for(project)
        async with lock:
            cached = self._cache.get(project)
            if cached is None:
                return
            ts, spend = cached
            if ts >= logged_at:
                return
            self._cache[project] = (ts, spend + cost_usd)

    async def invalidate(self, project: str | None = None) -> None:
        """Drop cached spend for ``project`` (or all projects if None)."""
        if project is None:
            async with self._locks_guard:
                projects = list(self._cache.keys())
        else:
            projects = [project]
        for p in projects:
            lock = await self._lock_for(p)
            async with lock:
                self._cache.pop(p, None)

    async def check_budget(self, project: str) -> None:
        """Check project budget, take action if exceeded."""
        pcfg = self._get_project_config(project)
        if pcfg is None:
            return
        if pcfg.daily_budget <= 0:
            return  # no budget = unlimited

        today_spend = await self._get_today_spend(project)

        if today_spend < pcfg.daily_budget:
            return  # under budget

        action = pcfg.budget_action

        if action == "warn":
            logger.warning(
                "Project '%s' exceeded daily budget: $%.2f / $%.2f",
                project,
                today_spend,
                pcfg.daily_budget,
            )
        elif action == "throttle":
            logger.warning(
                "Project '%s' exceeded budget, throttling: $%.2f / $%.2f",
                project,
                today_spend,
                pcfg.daily_budget,
            )
            raise BudgetThrottleSignal(project, today_spend, pcfg.daily_budget)
        elif action == "block":
            raise BudgetExceededError(project, today_spend, pcfg.daily_budget)

    def get_budget_status(self, project: str, today_spend: float) -> str:
        """Return budget status string for API response."""
        pcfg = self._get_project_config(project)
        if pcfg is None or pcfg.daily_budget <= 0:
            return "ok"
        ratio = today_spend / pcfg.daily_budget
        if ratio >= 1.0:
            return "exceeded"
        if ratio >= 0.8:
            return "warning"
        return "ok"
