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

from celery import Celery

from core.config import settings
from core.logging_config import configure_logging, get_logger

configure_logging(log_level=os.environ.get("LOG_LEVEL", "INFO"))
logger = get_logger(__name__)


def start_worker() -> None:
    """Start the Celery worker with configuration from environment."""
    from worker.celery_app import celery_app

    queues = os.environ.get("CELERY_QUEUES", "downloads,retries,dlq")
    concurrency = int(os.environ.get("CELERY_CONCURRENCY", "1"))
    loglevel = os.environ.get("LOG_LEVEL", "INFO").lower()

    logger.info(
        "starting_celery_worker",
        queues=queues,
        concurrency=concurrency,
        loglevel=loglevel,
    )

    argv = [
        "worker",
        "--loglevel=" + loglevel,
        "--queues=" + queues,
        f"--concurrency={concurrency}",
        "--without-heartbeat",
        "--without-gossip",
        "--without-mingle",
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
        "/tmp/celerybeat-schedule",
    ]

    celery_app.start(argv)


def start_flower() -> None:
    """Start the Flower monitoring dashboard."""
    from worker.celery_app import celery_app

    port = int(os.environ.get("FLOWER_PORT", "5555"))
    logger.info("starting_flower", port=port)

    argv = [
        "flower",
        f"--port={port}",
        "--broker_api=" + settings.redis_url,
    ]

    celery_app.start(argv)


def enqueue_pending_jobs() -> int:
    """Enqueue all pending jobs in the database.

    Returns the number of jobs enqueued.
    """
    from worker.celery_tasks import enqueue_pending

    result = enqueue_pending.delay()
    return result.get(timeout=30)


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
