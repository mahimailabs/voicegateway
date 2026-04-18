"""Main Gateway class — entry point for VoiceGateway."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Coroutine
from typing import Any, TypeVar

from voicegateway.core.config import GatewayConfig
from voicegateway.core.config_manager import ConfigManager
from voicegateway.core.router import (
    Router,
)
from voicegateway.middleware.budget_enforcer import BudgetEnforcer
from voicegateway.middleware.cost_tracker import CostTracker
from voicegateway.middleware.fallback import FallbackChain
from voicegateway.middleware.instrumented_provider import wrap_provider
from voicegateway.middleware.latency_monitor import LatencyMonitor
from voicegateway.middleware.logger import RequestLogger
from voicegateway.middleware.rate_limiter import RateLimiter
from voicegateway.storage.sqlite import SQLiteStorage

T = TypeVar("T")

DEFAULT_PROJECT = "default"
DEFAULT_DB_PATH = "~/.config/voicegateway/voicegw.db"


class Gateway:
    """Self-hosted inference gateway for voice AI agents.

    Routes STT, LLM, and TTS requests to cloud providers (with your API keys)
    or local models. Provides fallback chains, project-based cost tracking,
    and latency monitoring.
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
        self._router = Router(self._config)

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

        # ConfigManager for merging YAML + SQLite managed resources
        self._config_manager = ConfigManager(self._config, self._storage)

        # Merge managed resources from SQLite at startup
        self._config = _run_async(self._config_manager.load_merged())
        self._router = Router(self._config)

        self._cost_tracker = CostTracker(self._storage)
        self._latency_monitor = LatencyMonitor(
            ttfb_warning_ms=self._config.latency.get("ttfb_warning_ms", 500.0)
        )
        self._rate_limiter = RateLimiter(self._config.rate_limits)
        self._logger = RequestLogger()

        # Observability config
        obs = self._config.observability
        self._latency_tracking = obs.get("latency_tracking", True)

        # Budget enforcement
        self._budget_enforcer = BudgetEnforcer(self._config, self._storage)

        # Build fallback chains
        self._fallback_chains: dict[str, FallbackChain] = {}
        for modality, chain in self._config.fallbacks.items():
            if chain:
                self._fallback_chains[modality] = FallbackChain(
                    chain=chain,
                    resolver=self._router.resolve,
                    modality=modality,
                    on_fallback=self._logger.log_fallback,
                )

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
        self._router = Router(self._config)
        self._budget_enforcer = BudgetEnforcer(self._config, self._storage)
        # Rebuild fallback chains so they capture the new router.resolve
        for modality, chain_list in self._config.fallbacks.items():
            if chain_list:
                self._fallback_chains[modality] = FallbackChain(
                    chain=chain_list,
                    resolver=self._router.resolve,
                    modality=modality,
                    on_fallback=self._logger.log_fallback,
                )

    # ------------------------------------------------------------------
    # Model factories
    # ------------------------------------------------------------------

    def _wrap(self, instance: Any, model_id: str, modality: str, project: str) -> Any:
        """Wrap a provider instance with instrumentation if enabled."""
        if not self._latency_tracking:
            return instance
        from voicegateway.core.model_id import ModelId

        parsed = ModelId.parse(model_id)
        return wrap_provider(
            instance=instance,
            modality=modality,
            model_id=model_id,
            provider=parsed.provider,
            project=project,
            cost_tracker=self._cost_tracker,
            storage=self._storage,
        )

    def _check_budget(self, project: str) -> None:
        """Check project budget before resolving a model."""
        if project == DEFAULT_PROJECT:
            return
        _run_async(self._budget_enforcer.check_budget(project))

    def stt(self, model_id: str, project: str | None = None, **kwargs: Any) -> Any:
        """Create an STT instance for the given model ID.

        Args:
            model_id: "provider/model[:language]" format.
            project: Optional project ID to tag requests with.
            **kwargs: Additional provider-specific options.
        """
        proj = self._resolve_project(project)
        self._check_budget(proj)
        instance = self._router.resolve(model_id, "stt", project=proj, **kwargs)
        return self._wrap(instance, model_id, "stt", proj)

    def llm(self, model_id: str, project: str | None = None, **kwargs: Any) -> Any:
        """Create an LLM instance for the given model ID.

        Args:
            model_id: "provider/model" format.
            project: Optional project ID to tag requests with.
            **kwargs: Additional provider-specific options.
        """
        proj = self._resolve_project(project)
        self._check_budget(proj)
        instance = self._router.resolve(model_id, "llm", project=proj, **kwargs)
        return self._wrap(instance, model_id, "llm", proj)

    def tts(self, model_id: str, project: str | None = None, **kwargs: Any) -> Any:
        """Create a TTS instance for the given model ID.

        Args:
            model_id: "provider/model[:voice_id]" format.
            project: Optional project ID to tag requests with.
            **kwargs: Additional provider-specific options.
        """
        proj = self._resolve_project(project)
        self._check_budget(proj)
        instance = self._router.resolve(model_id, "tts", project=proj, **kwargs)
        return self._wrap(instance, model_id, "tts", proj)

    def stack(
        self, name: str, project: str | None = None, **kwargs: Any
    ) -> tuple[Any, Any, Any]:
        """Resolve a named stack (e.g. 'premium', 'budget', 'local') into (stt, llm, tts).

        Stacks are defined in voicegw.yaml under the `stacks:` section, e.g.::

            stacks:
              premium:
                stt: deepgram/nova-3
                llm: openai/gpt-4.1-mini
                tts: cartesia/sonic-3
        """
        stacks = self._config.stacks
        if name not in stacks:
            raise ValueError(
                f"Stack '{name}' not defined. Available: {', '.join(sorted(stacks))}"
            )
        stack = stacks[name]
        proj = self._resolve_project(project)
        stt = (
            self._router.resolve(stack["stt"], "stt", project=proj, **kwargs)
            if "stt" in stack
            else None
        )
        llm = (
            self._router.resolve(stack["llm"], "llm", project=proj, **kwargs)
            if "llm" in stack
            else None
        )
        tts = (
            self._router.resolve(stack["tts"], "tts", project=proj, **kwargs)
            if "tts" in stack
            else None
        )
        return stt, llm, tts

    def stt_with_fallback(self, project: str | None = None, **kwargs: Any) -> Any:
        """Create an STT instance using the configured fallback chain."""
        chain = self._fallback_chains.get("stt")
        if not chain:
            raise ValueError("No STT fallback chain configured")
        return chain.resolve(project=self._resolve_project(project), **kwargs)

    def llm_with_fallback(self, project: str | None = None, **kwargs: Any) -> Any:
        """Create an LLM instance using the configured fallback chain."""
        chain = self._fallback_chains.get("llm")
        if not chain:
            raise ValueError("No LLM fallback chain configured")
        return chain.resolve(project=self._resolve_project(project), **kwargs)

    def tts_with_fallback(self, project: str | None = None, **kwargs: Any) -> Any:
        """Create a TTS instance using the configured fallback chain."""
        chain = self._fallback_chains.get("tts")
        if not chain:
            raise ValueError("No TTS fallback chain configured")
        return chain.resolve(project=self._resolve_project(project), **kwargs)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def status(self, project: str | None = None) -> dict:
        """Return status of all configured providers.

        Args:
            project: Currently unused (kept for API parity with costs()).
        """
        return self._router.get_provider_status()

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
                }
            )
        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_project(self, project: str | None) -> str:
        """Return the effective project id (validates against config if provided)."""
        if project is None:
            return DEFAULT_PROJECT
        if self._config.projects and project not in self._config.projects:
            # Allow unknown projects for flexibility, but only the configured ones
            # get CLI/dashboard treatment.
            pass
        return project


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
