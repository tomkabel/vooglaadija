"""Temporary compatibility shim for database imports."""

from core.database import (
    Base,
    _EngineFactory,
    get_async_session,
    get_async_session_factory,
    get_db,
    get_engine,
)

__all__ = [
    "Base",
    "_EngineFactory",
    "get_async_session",
    "get_async_session_factory",
    "get_db",
    "get_engine",
]
