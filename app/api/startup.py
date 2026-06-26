"""Startup, shutdown, Sentry, and lifespan helpers for the API app."""

import asyncio
import os
import signal
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from core.logging_config import get_logger
from core.metrics import WORKER_STATUS, init_metrics
from core.redis_client import check_worker_health, close_redis_client

logger = get_logger(__name__)


class _ShutdownState:
    """Thread-safe shutdown state tracker."""

    def __init__(self) -> None:
        self._received: int = 0
        self._lock = threading.Lock()

    @property
    def received(self) -> int:
        with self._lock:
            return self._received

    def set(self, signum: int) -> None:
        with self._lock:
            self._received = signum


_shutdown_state = _ShutdownState()
_previous_signal_handlers: dict[int, Any] = {}


def _sigterm_handler(signum: int, frame: Any) -> None:
    """Handle SIGTERM/SIGINT for shutdown diagnostics."""
    _record_shutdown_signal(signum)


def _install_shutdown_diagnostics() -> None:
    """Install shutdown signal handlers safely from the main thread."""
    try:
        if threading.current_thread() is not threading.main_thread():
            return
        for signum in (signal.SIGTERM, signal.SIGINT):
            current_handler = signal.getsignal(signum)
            if current_handler is _chained_signal_handler:
                continue
            _previous_signal_handlers[signum] = signal.signal(signum, _chained_signal_handler)
        logger.info("shutdown_diagnostics_installed")
    except ValueError:
        logger.warning("shutdown_diagnostics_skipped_not_main_thread")


def _chained_signal_handler(signum: int, frame: Any) -> None:
    """Record shutdown diagnostics and chain to the prior signal handler."""
    _record_shutdown_signal(signum)
    previous_handler = _previous_signal_handlers.get(signum)
    if callable(previous_handler) and previous_handler is not _chained_signal_handler:
        try:
            previous_handler(signum, frame)
        except Exception:
            pass


def _record_shutdown_signal(signum: int) -> None:
    """Store and log the received shutdown signal."""
    _shutdown_state.set(signum)
    signal_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
    logger.warning(
        "shutdown_signal_received",
        signal=signal_name,
        signal_number=signum,
    )


def initialize_sentry(app_version: str) -> None:
    """Initialize Sentry in production when a DSN is configured."""
    environment = _get_environment()
    if environment != "production" or not os.environ.get("SENTRY_DSN"):
        return

    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.redis import RedisIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

    sentry_sdk.init(
        dsn=os.environ["SENTRY_DSN"],
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            SqlalchemyIntegration(),
            RedisIntegration(),
        ],
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
        environment=environment,
        release=f"vooglaadija@{app_version}",
    )
    logger.info("sentry_initialized", dsn_masked="***")


def _get_environment() -> str:
    """Read the deployment environment from the process environment."""
    return os.environ.get("ENVIRONMENT", "development")


def verify_templates_and_static_assets() -> None:
    """Log startup diagnostics for required template and static assets."""
    app_dir = Path(__file__).resolve().parents[1]
    template_dir = app_dir / "templates"
    static_dir = app_dir / "static"

    if not template_dir.exists():
        logger.error("templates_directory_missing", path=str(template_dir))
    else:
        required_templates = ["base.html", "login.html", "register.html", "dashboard.html"]
        missing = [name for name in required_templates if not (template_dir / name).exists()]
        if missing:
            logger.error("missing_templates", templates=missing, path=str(template_dir))
        else:
            logger.info("templates_verified", count=len(required_templates), path=str(template_dir))

    if not static_dir.exists():
        logger.error("static_directory_missing", path=str(static_dir))
    else:
        logger.info("static_directory_verified", path=str(static_dir))


async def poll_worker_health() -> None:
    """Poll Redis for worker heartbeats and update the worker status gauge."""
    backoff = 1
    max_interval = 15
    while True:
        try:
            is_healthy = await check_worker_health()
            WORKER_STATUS.set(1 if is_healthy else 0)
            backoff = 1
        except Exception:
            WORKER_STATUS.set(0)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_interval)
            continue
        await asyncio.sleep(15)


def start_worker_health_poller() -> asyncio.Task[None]:
    """Create the worker health polling task during lifespan startup."""
    return asyncio.create_task(poll_worker_health())


async def stop_worker_health_poller(health_poller: asyncio.Task[None]) -> None:
    """Cancel and await the worker health polling task during shutdown."""
    health_poller.cancel()
    try:
        await health_poller
    except asyncio.CancelledError:
        pass
    WORKER_STATUS.set(0)


async def close_api_resources() -> None:
    """Close pub/sub before shared Redis while swallowing cleanup failures."""
    try:
        from app.services.pubsub_service import close_pubsub_service

        await close_pubsub_service()
        await close_redis_client()
    except Exception:
        pass


def create_lifespan(
    app_version: str, uvloop_available: bool,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None, bool | None]]:
    """Create the FastAPI lifespan context manager for app assembly."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "application_starting",
            version=app_version,
            environment=_get_environment(),
            uvloop_available=uvloop_available,
        )
        init_metrics()
        verify_templates_and_static_assets()
        _install_shutdown_diagnostics()

        health_poller = start_worker_health_poller()
        try:
            yield
        finally:
            await stop_worker_health_poller(health_poller)
            await close_api_resources()

            logger.info(
                "application_shutting_down",
                shutdown_signal=_shutdown_state.received,
            )

    return lifespan
