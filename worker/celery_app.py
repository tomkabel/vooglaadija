"""Celery application configuration for Vooglaadija.

Replaces the hand-rolled BRPOP worker with Celery + Redis for durable,
observable, horizontally-scalable job processing.

Architecture:
- Redis serves as both broker and result backend
- Separate queues for downloads, retries, and dead-letter
- Automatic retry with exponential backoff via Celery's built-in mechanism
- Flower dashboard for monitoring
"""

import os

from celery import Celery
from celery.schedules import crontab
from kombu import Exchange, Queue

from core.config import settings

_celery_app: Celery | None = None


def get_celery_app() -> Celery:
    """Get or create the Celery application singleton."""
    global _celery_app
    if _celery_app is not None:
        return _celery_app

    broker_url = settings.redis_url

    _celery_app = Celery(
        "vooglaadija",
        broker=broker_url,
        backend=broker_url,
        include=[
            "worker.celery_tasks",
        ],
    )

    _celery_app.conf.update(
        # Serialization
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,

        # Task execution
        task_track_started=True,
        task_time_limit=600,
        task_soft_time_limit=540,
        worker_prefetch_multiplier=1,
        worker_max_tasks_per_child=1000,

        # Result backend
        result_expires=3600,
        result_extended=True,

        # Retry configuration
        task_default_retry_delay=10,
        task_max_retries=3,
        task_acks_late=True,
        task_reject_on_worker_lost=True,

        # Queue configuration
        task_default_queue="downloads",
        task_queues=[
            Queue("downloads", Exchange("downloads"), routing_key="downloads"),
            Queue("retries", Exchange("retries"), routing_key="retries"),
            Queue("dlq", Exchange("dlq"), routing_key="dlq"),
        ],

        # Routing
        task_routes={
            "worker.celery_tasks.process_download": {"queue": "downloads"},
            "worker.celery_tasks.retry_download": {"queue": "retries"},
            "worker.celery_tasks.handle_failed_job": {"queue": "dlq"},
        },

        # Beat scheduler for periodic tasks
        beat_schedule={
            "cleanup-expired-jobs": {
                "task": "worker.celery_tasks.cleanup_expired_jobs",
                "schedule": crontab(minute="*/5"),
            },
            "cleanup-dlq": {
                "task": "worker.celery_tasks.cleanup_dlq",
                "schedule": crontab(minute="*/30"),
            },
            "requeue-stuck-jobs": {
                "task": "worker.celery_tasks.requeue_stuck_jobs",
                "schedule": crontab(minute="*/15"),
            },
            "enqueue-pending": {
                "task": "worker.celery_tasks.enqueue_pending",
                "schedule": crontab(minute="*/2"),
            },
        },
    )

    return _celery_app


celery_app = get_celery_app()
