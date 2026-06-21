"""Retry decision and scheduling behavior for worker download failures."""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.error_classifier import (
    CATEGORY_POLICIES,
    ErrorCategory,
    calculate_delay,
    classify_error,
    extract_retry_after,
    format_attempt_error,
    is_non_retryable,
)
from core.logging_config import get_logger
from core.metrics import ERROR_CLASSIFICATION, RETRIES_TOTAL
from core.models.download_job import DownloadJob
from core.models.outbox import Outbox
from core.queue import push_to_retry_queue
from worker.job_executor import publish_job_status

logger = get_logger(__name__)


@dataclass(slots=True)
class RetryDecision:
    """Typed retry/final-failure decision returned by retry evaluation."""

    is_final: bool
    delay_seconds: float | None
    category: ErrorCategory
    effective_max_retries: int
    final_error: str | None
    retry_after: int | None
    retry_count: int
    next_retry_at: datetime | None = None
    formatted_error: str | None = None
    accumulated_error: str | None = None
    signal: str | None = None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def evaluate(job: DownloadJob, error: BaseException) -> RetryDecision:
    """Classify an error and decide whether the job should retry or fail finally."""
    error_str = str(error)
    classification = classify_error(error_str)
    category = classification.category
    job_max_retries = job.max_retries if job.max_retries else 3
    effective_max = min(CATEGORY_POLICIES[category].max_retries, job_max_retries)

    ERROR_CLASSIFICATION.labels(category=category.value).inc()

    if is_non_retryable(category) or job.retry_count >= effective_max:
        if is_non_retryable(category):
            final_error = f"Non-retryable error ({category.value}): {error_str}"
        else:
            final_error = (
                f"Max retries ({effective_max}) exceeded for "
                f"'{category.value}' category: {error_str}"
            )

        previous_errors = job.error or ""
        if previous_errors:
            accumulated = f"{previous_errors} \u2192 {final_error}"
        else:
            accumulated = final_error

        return RetryDecision(
            is_final=True,
            delay_seconds=None,
            category=category,
            effective_max_retries=effective_max,
            final_error=final_error,
            retry_after=None,
            retry_count=job.retry_count,
            accumulated_error=accumulated,
            signal=classification.signal,
        )

    prev_delay = None
    if job.next_retry_at:
        prev_delay = (_as_utc(job.next_retry_at) - datetime.now(UTC)).total_seconds()
        if prev_delay < 0:
            prev_delay = None

    retry_after = extract_retry_after(error_str)
    delay_seconds = calculate_delay(
        category=category,
        attempt=job.retry_count,
        prev_delay=prev_delay,
        retry_after=retry_after,
    )
    next_retry = datetime.now(UTC) + timedelta(seconds=delay_seconds)

    formatted_error = format_attempt_error(
        attempt=job.retry_count + 1,
        max_retries=effective_max,
        error_str=error_str,
        category=category,
    )

    previous = job.error or ""
    if previous and job.retry_count > 0:
        accumulated_error = f"{previous} \u2192 {formatted_error}"
    else:
        accumulated_error = formatted_error

    return RetryDecision(
        is_final=False,
        delay_seconds=delay_seconds,
        category=category,
        effective_max_retries=effective_max,
        final_error=None,
        retry_after=retry_after,
        retry_count=job.retry_count,
        next_retry_at=next_retry,
        formatted_error=formatted_error,
        accumulated_error=accumulated_error,
        signal=classification.signal,
    )


async def schedule_retry(db: AsyncSession, job: DownloadJob, decision: RetryDecision) -> bool:
    """Persist retry scheduling, then enqueue Redis and clear synced outbox rows."""
    active_job_id = job.id
    if decision.next_retry_at is None:
        delay = decision.delay_seconds or 0.0
        decision.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)

    retry_result = await db.execute(
        update(DownloadJob)
        .where(
            DownloadJob.id == active_job_id,
            DownloadJob.status == "processing",
        )
        .values(
            status="pending",
            retry_count=job.retry_count + 1,
            next_retry_at=decision.next_retry_at,
            error=decision.accumulated_error,
            error_category=decision.category.value,
            updated_at=datetime.now(UTC),
        )
    )
    if int(getattr(retry_result, "rowcount", 0) or 0) == 0:
        await db.rollback()
        logger.warning(
            "job_retry_update_skipped_not_processing",
            job_id=str(active_job_id),
        )
        return False

    outbox_entry = Outbox(
        id=uuid.uuid4(),
        job_id=active_job_id,
        event_type="retry_scheduled",
        payload=json.dumps(
            {
                "retry_count": job.retry_count + 1,
                "category": decision.category.value,
                "next_retry_at": decision.next_retry_at.isoformat(),
            }
        ),
        status="pending",
    )
    db.add(outbox_entry)
    await db.commit()

    enqueued = False
    try:
        enqueued = await push_to_retry_queue(active_job_id, decision.next_retry_at.timestamp())
        if enqueued:
            await db.execute(delete(Outbox).where(Outbox.id == outbox_entry.id))
            await db.commit()
            RETRIES_TOTAL.labels(category=decision.category.value).inc()
        else:
            logger.error("job_failed_to_enqueue_for_retry", job_id=str(active_job_id))
    except Exception as enqueue_error:
        logger.error(
            "job_failed_to_enqueue_for_retry",
            job_id=str(active_job_id),
            error=str(enqueue_error),
        )

    logger.info(
        "job_scheduled_for_retry",
        job_id=str(active_job_id),
        category=decision.category.value,
        retry_count=job.retry_count + 1,
        effective_max=decision.effective_max_retries,
        next_retry_at=decision.next_retry_at.isoformat(),
        delay_seconds=round(decision.delay_seconds or 0.0, 1),
    )

    select_result = await db.execute(select(DownloadJob).where(DownloadJob.id == active_job_id))
    retried_job = select_result.scalar_one_or_none()
    if retried_job:
        await publish_job_status(retried_job)

    return enqueued
