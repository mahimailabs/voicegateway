"""Declarative dependency-injection container for the HTTP server stack."""

from __future__ import annotations

import logging

from dependency_injector import containers, providers

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database
from voicegateway.repository.virtual_key_repository import VirtualKeyRepository
from voicegateway.services.virtual_key_service import VirtualKeyService

logger = logging.getLogger(__name__)


def _load_gateway_config() -> GatewayConfig:
    """Adapter so ``providers.Singleton`` can construct the config eagerly."""
    return GatewayConfig.load()


class Container(containers.DeclarativeContainer):
    """Single source of truth for runtime singletons."""

    wiring_config = containers.WiringConfiguration(
        modules=[
            "voicegateway.server.routes.virtual_keys",
        ],
    )

    config = providers.Singleton(_load_gateway_config)

    database = providers.Singleton(Database, config=config)

    # Repositories
    virtual_key_repository = providers.Factory(
        VirtualKeyRepository,
        session_factory=database.provided.session,
    )

    # Services
    virtual_key_service = providers.Factory(
        VirtualKeyService,
        repository=virtual_key_repository,
    )
