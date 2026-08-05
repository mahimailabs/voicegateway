"""FastAPI ``lifespan``: start and stop the collector's background workers.

Reads the StorageService off ``app.state.gateway`` (set during build) and, when
cost tracking is enabled and workers are not disabled, starts the latency and
agent rollup workers plus the retention worker, stopping them on shutdown. The
poll intervals come from the ``workers`` config; retention runs only when
enabled and prunes every project that has data at ``retention.default_days``.

The Prometheus node scrape is the one worker that is not started by default:
it is the only one that talks to the network, so it is built only when
``VOICEGW_NODE_SCRAPE_TARGETS`` names at least one target. An install that
never sets the variable behaves exactly as it did before the worker existed.
"""

from __future__ import annotations

import logging
import os
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
from voicegateway.middleware.node_samples_worker_middleware import (
    TARGETS_ENV_VAR,
    TARGETS_FILE_ENV_VAR,
    NodeSamplesWorker,
    targets_from_env,
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

    # The node scrape is opt-in, and opting in is naming targets: it is the only
    # worker here that makes outbound HTTP requests, so an install that upgrades
    # into this code must not start talking to the network on its own. No
    # targets, no worker at all (rather than a worker idling on empty ticks), so
    # the default process has exactly the tasks it had before.
    # Naming a targets FILE also opts in, and does so even when the file is
    # empty or absent right now. That is the difference between the two sources:
    # an env var is everything there will ever be, while a file is whatever the
    # thing that writes it has got to so far. Refusing to start on an empty file
    # would mean the collector had to be restarted once the file filled, which is
    # the restart this whole option exists to remove.
    targets_file = os.environ.get(TARGETS_FILE_ENV_VAR, "").strip()
    node_targets = targets_from_env()
    if targets_file or node_targets:
        workers.append(
            NodeSamplesWorker(
                storage,
                poll_interval_seconds=workers_cfg.node_scrape_interval_seconds,
            )
        )
        # Logged because the alternative is silence: an operator who typos the
        # variable otherwise cannot tell "not read" from "read and empty".
        if targets_file:
            logger.info(
                "Node scrape enabled: targets re-read from %s (%s) every tick",
                TARGETS_FILE_ENV_VAR,
                targets_file,
            )
        else:
            logger.info(
                "Node scrape enabled: %d target(s) from %s",
                len(node_targets),
                TARGETS_ENV_VAR,
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

    # Write out whatever the call-observations endpoint has accepted but not yet
    # flushed. Every queued report was answered with a 202, so a bounded drain is
    # what keeps that answer honest; the call is a no-op when nothing was ever
    # enqueued, and it never raises into shutdown.
    # Imported here, not at module scope: ``core`` must not import ``server`` at
    # load time (the dependency runs the other way).
    try:
        from voicegateway.server.api.call_observations import (
            shutdown_call_observations,
        )

        await shutdown_call_observations()
    except Exception:  # noqa: BLE001 - teardown must not fail on telemetry
        logger.warning("call observations shutdown failed", exc_info=True)

    for worker in started:
        await worker.stop()
    if started:
        logger.info("Stopped %d background worker(s)", len(started))

    if ch_client is not None:
        try:
            await ch_client.close()
        except Exception:  # noqa: BLE001 - best-effort teardown
            pass
