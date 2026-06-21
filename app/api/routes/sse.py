"""SSE (Server-Sent Events) routes for real-time job status and progress updates.

This module provides SSE endpoints that subscribe to Redis Pub/Sub for
real-time job status and progress updates, with fallback to polling if pub/sub fails.
"""

import asyncio
import json
import uuid
from collections import OrderedDict
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from sqlalchemy import select
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.api.dependencies import CurrentUserFromCookie
from app.database import get_async_session_factory
from app.logging_config import get_logger
from app.models.download_job import DownloadJob
from app.services.pubsub_service import get_pubsub_service

router = APIRouter(prefix="/web", tags=["sse"])

logger = get_logger(__name__)

MAX_SEEN_JOBS = 100
POLL_INTERVAL_SECONDS = 15
MAX_PUBSUB_RECONNECT_ATTEMPTS = 3
PUBSUB_RECONNECT_DELAY_SECONDS = 1
# Cap on buffered pub/sub events during the initial subscription window.
# Prevents unbounded memory growth under high throughput (500+ job transitions
# in 2s can produce 500+ buffered entries per SSE connection).
_MAX_BUFFERED_EVENTS = 200


async def _job_to_sse_data(job: DownloadJob) -> dict:
    """Convert a DownloadJob model to SSE data dictionary.

    Includes _sort_key for consistent client-side ordering.
    """
    return {
        "id": str(job.id),
        "url": job.url,
        "title": job.title,
        "status": job.status,
        "file_name": job.file_name,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "_sort_key": job.created_at.timestamp() if job.created_at else 0,
    }


async def _emit_initial_snapshot(
    session_factory,
    user_id: uuid.UUID,
    seen_initial: OrderedDict[str, str],
) -> list[ServerSentEvent]:
    """Emit initial job state from database."""
    events = []
    try:
        async with session_factory() as db:
            query = (
                select(DownloadJob)
                .where(DownloadJob.user_id == user_id)
                .order_by(DownloadJob.created_at.desc())
                .limit(50)
            )
            result = await db.execute(query)
            jobs = result.scalars().all()

            for job in jobs:
                job_id_str = str(job.id)
                job_updated_at = job.updated_at.isoformat() if job.updated_at else None
                status_key = f"{job_id_str}:{job_updated_at}"
                seen_initial[job_id_str] = status_key
                events.append(
                    ServerSentEvent(
                        event="job_update",
                        data=json.dumps(await _job_to_sse_data(job)),
                    )
                )
    except Exception as e:
        logger.warning("sse_initial_state_failed", user_id=str(user_id), error=str(e))
    return events


async def _replay_buffered_events(
    buffered_events: list[dict],
    seen_initial: OrderedDict[str, str],
) -> AsyncGenerator[ServerSentEvent, None]:
    """Replay buffered events, skipping ones already in seen_initial."""
    for buffered in buffered_events:
        key = buffered["key"]
        job_data = buffered["data"]
        job_id = job_data.get("id")
        if job_id and key not in seen_initial.values():
            seen_initial[job_id] = key
            while len(seen_initial) > MAX_SEEN_JOBS:
                seen_initial.popitem(last=False)
            yield ServerSentEvent(
                event="job_update",
                data=json.dumps(job_data),
            )


async def _subscribe_to_pubsub(
    pubsub,
    user_id: uuid.UUID,
    last_seen_job_ids: OrderedDict[str, str],
) -> AsyncGenerator[ServerSentEvent, None]:
    """Inner generator that yields events from pubsub subscription."""
    async for job_data in pubsub.subscribe(user_id):
        job_id = job_data.get("id")

        if job_id:
            job_updated_at = job_data.get("updated_at")
            status_key = f"{job_id}:{job_updated_at}"
            if job_id not in last_seen_job_ids or last_seen_job_ids[job_id] != status_key:
                last_seen_job_ids[job_id] = status_key
                last_seen_job_ids.move_to_end(job_id)

                while len(last_seen_job_ids) > MAX_SEEN_JOBS:
                    last_seen_job_ids.popitem(last=False)

                yield ServerSentEvent(
                    event="job_update",
                    data=json.dumps(job_data),
                )


async def _subscribe_to_progress_pubsub(
    pubsub,
    user_id: uuid.UUID,
) -> AsyncGenerator[ServerSentEvent, None]:
    """Inner generator that yields progress events from pubsub subscription."""
    async for progress_data in pubsub.subscribe_progress(user_id):
        job_id = progress_data.get("id")
        if job_id and progress_data.get("progress"):
            yield ServerSentEvent(
                event="progress_update",
                data=json.dumps(progress_data),
            )


async def pubsub_event_generator(
    request: Request,
    user_id: uuid.UUID,
    last_seen_job_ids: OrderedDict[str, str] | None = None,
) -> AsyncGenerator[ServerSentEvent, None]:
    """SSE event generator that subscribes to Redis Pub/Sub for real-time updates."""
    pubsub = get_pubsub_service()
    reconnect_attempts = 0
    last_seen_job_ids = last_seen_job_ids if last_seen_job_ids is not None else OrderedDict()

    while reconnect_attempts < MAX_PUBSUB_RECONNECT_ATTEMPTS:
        if await request.is_disconnected():
            break

        try:
            async for event in _subscribe_to_pubsub(pubsub, user_id, last_seen_job_ids):
                yield event
            break  # Normal completion, no more events

        except (asyncio.CancelledError, GeneratorExit):
            break
        except Exception as e:
            reconnect_attempts += 1
            logger.warning(
                "pubsub_subscription_error",
                user_id=str(user_id),
                attempt=reconnect_attempts,
                error=str(e),
            )

            if reconnect_attempts < MAX_PUBSUB_RECONNECT_ATTEMPTS:
                await asyncio.sleep(PUBSUB_RECONNECT_DELAY_SECONDS * reconnect_attempts)
            else:
                logger.error("pubsub_max_reconnect_attempts", user_id=str(user_id))
                return  # Use return instead of break for generator


async def progress_event_generator(
    request: Request,
    user_id: uuid.UUID,
) -> AsyncGenerator[ServerSentEvent, None]:
    """SSE event generator that subscribes to Redis Pub/Sub for download progress updates."""
    pubsub = get_pubsub_service()
    reconnect_attempts = 0

    while reconnect_attempts < MAX_PUBSUB_RECONNECT_ATTEMPTS:
        if await request.is_disconnected():
            break

        try:
            async for event in _subscribe_to_progress_pubsub(pubsub, user_id):
                yield event
            break

        except (asyncio.CancelledError, GeneratorExit):
            break
        except Exception as e:
            reconnect_attempts += 1
            logger.warning(
                "pubsub_progress_subscription_error",
                user_id=str(user_id),
                attempt=reconnect_attempts,
                error=str(e),
            )

            if reconnect_attempts < MAX_PUBSUB_RECONNECT_ATTEMPTS:
                await asyncio.sleep(PUBSUB_RECONNECT_DELAY_SECONDS * reconnect_attempts)
            else:
                logger.error("pubsub_progress_max_reconnect_attempts", user_id=str(user_id))
                return


async def _merge_generators(
    gen1: AsyncGenerator[ServerSentEvent, None],
    gen2: AsyncGenerator[ServerSentEvent, None],
) -> AsyncGenerator[ServerSentEvent, None]:
    """Merge two SSE generators with backpressure via bounded queue + coalescing."""
    queue: asyncio.Queue[ServerSentEvent | None] = asyncio.Queue(maxsize=128)

    async def _drain(source, src_name):
        try:
            async for event in source:
                # Status events (job_update) are never dropped — they
                # represent terminal/job-state transitions the client
                # needs. Progress events can be safely coalesced under
                # backpressure since each supersedes the previous.
                is_status = (
                    hasattr(event, "event") and event.event == "job_update"
                )
                if is_status:
                    # Block on status events — don't drop
                    await queue.put(event)
                else:
                    # Progress events — safe to drop under backpressure
                    try:
                        await asyncio.wait_for(
                            queue.put(event), timeout=5.0
                        )
                    except (TimeoutError, asyncio.QueueFull):
                        logger.warning(
                            "sse_queue_full_dropping_progress",
                            src_name=src_name,
                        )
                        continue
        except (GeneratorExit, asyncio.CancelledError):
            pass
        except Exception as e:
            logger.warning("sse_merge_error", src_name=src_name, error=str(e))
        finally:
            await queue.put(None)  # sentinel

    task1 = asyncio.create_task(_drain(gen1, "status"))
    task2 = asyncio.create_task(_drain(gen2, "progress"))

    completed = 0
    while completed < 2:
        event = await queue.get()
        if event is None:
            completed += 1
        else:
            yield event

    task1.cancel()
    task2.cancel()


async def fallback_polling_generator(
    request: Request,
    session_factory,
    user_id: uuid.UUID,
    seen_jobs: OrderedDict[str, str] | None = None,
) -> AsyncGenerator[ServerSentEvent, None]:
    """Fallback polling generator when Pub/Sub is unavailable."""
    # Reuse existing dedup cache from request state if available
    if seen_jobs is None:
        if hasattr(request.state, "seen_jobs") and request.state.seen_jobs:
            seen_jobs = request.state.seen_jobs
        else:
            seen_jobs = OrderedDict()

    # Store back to request state for potential reuse
    request.state.seen_jobs = seen_jobs

    try:
        while True:
            if await request.is_disconnected():
                break

            async with session_factory() as db:
                query = (
                    select(DownloadJob)
                    .where(DownloadJob.user_id == user_id)
                    .order_by(DownloadJob.created_at.desc())
                    .limit(50)
                )
                result = await db.execute(query)
                jobs = result.scalars().all()

                for job in jobs:
                    job_id_str = str(job.id)
                    job_updated_at = job.updated_at.isoformat() if job.updated_at else None
                    status_key = f"{job_id_str}:{job_updated_at}"

                    if job_id_str not in seen_jobs or seen_jobs[job_id_str] != status_key:
                        seen_jobs[job_id_str] = status_key
                        seen_jobs.move_to_end(job_id_str)

                        # Trim to max size
                        while len(seen_jobs) > MAX_SEEN_JOBS:
                            seen_jobs.popitem(last=False)

                        yield ServerSentEvent(
                            event="job_update",
                            data=json.dumps(await _job_to_sse_data(job)),
                        )

            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    except (asyncio.CancelledError, GeneratorExit):
        pass


async def _disconnect_monitor(
    request: Request,
    task: asyncio.Task,
    timeout: float,
    check_interval: float = 0.25,
) -> None:
    """Wait for a task to complete, but cancel early if client disconnects."""
    elapsed = 0.0
    while elapsed < timeout:
        if await request.is_disconnected():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (asyncio.CancelledError, TimeoutError, Exception):
                pass
            return
        await asyncio.sleep(check_interval)
        elapsed += check_interval
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=0.1)
    except (TimeoutError, Exception):
        pass


async def event_generator(
    request: Request,
    session_factory,
    user_id: uuid.UUID,
) -> AsyncGenerator[ServerSentEvent, None]:
    """SSE event generator that prioritizes Pub/Sub with polling fallback."""
    seen_initial: OrderedDict[str, str] = OrderedDict()
    buffered_events: list[dict] = []

    # Start pub/sub subscription first and buffer incoming messages
    pubsub = get_pubsub_service()
    reconnect_attempts = 0
    buffer_task: asyncio.Task | None = None

    async def _buffer_pubsub_events():
        """Buffer pub/sub events before DB snapshot."""
        nonlocal reconnect_attempts
        while reconnect_attempts < MAX_PUBSUB_RECONNECT_ATTEMPTS:
            if await request.is_disconnected():
                break
            try:
                async for job_data in pubsub.subscribe(user_id):
                    if await request.is_disconnected():
                        break
                    job_id = job_data.get("id")
                    if job_id:
                        job_updated_at = job_data.get("updated_at")
                        status_key = f"{job_id}:{job_updated_at}"
                        # FIFO eviction at max capacity to prevent unbounded
                        # memory growth under high-throughput bursts
                        if len(buffered_events) >= _MAX_BUFFERED_EVENTS:
                            buffered_events.pop(0)
                        buffered_events.append({"key": status_key, "data": job_data})
                break
            except (asyncio.CancelledError, GeneratorExit):
                break
            except Exception as e:
                reconnect_attempts += 1
                logger.warning(
                    "pubsub_buffer_error",
                    user_id=str(user_id),
                    attempt=reconnect_attempts,
                    error=str(e),
                )
                if reconnect_attempts < MAX_PUBSUB_RECONNECT_ATTEMPTS:
                    await asyncio.sleep(PUBSUB_RECONNECT_DELAY_SECONDS * reconnect_attempts)
                else:
                    break

    # Run buffering task concurrently with DB query
    buffer_task = asyncio.create_task(_buffer_pubsub_events())
    try:
        # Send initial state from database
        initial_events = await _emit_initial_snapshot(session_factory, user_id, seen_initial)
        for event in initial_events:
            yield event

        # Wait for buffering to complete (with disconnect monitoring)
        await _disconnect_monitor(request, buffer_task, timeout=2.0)

        # Replay buffered events (skipping ones already in seen_initial)
        async for event in _replay_buffered_events(buffered_events, seen_initial):
            yield event

        # Continue with merged pub/sub streams
        try:
            async for event in _merge_generators(
                pubsub_event_generator(request, user_id, seen_initial),
                progress_event_generator(request, user_id),
            ):
                yield event
        except Exception as e:
            logger.warning(
                "sse_pubsub_generator_failed",
                user_id=str(user_id),
                error=str(e),
                fallback_to_polling=True,
            )

        # Fall back to polling
        async for event in fallback_polling_generator(request, session_factory, user_id, seen_initial):
            yield event
    except GeneratorExit:
        # Client disconnected — clean up buffer task immediately
        raise
    except Exception as e:
        logger.warning("sse_generator_failed", user_id=str(user_id), error=str(e))
    finally:
        if buffer_task is not None and not buffer_task.done():
            buffer_task.cancel()
            try:
                await asyncio.wait_for(buffer_task, timeout=1.0)
            except (asyncio.CancelledError, TimeoutError):
                pass


@router.get("/downloads/stream")
async def download_status_stream(
    request: Request,
    current_user: CurrentUserFromCookie,
):
    """Server-Sent Events endpoint for real-time download status and progress updates."""
    return EventSourceResponse(
        event_generator(request, get_async_session_factory(), current_user.id),
        media_type="text/event-stream",
        ping=POLL_INTERVAL_SECONDS,
    )
