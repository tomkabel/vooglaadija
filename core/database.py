"""Database engine and session configuration shared by API and worker.

Uses lazy initialization to avoid creating the engine at import time,
which prevents issues with test environment overrides and ensures
the engine is created with the correct configuration.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.config import settings
from core.models.base import Base as CoreBase

Base = CoreBase


class _EngineFactory:
    """Lazy-initialized engine and session factory wrapper.

    Avoids module-level global state while providing singleton-like behavior.
    """

    def __init__(self) -> None:
        self._engine: AsyncEngine | None = None
        self._async_session_factory: async_sessionmaker[AsyncSession] | None = None

    def get_engine(self) -> AsyncEngine:
        """
        Provide the configured asynchronous database engine.
        
        Returns:
            AsyncEngine: The database engine.
        """
        if self._engine is None:
            self._engine = create_async_engine(
                settings.database_url,
                echo=False,
                future=True,
                **self._pool_kwargs(),
                pool_recycle=settings.db_pool_recycle,
                pool_pre_ping=True,
            )
        return self._engine

    def _pool_kwargs(self) -> dict[str, int]:
        """Return queue pool kwargs only for dialects that support them."""
        url = make_url(settings.database_url)
        if url.get_backend_name() == "sqlite":
            return {}
        return {
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_max_overflow,
            "pool_timeout": settings.db_pool_timeout,
        }

    def get_async_session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Get or create the async session factory (lazy initialization)."""
        if self._async_session_factory is None:
            self._async_session_factory = async_sessionmaker(
                self.get_engine(), class_=AsyncSession, expire_on_commit=False,
            )
        return self._async_session_factory


_factory = _EngineFactory()


def get_engine() -> AsyncEngine:
    """Get or create the async engine (lazy initialization)."""
    return _factory.get_engine()


def get_async_session_factory() -> async_sessionmaker[AsyncSession]:
    """
    Provide the shared asynchronous database session factory.
    
    Returns:
        async_sessionmaker[AsyncSession]: The session factory used to create asynchronous database sessions.
    """
    return _factory.get_async_session_factory()


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session."""
    async with _factory.get_async_session_factory()() as session:
        yield session


get_db = get_async_session

__all__ = [
    "Base",
    "get_async_session",
    "get_async_session_factory",
    "get_db",
    "get_engine",
]
