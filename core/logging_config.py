"""Structured logging configuration using structlog.

This module provides structured logging for the Vooglaadija application.
In production, logs are JSON formatted for log aggregation systems.
In development, logs are human-readable with colors.

Usage:
    from core.logging_config import get_logger

    logger = get_logger(__name__)
    logger.info("user_login", user_id=123)  # Structured with context

    # With bound context
    logger = get_logger(__name__, request_id="abc")
    logger.info("processing_request")  # Automatically includes request_id
"""

import logging
import os
import sys
from collections.abc import MutableMapping
from datetime import UTC
from typing import TYPE_CHECKING, Any

import structlog
from structlog.types import Processor

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger


def add_timestamp(
    logger: Any,
    method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Add ISO timestamp to all log entries."""
    from datetime import datetime

    event_dict["timestamp"] = datetime.now(UTC).isoformat()
    return event_dict


def add_service_context(
    logger: Any,
    method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Add service context to all log entries."""
    event_dict["service"] = "vooglaadija"
    event_dict["environment"] = os.environ.get("ENVIRONMENT", "development")
    return event_dict


def rename_event_key(
    logger: Any,
    method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Rename 'event' key to 'message' for standard log aggregation compatibility.

    structlog uses 'event' as the default key for log messages.
    This processor renames it to 'message' for compatibility with most
    log aggregation systems (ELK, Datadog, etc.)
    """
    if "event" in event_dict:
        event_dict["message"] = event_dict.pop("event")
    return event_dict


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structlog for the application.

    Production: JSON structured logs for log aggregation
    Development: Human-readable colored output

    Args:
        log_level: Minimum log level to output (default: INFO)

    """
    is_production = os.environ.get("ENVIRONMENT", "development") == "production"

    # Shared processors for all environments
    shared_processors: list[Processor] = [
        # Merge context variables from structlog context managers
        structlog.contextvars.merge_contextvars,
        # Add log level
        structlog.stdlib.add_log_level,
        # Add logger name
        structlog.stdlib.add_logger_name,
        # Handle positional arguments in log calls
        structlog.stdlib.PositionalArgumentsFormatter(),
        # Add stack info for debugging
        structlog.processors.StackInfoRenderer(),
        # Decode bytes to strings
        structlog.processors.UnicodeDecoder(),
        # Add timestamp
        add_timestamp,
        # Add service context
        add_service_context,
        # Rename 'event' to 'message' for standard compatibility
        rename_event_key,
    ]

    if is_production:
        # Production: JSON output with exception formatting
        shared_processors.append(structlog.processors.format_exc_info)

        structlog.configure(
            processors=[
                *shared_processors,
                # Final renderer - JSON output
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )
    else:
        # Development: Pretty console output with colors
        # Create a fresh processors list to avoid duplicates if configure_logging is called multiple times
        dev_processors: list[Processor] = [
            *shared_processors,
            structlog.dev.ConsoleRenderer(
                colors=True,
                exception_formatter=structlog.dev.plain_traceback,
            ),
        ]

        structlog.configure(
            processors=dev_processors,
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=False,
        )

    # Configure standard library logging
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
        force=True,
    )

    # Suppress noisy third-party loggers
    for logger_name in ["uvicorn.access", "httpx", "httpcore", "aiohttp", "urllib3"]:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def get_logger(name: str | None = None, **kwargs: Any) -> "BoundLogger":
    """Get a structured logger instance.

    Args:
        name: Logger name (typically __name__ for the module)
        **kwargs: Additional context to bind to all log messages

    Returns:
        Configured structlog BoundLogger

    Example:
        logger = get_logger(__name__)
        logger.info("user_action", action="login", user_id=123)

        # With bound context
        logger = get_logger(__name__, request_id="abc-123")
        logger.info("processing")  # All logs include request_id

        # Context manager for temporary context
        from structlog.contextvars import bind_contextvars
        with bind_contextvars(user_id=123, request_id="abc"):
            logger.info("within_context")  # Includes both user_id and request_id

    """
    logger = structlog.get_logger(name)
    if kwargs:
        return logger.bind(**kwargs)  # type: ignore[no-any-return]
    return logger  # type: ignore[no-any-return]


# Backward compatibility alias for type hints
LoggerAdapter = structlog.stdlib.BoundLogger
