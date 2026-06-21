"""Worker health monitoring via Redis heartbeat and HTTP health endpoint."""

import json
import os
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.logging_config import get_logger
from app.services.redis_client import close_redis_client, get_redis_client, reset_redis_client
from core.config import settings

logger = get_logger(__name__)

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


class _HealthHandler(BaseHTTPRequestHandler):
    """HTTP request handler for health checks."""

    def do_GET(self):
        if self.path == "/metrics":
            data = generate_latest()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/health":
            uptime = (datetime.now(UTC) - _start_time).total_seconds()
            # Clone all worker state under lock to avoid race conditions
            with _state_lock:
                worker_status = _worker_state.get("status", "unknown")
                last_hb = _worker_state.get("last_heartbeat")
                current_job_started_at = _worker_state.get("current_job_started_at")
                health_data = {
                    **_worker_state,
                    "uptime_seconds": round(uptime),
                }

            # Determine health status based on worker state and heartbeat
            if worker_status == "running":
                # Worker is running - consider healthy if a job is actively processing
                if current_job_started_at:
                    # Job is running, check if job started recently
                    job_start_dt = datetime.fromisoformat(current_job_started_at)
                    seconds_since_job_start = (datetime.now(UTC) - job_start_dt).total_seconds()
                    # Only mark unhealthy if job exceeded 10 minutes AND no fresh heartbeat
                    if seconds_since_job_start > 600:
                        # Check if there's a recent heartbeat
                        has_fresh_heartbeat = False
                        if last_hb:
                            last_hb_dt = datetime.fromisoformat(last_hb)
                            seconds_since_hb = (datetime.now(UTC) - last_hb_dt).total_seconds()
                            if seconds_since_hb < 120:
                                has_fresh_heartbeat = True
                        if not has_fresh_heartbeat:
                            health_data["status"] = "unhealthy"
                            health_data["reason"] = "Job processing exceeded 10 minutes"
                elif last_hb:
                    # No job running but have heartbeat - check heartbeat freshness
                    last_hb_dt = datetime.fromisoformat(last_hb)
                    seconds_since_hb = (datetime.now(UTC) - last_hb_dt).total_seconds()
                    if seconds_since_hb > 120:
                        health_data["status"] = "unhealthy"
                        health_data["reason"] = "No heartbeat in over 120 seconds"
                # else: No job running and no heartbeat yet - this is okay for idle worker
            elif worker_status == "starting":
                health_data["status"] = "starting"
            else:
                health_data["status"] = "unhealthy"
                health_data["reason"] = f"Worker status is {worker_status}"

            status_code = 200 if health_data["status"] in ("running", "starting") else 503

            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(health_data).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):  # noqa: A002
        """Suppress default logging."""


def start_health_server(port: int | None = None) -> HTTPServer | None:
    """Start the health check HTTP server in a background thread.

    Port is read from WORKER_HEALTH_PORT env var (default: 8082).
    Set WORKER_HEALTH_PORT=0 to disable.

    Returns the server instance (call server.shutdown() to stop).
    """
    global _health_server
    if _health_server is not None:
        return _health_server

    env_port = os.environ.get("WORKER_HEALTH_PORT", "8082")
    if port is None:
        port = int(env_port)

    if port == 0:
        logger.info("worker_health_http_disabled")
        return None

    _health_server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(target=_health_server.serve_forever, daemon=True)
    thread.start()
    logger.info("worker_health_server_started", port=port)
    return _health_server


def stop_health_server():
    """Stop the health check HTTP server."""
    global _health_server
    if _health_server:
        _health_server.shutdown()
        try:
            _health_server.server_close()
        except Exception as e:
            logger.warning("error_closing_health_server_socket", error=str(e))
        _health_server = None


if __name__ == "__main__":
    import sys

    success = write_health_sync()
    sys.exit(0 if success else 1)
