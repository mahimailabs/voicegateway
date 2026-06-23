"""Declarative dependency-injection container for the HTTP server stack."""

from __future__ import annotations

import logging

from dependency_injector import containers, providers

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database
from voicegateway.repository.api_key_repository import ApiKeyRepository
from voicegateway.services.api_key_service import ApiKeyService

logger = logging.getLogger(__name__)


def _load_gateway_config() -> GatewayConfig:
    """Adapter so ``providers.Singleton`` can construct the config eagerly."""
    return GatewayConfig.load()


class Container(containers.DeclarativeContainer):
    """Single source of truth for runtime singletons."""

    wiring_config = containers.WiringConfiguration(
        modules=[
            "voicegateway.server.api.api_keys",
        ],
    )

    config = providers.Singleton(_load_gateway_config)

    database = providers.Singleton(Database, config=config)

    # Repositories
    api_key_repository = providers.Factory(
        ApiKeyRepository,
        session_factory=database.provided.session,
    )

    # Services
    api_key_service = providers.Factory(
        ApiKeyService,
        repository=api_key_repository,
    )
