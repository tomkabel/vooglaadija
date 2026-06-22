"""Worker health monitoring via Redis heartbeat and HTTP health endpoint."""

import asyncio
import json
import os
import threading
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeVar

import uvicorn
from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from core.config import settings
from core.database import get_async_session_factory
from core.logging_config import get_logger
from core.redis_client import close_redis_client, get_redis_client, reset_redis_client

logger = get_logger(__name__)

health_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# Module-level lock for thread-safe access to _worker_state
_state_lock = threading.Lock()

# Global state updated by the worker main loop
_worker_state = {
    "status": "starting",
    "last_heartbeat": None,
    "current_job_started_at": None,
    "last_job_processed": None,
    "last_cleanup": None,
    "pid": os.getpid(),
}

_start_time = datetime.now(UTC)
_health_server = None
_health_server_thread: threading.Thread | None = None
_worker_loop: asyncio.AbstractEventLoop | None = None

T = TypeVar("T")

# Re-export shared client lifecycle for worker/main.py
close_health_redis_client = close_redis_client
reset_health_redis_client = reset_redis_client


def update_worker_state(**kwargs):
    """Update worker state for health reporting (thread-safe)."""
    with _state_lock:
        _worker_state.update(kwargs)
        _worker_state["last_heartbeat"] = datetime.now(UTC).isoformat()


def get_redis_url() -> str:
    """Get Redis URL from the canonical settings singleton."""
    return settings.redis_url


def get_worker_id() -> str:
    """Get worker ID from environment or default."""
    return os.environ.get("WORKER_ID", "worker-1")


def write_health_sync() -> bool:
    """Write worker health (synchronous version for shell scripts)."""
    import redis

    worker_id = get_worker_id()

    health_data = {
        "worker_id": worker_id,
        "status": "healthy",
        "timestamp": datetime.now(UTC).isoformat(),
        "pid": os.getpid(),
    }

    r = redis.from_url(
        settings.redis_url,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry_on_timeout=False,
    )
    try:
        r.setex(f"worker:health:{worker_id}", 30, json.dumps(health_data))
        return True
    except (redis.exceptions.TimeoutError, redis.exceptions.ConnectionError) as e:
        logger.error("failed_to_write_sync_health_timeout", error=str(e))
        return False
    except Exception as e:
        logger.error("failed_to_write_sync_health", error=str(e))
        return False
    finally:
        r.close()


async def write_health_async() -> bool:
    """Write worker health (async version for use in worker loop)."""
    from redis.exceptions import ConnectionError as SyncConnectionError
    from redis.exceptions import TimeoutError as SyncTimeoutError

    worker_id = get_worker_id()
    health_data = {
        "worker_id": worker_id,
        "status": "healthy",
        "timestamp": datetime.now(UTC).isoformat(),
        "pid": os.getpid(),
    }

    client = get_redis_client()
    try:
        await client.setex(f"worker:health:{worker_id}", 30, json.dumps(health_data))
        return True
    except (TimeoutError, SyncTimeoutError, SyncConnectionError) as e:
        logger.error("failed_to_write_async_health_timeout", error=str(e))
        return False
    except Exception as e:
        logger.error("failed_to_write_async_health", error=str(e))
        return False


async def _check_redis() -> bool:
    """Return whether Redis is reachable from the worker health app."""
    try:
        client = get_redis_client()
        return bool(await client.ping())
    except Exception as e:
        logger.warning("worker_health_redis_check_failed", error=str(e))
        return False


async def _check_database() -> bool:
    """Return whether the database is reachable from the worker health app."""
    try:
        session_factory = get_async_session_factory()
        async with session_factory() as db:
            await db.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.warning("worker_health_database_check_failed", error=str(e))
        return False


async def _run_on_worker_loop(coro_factory: Callable[[], Awaitable[T]]) -> T:
    """Run async health checks on the worker loop when the HTTP server is threaded."""
    loop = _worker_loop
    current_loop = asyncio.get_running_loop()
    if loop is None or loop is current_loop or loop.is_closed():
        return await coro_factory()

    future = asyncio.run_coroutine_threadsafe(coro_factory(), loop)
    return await asyncio.wrap_future(future)


@health_app.get("/health")
async def health() -> JSONResponse:
    """Return dependency readiness for worker health checks."""
    redis_ok, database_ok = await asyncio.gather(
        _run_on_worker_loop(_check_redis),
        _run_on_worker_loop(_check_database),
    )
    is_ok = redis_ok and database_ok
    return JSONResponse(
        status_code=200 if is_ok else 503,
        content={
            "status": "ok" if is_ok else "degraded",
            "checks": {
                "redis": redis_ok,
                "database": database_ok,
            },
        },
    )


@health_app.get("/metrics")
async def metrics() -> Response:
    """Return Prometheus-formatted worker metrics."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def start_health_server(port: int | None = None) -> uvicorn.Server | None:
    """Start the health check FastAPI server in a background thread.

    Port is read from WORKER_HEALTH_PORT env var (default: 8082).
    Set WORKER_HEALTH_PORT=0 to disable.

    Returns the uvicorn server instance for truthiness/lifecycle compatibility.
    """
    global _health_server, _health_server_thread, _worker_loop
    if _health_server is not None:
        return _health_server

    env_port = os.environ.get("WORKER_HEALTH_PORT", "8082")
    if port is None:
        port = int(env_port)

    if port == 0:
        logger.info("worker_health_http_disabled")
        return None

    try:
        _worker_loop = asyncio.get_running_loop()
    except RuntimeError:
        _worker_loop = None

    config = uvicorn.Config(
        health_app,
        host="0.0.0.0",
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
        access_log=False,
    )
    _health_server = uvicorn.Server(config)
    _health_server_thread = threading.Thread(target=_health_server.run, daemon=True)
    _health_server_thread.start()
    logger.info("worker_health_server_started", port=port)
    return _health_server


def stop_health_server():
    """Stop the health check HTTP server."""
    global _health_server, _health_server_thread, _worker_loop
    if _health_server:
        _health_server.should_exit = True
        if _health_server_thread is not None:
            _health_server_thread.join(timeout=5)
            if _health_server_thread.is_alive():
                _health_server.force_exit = True
                _health_server_thread.join(timeout=1)
        _health_server = None
        _health_server_thread = None
        _worker_loop = None


if __name__ == "__main__":
    import sys

    success = write_health_sync()
    sys.exit(0 if success else 1)
