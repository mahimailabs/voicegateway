"""Mount the dashboard FastAPI sub-app onto the main VG app.

No-op when the optional ``dashboard`` package is not installed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

    from voicegateway.core.gateway import Gateway

logger = logging.getLogger(__name__)


def mount_dashboard(app: FastAPI, gateway: Gateway) -> None:
    """Attach the dashboard FastAPI sub-app onto the main app's routes."""
    try:
        import dashboard.api.main as dash_mod
    except ImportError:
        logger.info("Dashboard not installed, skipping")
        return
    dash_mod._gateway = gateway
    for route in dash_mod.app.routes:
        app.routes.append(route)
    logger.info("Dashboard API mounted")


__all__ = ["mount_dashboard"]
