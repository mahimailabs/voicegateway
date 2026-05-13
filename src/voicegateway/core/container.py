import logging

from dependency_injector import containers, providers
from src.core.config import get_config
from src.core.database import Database

logger = logging.getLogger(__name__)


def _create_redis_client(redis_url: str | None = None):  # type: ignore[no-untyped-def]
    """Create an async Redis client, or return None if not configured."""
    if not redis_url:
        return None
    try:
        import redis.asyncio as aioredis

        url = (
            redis_url.get_secret_value()
            if hasattr(redis_url, "get_secret_value")
            else str(redis_url)
        )
        if not url:
            return None
        return aioredis.from_url(url)
    except Exception:
        logger.warning("Failed to create Redis client", exc_info=True)
        return None


class Container(containers.DeclarativeContainer):
    wiring_config = containers.WiringConfiguration(
        modules=[],
    )

    config = providers.Singleton(get_config)

    database = providers.Singleton(Database, config=config)

    # Repositories
