"""Budget enforcement for project-based daily spending limits."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voicegateway.core.config import GatewayConfig, ProjectConfig
    from voicegateway.storage.sqlite import SQLiteStorage

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
    """Checks project spending against daily budgets.

    Supports three actions:
    - warn: log a warning, allow the request
    - throttle: log a warning, raise BudgetThrottleSignal (caller falls back to local)
    - block: raise BudgetExceededError, reject the request

    Budget checks are cached in memory for `cache_ttl_seconds` to avoid
    hitting the database on every single request.
    """

    def __init__(
        self,
        config: GatewayConfig,
        storage: SQLiteStorage | None,
        cache_ttl_seconds: float = 30.0,
    ):
        self._config = config
        self._storage = storage
        self._ttl = cache_ttl_seconds
        # Cache: project -> (timestamp, spend_usd)
        self._cache: dict[str, tuple[float, float]] = {}

    def _get_project_config(self, project: str) -> ProjectConfig | None:
        return self._config.get_project(project)

    async def _get_today_spend(self, project: str) -> float:
        """Get today's spend for a project, using cache if fresh."""
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

    async def check_budget(self, project: str) -> None:
        """Check project budget, take action if exceeded.

        Args:
            project: Project ID to check.

        Raises:
            BudgetExceededError: If action is 'block' and budget is exceeded.
            BudgetThrottleSignal: If action is 'throttle' and budget is exceeded.
        """
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
