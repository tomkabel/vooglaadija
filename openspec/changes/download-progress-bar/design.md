## Context

The download system uses yt-dlp as a subprocess. Currently the worker calls `extract_media_with_circuit_breaker()` which blocks until yt-dlp finishes, then publishes a single status transition. There is no intermediate progress feedback.

The existing SSE infrastructure streams events to the browser via Redis pub/sub. The frontend uses HTMX SSE with client-side row management. Adding a progress bar requires:

1. Making yt-dlp emit progress data during download
2. Streaming that data back through the worker to Redis
3. Delivering it to the browser via SSE
4. Rendering it as an animated progress bar

The design reuses every existing component (Redis, pub/sub, SSE, HTMX) — no new infrastructure.

## Goals / Non-Goals

**Goals:**
- Real-time progress bar (% , speed, ETA) for each processing download row
- Separate progress channel to avoid interfering with status deduplication
- Backward-compatible — existing status flow unchanged
- No database writes for progress data
- Handle edge cases: unknown total size, subprocess crash, page refresh

**Non-Goals:**
- Persisting progress to PostgreSQL (ephemeral by design)
- Progress for pre-processing phases (format detection, extraction — yt-dlp only reports during download)
- Granular progress during retry scheduling or queue wait
- WebSocket or polling alternatives to SSE

## Decisions

### Decision 1: stdout streaming vs. separate Redis connection from subprocess

**Chosen: Stdout streaming with JSON lines**

- The subprocess already has stdout connected via `asyncio.subprocess.PIPE`
- Progress lines are identified by `{"progress": true}` marker — the final extraction result is the only line without this marker
- Zero additional dependencies, auth, or connection management in the subprocess
- Reading line-by-line via `async for line in process.stdout` is native asyncio
- The `progress_callback` closure in the worker has full access to Redis and job context

### Decision 2: Separate pub/sub channel vs. reusing job_status channel

**Chosen: Separate `job_progress:{user_id}` channel**

- The SSE deduplication logic uses `job_id:updated_at` as the dedup key — progress events would be silently dropped on the status channel
- Progress fires at high frequency (~every 1-2 seconds) — separate channel prevents congestion on status updates
- The SSE endpoint subscribes to both channels and emits different event types: `job_update` (status) and `progress_update` (progress)
- Clean separation of concerns — status and progress are semantically different

### Decision 3: Throttle in subprocess vs. throttle in worker

**Chosen: Throttle in the subprocess (progress_hook)**

- The hook skips writing to stdout if `percent` hasn't changed by >= 0.5 since last emit
- Reduces IPC overhead (fewer lines through the pipe)
- Reduces Redis pub/sub publish calls
- Simpler worker code — no debouncing logic needed
- yt-dlp fires hooks on every chunk (often 1-2% jumps for video), so 0.5% threshold still gives smooth animation

### Decision 4: Progress callback threading through circuit breaker

**Chosen: Optional `progress_callback: Callable[[dict], Awaitable[None]] | None` threaded through the call chain**

- The circuit breaker doesn't interpret progress — it's purely a pass-through
- The callback type is async so the worker can directly call `pubsub.publish_job_progress()` without extra thread management
- Making it optional ensures zero behavior change for callers that don't need progress
- Backward-compatible — existing callers pass nothing

## Risks / Trade-offs

- **[Risk] yt-dlp subprocess crashes mid-progress**: The existing error handling in `process_next_job` catches exceptions and publishes status transitions. Progress stops naturally. The frontend sees no more `progress_update` events, and the next `job_update` (failed/retry) takes over.
- **[Risk] Unknown total_bytes (livestreams, HLS)**: If `total_bytes` is None and `total_bytes_estimate` is None, the subprocess skips progress output for that download. The frontend shows only the "Processing" status badge as before — graceful degradation.
- **[Risk] SSE disconnection during download**: On reconnect, the initial DB snapshot shows the job as `processing` but has no progress data. The progress bar re-appears once the next progress update arrives from the worker (typically within 1-2 seconds).
- **[Risk] Timing: progress arrives before row exists**: If pub/sub delivers a progress event to SSE before the initial DB snapshot is sent, the `progress_update` reaches the browser before the row DOM element exists. Mitigation: the `createDownloadRow(data)` function is called on both `job_update` and `progress_update` events if no row exists yet — using the available data to create a minimal row.
- **[Trade-off] No progress for non-download phases**: yt-dlp's `progress_hooks` only fire during the actual download phase. Format detection, extraction, and post-processing have no progress — these phases are typically short (a few seconds).
