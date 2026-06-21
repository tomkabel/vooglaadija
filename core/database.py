"""Database engine and session configuration shared by API and worker.

Uses lazy initialization to avoid creating the engine at import time,
which prevents issues with test environment overrides and ensures
the engine is created with the correct configuration.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import settings
from core.models.base import Base as CoreBase

Base = CoreBase


class _EngineFactory:
    """Lazy-initialized engine and session factory wrapper.

    Avoids module-level global state while providing singleton-like behavior.
    """

    def __init__(self) -> None:
        self._engine = None
        self._async_session_factory = None

    def get_engine(self):
        """Get or create the async engine (lazy initialization)."""
        if self._engine is None:
            self._engine = create_async_engine(
                settings.database_url,
                echo=False,
                future=True,
                pool_size=10,
                max_overflow=5,
                pool_timeout=30,
                pool_recycle=1800,
                pool_pre_ping=True,
            )
        return self._engine

    def get_async_session_factory(self):
        """Get or create the async session factory (lazy initialization)."""
        if self._async_session_factory is None:
            self._async_session_factory = async_sessionmaker(
                self.get_engine(), class_=AsyncSession, expire_on_commit=False
            )
        return self._async_session_factory


_factory = _EngineFactory()


def get_engine():
    """Get or create the async engine (lazy initialization)."""
    return _factory.get_engine()


def get_async_session_factory():
    """Get or create the async session factory (lazy initialization)."""
    return _factory.get_async_session_factory()


async def get_async_session():
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
