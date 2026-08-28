## Context

`extract_media_url` -> `_extract_via_subprocess` runs yt-dlp as a `python -c`
subprocess per job. The subprocess re-imports `yt_dlp` (heavy) and re-initializes
its extractors on every call. The overhead is fixed per job and dominates short
downloads, and it scales linearly with job volume.

The fix is to keep `yt_dlp` resident in a small pool of worker processes and feed
jobs to them over stdin, reusing the import + init across many jobs.

## Goals / Non-Goals

**Goals:**
- Eliminate the per-job `yt_dlp` import/init cold start.
- Preserve the existing timeout/kill semantics (process-group SIGTERM -> wait ->
  /proc orphan walk -> SIGKILL) against the pool.
- Keep the public contract of `extract_media_url` (return tuple, progress
  callback, UUID-only file paths, SSRF checks, throttle detection) unchanged.
- Reuse existing stderr-based throttle detection without a per-job pipe.

**Non-Goals:**
- Changing format fallback chains or single-stream defaults (tracked separately).
- Pooling the metadata-only `resolve_video_title` path (out of scope for this
  issue; it remains a per-call subprocess).
- Cross-process load balancing beyond one worker per concurrency slot.

## Decisions

### Decision 1: Persistent driver pool vs. per-call subprocess
**Chosen: Persistent driver pool.** Each driver imports `yt_dlp` once and serves
many jobs over stdin/stdout JSON lines. This removes the dominant per-job cost.
The driver is launched as a dedicated script file (`yt_dlp_worker_driver.py`),
which keeps `yt_dlp_service.py` free of the large inline f-string and makes the
driver unit-testable in isolation. The pool holds a fixed list of slots
(dicts tracking `proc`/`ready`/`busy`/`starting`/`stderr_lines`/`stderr_task`)
rather than a worker-object-per-slot abstraction.

### Decision 2: Driver stdin/stdout JSON protocol vs. re-running `python -c` per job
**Chosen: newline-delimited JSON over stdin/stdout.** The parent writes one JSON
request line; the driver streams `{"job_id": ..., "progress": true, ...}` lines
and exactly one terminal line (`{"job_id": ..., "result": {...}}` or
`{"job_id": ..., "error": "..."}`). `run_job` reads until it sees the matching
`job_id`'s terminal line, ignoring stray/mismatched lines. On startup each
driver prints `{"_worker_ready": true}` so `_start_slot` can confirm the import
succeeded before the slot is handed real jobs. This preserves the existing
progress/result framing and needs no new network connections.

### Decision 3: stderr handling for persistent drivers
**Chosen: pipe the driver's `stderr` and drain it continuously with a
background task (`_drain_stderr`) into a per-slot ring buffer.** A persistent
driver's stderr cannot be read synchronously per-job without blocking or
framing complexity, so each slot's stderr is drained as it arrives; `run_job`
slices the buffer from a per-job watermark after the job completes and checks
it for throttle/error signals. There is no per-job `_stderr` field in the
response protocol.

### Decision 4: Timeout/kill against the pool
**Chosen: kill the timed-out slot's process group via `_kill_busy_slot`
(SIGTERM -> wait 3s -> /proc orphan walk -> SIGKILL -> wait 3s), mark the slot
dead, and let the next checkout respawn it via `_start_slot`.** This mirrors
the previous per-call timeout block exactly, so ffmpeg children that detached
from the group are still reaped, and the pool returns to full strength on
demand rather than eagerly.

### Decision 5: Concurrency limiting
**Chosen: pool size == `YT_DLP_POOL_SIZE` (default `YT_DLP_EXTRACTION_CONCURRENCY`)
with slot-based checkout/release.** `_checkout` reserves a free, ready slot (or
lazily spawns a pending one) and `_release` returns it, dropping it if its
driver died. The existing `_EXTRACTION_SEMAPHORE` in `extract_media_url` is
retained as a secondary guard; both derive from the same configured value so
there is no deadlock.
