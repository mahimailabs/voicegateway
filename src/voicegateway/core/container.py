"""Declarative dependency-injection container for the HTTP server stack.

Built on :mod:`dependency_injector`. The container owns every singleton
the server needs (config, database, repositories, services) and wires
them into the FastAPI routers via ``@inject`` + ``Provide[]``.

Start of the per-entity migration is intentionally small: only the
``config`` and ``database`` providers are declared today. Repository
and service providers are added as entities move off the legacy
aiosqlite paths.
"""

from __future__ import annotations

import logging

from dependency_injector import containers, providers

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database

logger = logging.getLogger(__name__)


def _load_gateway_config() -> GatewayConfig:
    """Adapter so ``providers.Singleton`` can construct the config eagerly."""
    return GatewayConfig.load()


class Container(containers.DeclarativeContainer):
    """Single source of truth for runtime singletons."""

    wiring_config = containers.WiringConfiguration(
        # Populated as each api/* router is migrated onto @inject. The
        # first entity-migration commit appends the matching module path
        # here (e.g. "voicegateway.server.routes.virtual_keys").
        modules=[],
    )

    config = providers.Singleton(_load_gateway_config)

    database = providers.Singleton(Database, config=config)
