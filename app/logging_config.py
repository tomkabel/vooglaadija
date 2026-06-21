"""Temporary compatibility shim for structured logging imports."""

from core.logging_config import (
    LoggerAdapter,
    add_service_context,
    add_timestamp,
    configure_logging,
    get_logger,
    rename_event_key,
)

__all__ = [
    "LoggerAdapter",
    "add_service_context",
    "add_timestamp",
    "configure_logging",
    "get_logger",
    "rename_event_key",
]
