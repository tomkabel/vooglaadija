"""Celery-based worker entry point for Vooglaadija.

Replaces the legacy hand-rolled BRPOP worker (worker/main.py) with
a Celery worker that provides:
  - Automatic retry with exponential backoff
  - Dead-letter queue for permanently failed jobs
  - Horizontal scaling (multiple workers without work duplication)
  - Health monitoring via Flower
  - Scheduled tasks (cleanup, zombie sweep) via Celery Beat

Usage:
  # Start worker (processes downloads queue)
  celery -A worker.celery_app worker --loglevel=info --queues=downloads,retries,dlq

  # Start Beat scheduler (cleanup, zombie sweep)
  celery -A worker.celery_app beat --loglevel=info

  # Start Flower dashboard
  celery -A worker.celery_app flower --port=5555
"""

import os
import sys
import threading
import time

from core.config import settings
from core.logging_config import configure_logging, get_logger

configure_logging(log_level=os.environ.get("LOG_LEVEL", "INFO"))
logger = get_logger(__name__)


def _start_health_heartbeat() -> None:
    """Keep the worker /health endpoint's liveness check fresh.

    ``worker.health._check_worker_loop()`` considers the loop alive only when
    ``last_heartbeat`` was updated within ``WORKER_HEALTH_STALE_AFTER_SECONDS``
    (default 30s). The Celery worker has no central main-loop hook to call
    ``update_worker_state()``, so a background thread publishes the heartbeat.
    """

    def _beat() -> None:
        from worker.health import update_worker_state

        while True:
            try:
                update_worker_state(status="running", current_job_started_at=None)
            except Exception:
                logger.warning("worker_health_heartbeat_failed", exc_info=True)
            time.sleep(10)

    threading.Thread(target=_beat, name="worker-health-heartbeat", daemon=True).start()


def start_worker() -> None:
    """Start the Celery worker with configuration from environment."""
    from worker.celery_app import celery_app
    from worker.health import start_health_server

    queues = os.environ.get("CELERY_QUEUES", "downloads,retries,dlq")
    concurrency = int(os.environ.get("CELERY_CONCURRENCY", "1"))
    loglevel = os.environ.get("LOG_LEVEL", "INFO").lower()

    # Keep the legacy health/metrics contract: serve /health and /metrics on
    # WORKER_HEALTH_PORT (default 8082) so the compose healthcheck, the
    # Dockerfile HEALTHCHECK and the Prometheus worker scrape target keep
    # working after the migration to Celery.
    start_health_server()
    _start_health_heartbeat()

    logger.info(
        "starting_celery_worker",
        queues=queues,
        concurrency=concurrency,
        loglevel=loglevel,
    )

    # Flower drives the worker over Celery's pidbox remote-control queue
    # (heartbeat, enable_events, inspect, ...). --without-heartbeat leaves
    # Consumer.event_dispatcher unset, so a heartbeat/enable_events control
    # command from Flower crashes with AttributeError: 'NoneType' object has
    # no attribute 'send'/'groups' instead of replying — keep heartbeat (and
    # gossip/mingle, which pidbox/events also depend on) enabled.
    argv = [
        "worker",
        "--loglevel=" + loglevel,
        "--queues=" + queues,
        f"--concurrency={concurrency}",
    ]

    celery_app.worker_main(argv)


def start_beat() -> None:
    """Start the Celery Beat scheduler for periodic tasks."""
    from worker.celery_app import celery_app

    logger.info("starting_celery_beat")

    argv = [
        "beat",
        "--loglevel=" + os.environ.get("LOG_LEVEL", "INFO").lower(),
        "--scheduler",
        "celery.beat.PersistentScheduler",
        "--schedule",
        "/var/lib/celerybeat/celerybeat-schedule",
    ]

    celery_app.start(argv)


def start_flower() -> None:
    """Start the Flower monitoring dashboard.

    Flower is started as a standalone command (not through celery_app.start)
    to avoid issues with the health heartbeat thread and Celery worker state.
    Flower connects directly to Redis as a broker API client.
    """
    import shutil
    import subprocess

    port = int(os.environ.get("FLOWER_PORT", "5555"))
    logger.info("starting_flower", port=port)

    celery_bin = shutil.which("celery")
    if celery_bin is None:
        raise RuntimeError("celery executable not found on PATH")

    cmd = [
        celery_bin,
        "-A",
        "worker.celery_app",
        "flower",
        f"--port={port}",
        f"--broker_api={settings.redis_url}",
    ]
    subprocess.run(cmd, check=False)  # noqa: S603 — fixed argv, no untrusted input


def enqueue_pending_jobs() -> int:
    """Enqueue all pending jobs in the database.

    Returns the number of jobs enqueued.
    """
    from worker.celery_tasks import enqueue_pending

    result = enqueue_pending.delay()
    return int(result.get(timeout=30))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m worker.celery_main [worker|beat|flower|enqueue]")
        sys.exit(1)

    command = sys.argv[1]

    if command == "worker":
        start_worker()
    elif command == "beat":
        start_beat()
    elif command == "flower":
        start_flower()
    elif command == "enqueue":
        count = enqueue_pending_jobs()
        print(f"Enqueued {count} pending jobs")
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
