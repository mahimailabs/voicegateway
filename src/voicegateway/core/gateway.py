"""Gateway: shared state container for the inference module, server, CLI, and MCP."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Coroutine
from typing import Any, TypeVar

from voicegateway.billing.rate_card import RateCard
from voicegateway.core.config import GatewayConfig, ProjectConfig
from voicegateway.core.config_manager import ConfigManager
from voicegateway.middleware.budget_enforcer_middleware import BudgetEnforcer
from voicegateway.middleware.cost_tracker_middleware import CostTracker
from voicegateway.middleware.latency_monitor_middleware import LatencyMonitor
from voicegateway.middleware.logger_middleware import RequestLogger
from voicegateway.middleware.rate_limiter_middleware import RateLimiter
from voicegateway.services.sinks import LocalSqliteSink
from voicegateway.services.storage_service import StorageService

T = TypeVar("T")

DEFAULT_PROJECT = "default"
DEFAULT_DB_PATH = "~/.config/voicegateway/voicegw.db"


class Gateway:
    """Shared internal container for the inference module, server, CLI, and MCP."""

    def __init__(self, config_path: str | None = None, *, require_config: bool = True):
        """Initialize the gateway.

        ``require_config=False`` boots on built-in defaults when no config file
        exists, instead of raising. The server entrypoint passes it so the
        published image starts on a host that mounts no ``voicegw.yaml``; see
        :meth:`GatewayConfig.load`. Every other caller keeps the strict default,
        so a mistyped ``--config`` is still an error rather than a silent start
        with no providers.
        """
        self._config = GatewayConfig.load(config_path, required=require_config)

        # Resolve DB path: env var > config > default
        cost_cfg = self._config.cost_tracking
        env_db = os.environ.get("VOICEGW_DB_PATH")
        # A Postgres collector points at its DB with VOICEGW_DB_URL alone, so
        # that must enable storage too (resolve_database_url gives it precedence
        # over the sqlite db_path below).
        env_url = os.environ.get("VOICEGW_DB_URL")
        enabled = cost_cfg.get("enabled", False) or bool(env_db) or bool(env_url)
        self._storage: StorageService | None
        if enabled:
            db_path = env_db or cost_cfg.get("db_path", DEFAULT_DB_PATH)
            self._storage = StorageService(db_path, config=self._config)
        else:
            self._storage = None

        # ConfigManager merges YAML + SQLite managed_* tables.
        self._config_manager = ConfigManager(self._config, self._storage)
        self._config = _run_async(self._config_manager.load_merged())

        if DEFAULT_PROJECT not in self._config.projects:
            if self._storage is not None:
                _run_async(
                    self._storage.upsert_managed_project(
                        project_id=DEFAULT_PROJECT,
                        name="Default",
                        description="Auto-created on first run.",
                        daily_budget=0.0,
                        budget_action="warn",
                    )
                )
                self._config = _run_async(self._config_manager.refresh())
            else:
                self._config.projects[DEFAULT_PROJECT] = ProjectConfig(
                    id=DEFAULT_PROJECT,
                    name="Default",
                    source="auto",
                )

        # CostTracker writes through a Sink. In single-node mode that is the
        # LocalSqliteSink wrapping the embedded StorageService; fleet mode
        # swaps in a RemoteCollectorSink with no change to the producer.
        cost_sink = (
            LocalSqliteSink(self._storage) if self._storage is not None else None
        )
        self._cost_tracker = CostTracker(cost_sink)
        db_rate_rules = (
            _run_async(self._storage.list_rate_rules())
            if self._storage is not None
            else []
        )
        self._cost_tracker.set_rate_card(self._effective_rate_card(db_rate_rules))
        self._latency_monitor = LatencyMonitor(
            ttfb_warning_ms=self._config.latency.get("ttfb_warning_ms", 500.0)
        )
        self._rate_limiter = RateLimiter(self._config.rate_limits)
        self._logger = RequestLogger()

        obs = self._config.observability
        self._latency_tracking = obs.get("latency_tracking", True)

        self._budget_enforcer = BudgetEnforcer(self._config, self._storage)
        self._cost_tracker.set_budget_enforcer(self._budget_enforcer)

    @property
    def config(self) -> GatewayConfig:
        """Return the gateway configuration."""
        return self._config

    @property
    def storage(self) -> StorageService | None:
        """Return the SQLite storage backend, if enabled."""
        return self._storage

    @property
    def cost_tracker(self) -> CostTracker:
        """Return the cost tracker."""
        return self._cost_tracker

    def _effective_rate_card(self, db_rows: list[dict[str, Any]]) -> RateCard:
        """Build the rate card in effect: YAML seed rules + DB overrides.

        DB rows are appended after the seed rules so a DB override wins a
        specificity tie against a seed rule at the same scope.
        """
        return RateCard.from_config(self._config.rate_card).with_overrides(db_rows)

    async def refresh_config(self) -> None:
        """Reload config from YAML + SQLite. Called after any managed_* write."""
        self._config = await self._config_manager.refresh()
        self._budget_enforcer = BudgetEnforcer(self._config, self._storage)
        self._cost_tracker.set_budget_enforcer(self._budget_enforcer)
        db_rate_rules = (
            await self._storage.list_rate_rules() if self._storage is not None else []
        )
        self._cost_tracker.set_rate_card(self._effective_rate_card(db_rate_rules))

    def costs(self, period: str = "today", project: str | None = None) -> dict:
        """Return cost summary for the given period, optionally filtered by project."""
        if self._storage is None:
            return {
                "period": period,
                "project": project,
                "total": 0.0,
                "by_provider": {},
                "by_model": {},
            }
        return _run_async(self._storage.get_cost_summary(period, project=project))

    def list_projects(self) -> list[dict[str, Any]]:
        """Return configured projects as a list of serializable dicts."""
        result = []
        for pid, pcfg in self._config.projects.items():
            result.append(
                {
                    "id": pid,
                    "name": pcfg.name,
                    "description": pcfg.description,
                    "daily_budget": pcfg.daily_budget,
                    "default_stack": pcfg.default_stack,
                    "tags": list(pcfg.tags),
                    "accent": pcfg.accent,
                    "source": pcfg.source,
                    "budget_action": pcfg.budget_action,
                }
            )
        return result


def _run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine synchronously, even from inside a running loop."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return asyncio.run(coro)
    if loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)
