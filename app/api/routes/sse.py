"""SSE (Server-Sent Events) routes for real-time job status and progress updates.

This module provides SSE endpoints that subscribe to Redis Pub/Sub for
real-time job status and progress updates, with fallback to polling if pub/sub fails.
"""

import asyncio
import contextlib
import json
import uuid
from collections import OrderedDict
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sse_starlette import EventSourceResponse, ServerSentEvent
from starlette.responses import JSONResponse

from app.api.dependencies import CurrentUserFromCookie
from app.api.rate_limit_config import limiter
from app.services.pubsub_service import PubSubService, get_pubsub_service
from core.database import get_async_session_factory
from core.logging_config import get_logger
from core.models.download_job import DownloadJob

router = APIRouter(prefix="/web", tags=["sse"])

# Per-user concurrent SSE stream cap. Every stream holds 2 Redis pub/sub
# subscriptions + a 10s DB poll loop; N tabs = N*2 subscriptions, so an
# unbounded count exhausts Redis connections and DB throughput.
_MAX_SSE_PER_USER = 5
_sse_connections: dict[uuid.UUID, int] = {}
_sse_connections_lock = asyncio.Lock()

logger = get_logger(__name__)

MAX_SEEN_JOBS = 100
POLL_INTERVAL_SECONDS = 10
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
    session_factory: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
    seen_initial: OrderedDict[str, str],
) -> list[ServerSentEvent]:
    """
    Create initial Server-Sent Events for the user's most recent download jobs.

    Parameters:
        session_factory: Factory for creating database sessions.
        user_id: Identifier of the user whose jobs are included.
        seen_initial: Mapping updated with each emitted job's identifier and state key.

    Returns:
        The initial job update events, or an empty list if the database query fails.
    """
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
                    ),
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
    pubsub: PubSubService,
    user_id: uuid.UUID,
    last_seen_job_ids: OrderedDict[str, str],
) -> AsyncGenerator[ServerSentEvent, None]:
    """
    Stream distinct job status updates for a user from Pub/Sub.

    Parameters:
        last_seen_job_ids (OrderedDict[str, str]): Cache of the latest emitted state for each job.

    Returns:
        AsyncGenerator[ServerSentEvent, None]: Job update events containing serialized job data.
    """
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
    pubsub: PubSubService,
    user_id: uuid.UUID,
) -> AsyncGenerator[ServerSentEvent, None]:
    """
    Stream progress updates for jobs belonging to a user.

    Parameters:
        user_id (uuid.UUID): Identifier of the user whose job progress updates are streamed.

    Yields:
        ServerSentEvent: An SSE event containing a job's progress data.
    """
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
    """
    Merge status and progress event streams into a single SSE stream.

    Status events are preserved under backpressure, while progress events may be dropped if the bounded queue remains full for five seconds.
    """
    queue: asyncio.Queue[ServerSentEvent | None] = asyncio.Queue(maxsize=128)

    async def _drain(
        source: AsyncGenerator[ServerSentEvent, None],
        src_name: str,
    ) -> None:
        """
        Drain events from a source into the shared queue, preserving status events and allowing progress events to be dropped under backpressure.

        Parameters:
                source (AsyncGenerator[ServerSentEvent, None]): Event source to consume.
                src_name (str): Name used to identify the event source.
        """
        try:
            async for event in source:
                # Status events (job_update) are never dropped — they
                # represent terminal/job-state transitions the client
                # needs. Progress events can be safely coalesced under
                # backpressure since each supersedes the previous.
                is_status = hasattr(event, "event") and event.event == "job_update"
                if is_status:
                    # Block on status events — don't drop
                    await queue.put(event)
                else:
                    # Progress events — safe to drop under backpressure
                    try:
                        await asyncio.wait_for(queue.put(event), timeout=5.0)
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

    try:
        completed = 0
        while completed < 2:
            event = await queue.get()
            if event is None:
                completed += 1
            else:
                yield event
    finally:
        # GeneratorExit (client disconnect) is raised at the `yield` inside
        # the loop, skipping any code after it — the drain tasks would leak
        # (2 asyncio tasks + 2 Redis pub/sub subscriptions per tab). Cancel
        # and join them here on every exit path.
        task1.cancel()
        task2.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task1
        with contextlib.suppress(asyncio.CancelledError):
            await task2


async def fallback_polling_generator(
    request: Request,
    session_factory: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
    seen_jobs: OrderedDict[str, str] | None = None,
) -> AsyncGenerator[ServerSentEvent, None]:
    """
    Polls the database for the user's latest download-job states and emits changes as SSE events.

    Parameters:
        request (Request): The current client request, used to detect disconnection and retain deduplication state.
        session_factory (async_sessionmaker[AsyncSession]): Factory for database sessions.
        user_id (uuid.UUID): Identifier of the user whose download jobs are monitored.
        seen_jobs (OrderedDict[str, str] | None): Optional cache of previously emitted job states.

    Yields:
        ServerSentEvent: A `job_update` event for each new or changed download-job state.
    """
    # Reuse existing dedup cache from request state if available
    if seen_jobs is None:
        existing_seen_jobs = getattr(request.state, "seen_jobs", None)
        if isinstance(existing_seen_jobs, OrderedDict):
            seen_jobs = existing_seen_jobs
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
    session_factory: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
) -> AsyncGenerator[ServerSentEvent, None]:
    """
    Stream download job updates through server-sent events.

    Publishes the initial database snapshot, replays events received during
    initialization, and then streams status and progress updates from Pub/Sub.
    Falls back to database polling when the Pub/Sub streams fail or complete.
    Stops processing when the client disconnects.

    Parameters:
        request (Request): The client request used to detect disconnection.
        session_factory (async_sessionmaker[AsyncSession]): Factory for database sessions.
        user_id (uuid.UUID): Identifier of the user whose job updates are streamed.

    Yields:
        ServerSentEvent: A job status or progress update for the user.
    """
    seen_initial: OrderedDict[str, str] = OrderedDict()
    buffered_events: list[dict] = []

    # Start pub/sub subscription first and buffer incoming messages
    pubsub = get_pubsub_service()
    reconnect_attempts = 0
    buffer_task: asyncio.Task | None = None

    async def _buffer_pubsub_events() -> None:
        """
        Buffers user-specific Pub/Sub job events for replay after the initial database snapshot.

        Events are retained up to the configured buffer capacity, with the oldest events evicted when the limit is reached. The operation stops when the client disconnects, the generator is closed or cancelled, or Pub/Sub reconnection attempts are exhausted.
        """
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
        async for event in fallback_polling_generator(
            request,
            session_factory,
            user_id,
            seen_initial,
        ):
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


@router.get("/downloads/stream", response_model=None)
# EventSource reconnects automatically on transient disconnects (deploys, flaky
# networks). A normal GET budget of 5/minute would exhaust the bucket during a
# short flap and leave the page without live updates, so the streaming endpoint
# gets a much larger burst budget.
@limiter.limit("60/minute")
async def download_status_stream(
    request: Request,
    current_user: CurrentUserFromCookie,
) -> EventSourceResponse | JSONResponse:
    """Provide a Server-Sent Events stream for the authenticated user's download updates.

    Each stream holds two Redis pub/sub subscriptions plus a 10s DB polling
    loop, so an unbounded number of tabs can exhaust Redis connections and DB
    throughput — cap concurrent streams per user.

    Returns:
        EventSourceResponse: The SSE response carrying download status and progress events.
    """
    async with _sse_connections_lock:
        active = _sse_connections.get(current_user.id, 0)
        if active >= _MAX_SSE_PER_USER:
            logger.warning(
                "sse_connection_limit_exceeded",
                user_id=str(current_user.id),
                active=active,
                max_connections=_MAX_SSE_PER_USER,
            )
            return JSONResponse(
                status_code=429,
                content={"detail": f"Too many open streams (max {_MAX_SSE_PER_USER})"},
            )
        _sse_connections[current_user.id] = active + 1

    async def _counted_generator() -> AsyncGenerator[ServerSentEvent, None]:
        try:
            async for event in event_generator(
                request, get_async_session_factory(), current_user.id
            ):
                yield event
        finally:
            async with _sse_connections_lock:
                remaining = _sse_connections.get(current_user.id, 1) - 1
                if remaining <= 0:
                    _sse_connections.pop(current_user.id, None)
                else:
                    _sse_connections[current_user.id] = remaining

    return EventSourceResponse(
        _counted_generator(),
        media_type="text/event-stream",
        ping=POLL_INTERVAL_SECONDS,
    )
