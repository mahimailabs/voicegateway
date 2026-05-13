"""FastAPI ``lifespan`` that initializes and tears down container resources.

Bound to the :class:`AppCreator` at construction time. Idempotent.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from voicegateway.core.logging_conf import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Wire container resources at startup, dispose at shutdown."""
    configure_logging()

    container = getattr(app.state, "container", None)
    if container is not None:
        container.init_resources()
        logger.info("Container resources initialized")

    yield

    if container is not None:
        container.shutdown_resources()
        logger.info("Container resources shutdown")
