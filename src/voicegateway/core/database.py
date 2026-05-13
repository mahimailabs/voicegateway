import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import src.models  # noqa: F401
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from src.core.config import Config

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, config: Config) -> None:
        self.config = config

        self._engine = create_async_engine(
            config.SQLALCHEMY_DATABASE_URI,
            echo=config.DEBUG,
            echo_pool=config.DEBUG,
            pool_pre_ping=True,
            pool_recycle=600,
            connect_args=config.SQLALCHEMY_CONNECT_ARGS,
        )

        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )

    async def create_database(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    async def dispose_pool(self) -> None:
        """Dispose the connection pool, forcing new connections to be created."""
        await self._engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        async with self._session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
