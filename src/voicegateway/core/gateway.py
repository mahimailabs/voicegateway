"""Gateway: shared state container for the inference module, server, CLI, and MCP.

Pre-v0.0.5 the ``Gateway`` class also exposed ``stt`` / ``llm`` / ``tts``
factories. Those landed unmerged on ``feat/livekit-parity`` and never
shipped to PyPI; v0.0.5 makes ``voicegateway.inference`` the single
public surface and reduces this class to its internal role: own the
config, storage, cost tracker, latency monitor, rate limiter, logger,
and budget enforcer that the inference factories and operations
endpoints (CLI / HTTP / MCP / dashboard) share.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Coroutine
from typing import Any, TypeVar

from voicegateway.core.config import GatewayConfig, ProjectConfig
from voicegateway.core.config_manager import ConfigManager
from voicegateway.middleware.budget_enforcer import BudgetEnforcer
from voicegateway.middleware.cost_tracker import CostTracker
from voicegateway.middleware.latency_monitor import LatencyMonitor
from voicegateway.middleware.logger import RequestLogger
from voicegateway.middleware.rate_limiter import RateLimiter
from voicegateway.storage.sqlite import SQLiteStorage

T = TypeVar("T")

DEFAULT_PROJECT = "default"
DEFAULT_DB_PATH = "~/.config/voicegateway/voicegw.db"


class Gateway:
    """Shared internal container for the v0.0.5+ inference module.

    Not a public Python SDK. Library users construct providers via
    ``voicegateway.inference.STT/LLM/TTS``; the inference factory
    holds a process-wide singleton of this class and threads its
    cost tracker, storage, and budget enforcer into each wrapped
    plugin instance. Server processes (the FastAPI app, the MCP
    server, the CLI) instantiate it directly to read config or
    surface costs.
    """

    def __init__(self, config_path: str | None = None):
        """Initialize the gateway.

        Args:
            config_path: Path to voicegw.yaml. If None, searches:
                1. ./voicegw.yaml (and legacy ./gateway.yaml)
                2. ~/.config/voicegateway/voicegw.yaml
                3. /etc/voicegateway/voicegw.yaml
        """
        self._config = GatewayConfig.load(config_path)

        # Resolve DB path: env var > config > default
        cost_cfg = self._config.cost_tracking
        env_db = os.environ.get("VOICEGW_DB_PATH")
        enabled = cost_cfg.get("enabled", False) or bool(env_db)
        self._storage: SQLiteStorage | None
        if enabled:
            db_path = env_db or cost_cfg.get("db_path", DEFAULT_DB_PATH)
            self._storage = SQLiteStorage(db_path)
        else:
            self._storage = None

        # ConfigManager merges YAML + SQLite managed_* tables.
        self._config_manager = ConfigManager(self._config, self._storage)
        self._config = _run_async(self._config_manager.load_merged())

        # v0.0.5: ensure a "default" project always exists so the
        # inference resolver has something to charge against on a
        # fresh install. When YAML or the DB already configures one,
        # that takes precedence. With storage enabled we persist the
        # row so the dashboard, MCP tools, and HTTP API surface it
        # consistently with user-defined projects.
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

        self._cost_tracker = CostTracker(self._storage)
        self._latency_monitor = LatencyMonitor(
            ttfb_warning_ms=self._config.latency.get("ttfb_warning_ms", 500.0)
        )
        self._rate_limiter = RateLimiter(self._config.rate_limits)
        self._logger = RequestLogger()

        # Observability config — read once so the inference wrappers
        # can decide whether to skip the instrumentation hop.
        obs = self._config.observability
        self._latency_tracking = obs.get("latency_tracking", True)

        # Budget enforcement. Wired into the cost tracker so newly
        # logged requests update the enforcer's in-memory spend cache,
        # closing the TTL blind spot where a burst of requests can
        # race past the check.
        self._budget_enforcer = BudgetEnforcer(self._config, self._storage)
        self._cost_tracker.set_budget_enforcer(self._budget_enforcer)

    @property
    def config(self) -> GatewayConfig:
        """Return the gateway configuration."""
        return self._config

    @property
    def storage(self) -> SQLiteStorage | None:
        """Return the SQLite storage backend, if enabled."""
        return self._storage

    @property
    def cost_tracker(self) -> CostTracker:
        """Return the cost tracker."""
        return self._cost_tracker

    async def refresh_config(self) -> None:
        """Reload config from YAML + SQLite. Called after any managed_* write."""
        self._config = await self._config_manager.refresh()
        self._budget_enforcer = BudgetEnforcer(self._config, self._storage)
        self._cost_tracker.set_budget_enforcer(self._budget_enforcer)

    # ------------------------------------------------------------------
    # Query helpers (used by CLI / dashboard / MCP / HTTP API).
    # ------------------------------------------------------------------

    def costs(self, period: str = "today", project: str | None = None) -> dict:
        """Return cost summary for the given period, optionally filtered by project.

        Args:
            period: "today", "week", "month", or "all".
            project: Optional project ID to filter by.

        Returns:
            Dict with total cost, per-provider breakdown, per-model breakdown.
        """
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
        """Return configured projects as a list of serializable dicts.

        ``source`` is one of ``"yaml"``, ``"db"``, or ``"auto"``. The
        last comes from the v0.0.5 auto-create-default branch in
        ``__init__``; the dashboard renders a distinct badge for it
        so the user can tell their custom config apart from the
        gateway's first-run stub.
        """
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
