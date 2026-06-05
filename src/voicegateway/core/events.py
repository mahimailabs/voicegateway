"""FastAPI ``lifespan``: start and stop the collector's background workers.

Reads the StorageService off ``app.state.gateway`` (set during build) and, when
cost tracking is enabled and workers are not disabled, starts the latency and
agent rollup workers plus the retention worker, stopping them on shutdown. The
poll intervals come from the ``workers`` config; retention runs only when
enabled and prunes every project that has data at ``retention.default_days``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from sqlalchemy import text

from voicegateway.core.logging_conf import configure_logging
from voicegateway.middleware.agent_observations_worker_middleware import (
    AgentObservationsWorker,
)
from voicegateway.middleware.latency_observations_worker_middleware import (
    LatencyObservationsWorker,
)
from voicegateway.services.retention_service import RetentionWorker

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

logger = logging.getLogger(__name__)


def _build_workers(gateway: Gateway) -> list[Any]:
    """Construct the background workers for a gateway, or [] when disabled."""
    storage = gateway.storage
    workers_cfg = gateway.config.workers
    if storage is None or not workers_cfg.enabled:
        return []

    rollup_interval = workers_cfg.rollup_interval_seconds
    workers: list[Any] = [
        LatencyObservationsWorker(storage, poll_interval_seconds=rollup_interval),
        AgentObservationsWorker(storage, poll_interval_seconds=rollup_interval),
    ]

    retention_cfg = gateway.config.retention
    if retention_cfg.enabled:

        async def _retention_provider() -> list[tuple[str, int]]:
            days = retention_cfg.default_days
            await storage._ensure_initialized()
            async with storage._conn.session() as db:
                result = await db.execute(
                    text(
                        "SELECT project FROM requests WHERE project IS NOT NULL "
                        "UNION SELECT project FROM sessions WHERE project IS NOT NULL"
                    )
                )
                projects = [str(row[0]) for row in result]
            return [(p, days) for p in projects]

        workers.append(
            RetentionWorker(
                storage,
                retention_provider=_retention_provider,
                default_retention_days=retention_cfg.default_days,
                poll_interval_seconds=workers_cfg.retention_interval_seconds,
            )
        )
    return workers


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start the collector's background workers; stop them on shutdown."""
    configure_logging()
    gateway = getattr(app.state, "gateway", None)
    workers = _build_workers(gateway) if gateway is not None else []
    for worker in workers:
        await worker.start()
    app.state.workers = workers
    if workers:
        logger.info("Started %d background worker(s)", len(workers))

    yield

    for worker in workers:
        await worker.stop()
    if workers:
        logger.info("Stopped %d background worker(s)", len(workers))
