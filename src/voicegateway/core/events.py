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
    started: list[Any] = []
    app.state.workers = started
    try:
        for worker in workers:
            await worker.start()
            started.append(worker)
    except Exception:
        logger.exception(
            "Worker startup failed; stopping %d already-started worker(s)",
            len(started),
        )
        for worker in started:
            await worker.stop()
        raise
    if started:
        logger.info("Started %d background worker(s)", len(started))

    # ClickHouse client setup (runs after workers so a CH failure tears down
    # already-started workers in the finally/raise path above).
    ch_client = None
    if gateway is not None:
        cfg = gateway.config.clickhouse
        if cfg.host:
            try:
                import clickhouse_connect

                from voicegateway.clickhouse.migrate import apply_migrations

                ch_client = await clickhouse_connect.get_async_client(
                    host=cfg.host,
                    port=cfg.port,
                    username=cfg.username,
                    password=cfg.password,
                    database=cfg.database,
                )
                await apply_migrations(ch_client)
                logger.info(
                    "ClickHouse client ready: %s:%d/%s",
                    cfg.host,
                    cfg.port,
                    cfg.database,
                )
            except Exception:
                logger.exception("ClickHouse startup failed; continuing without it")
                ch_client = None
    app.state.ch_client = ch_client

    yield

    for worker in started:
        await worker.stop()
    if started:
        logger.info("Stopped %d background worker(s)", len(started))

    if ch_client is not None:
        try:
            await ch_client.close()
        except Exception:  # noqa: BLE001 - best-effort teardown
            pass
