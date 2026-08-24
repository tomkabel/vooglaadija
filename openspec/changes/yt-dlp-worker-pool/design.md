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

### Decision 1: Persistent worker pool vs. per-call subprocess
**Chosen: Persistent worker pool.** Each worker imports `yt_dlp` once and serves
many jobs over stdin/stdout JSON lines. This removes the dominant per-job cost.
The worker is launched as a dedicated script file (`yt_dlp_worker.py`), which
keeps `yt_dlp_service.py` free of the large inline f-string and makes the worker
unit-testable in isolation.

### Decision 2: Worker stdin/stdout JSON protocol vs. re-running `python -c` per job
**Chosen: newline-delimited JSON over stdin/stdout.** The parent writes one JSON
request line; the worker streams `{"progress": true, ...}` lines and exactly one
terminal line (result dict or `{"error": ...}`). The parent reads until the first
non-progress line. This preserves the existing progress/result framing and needs
no new network connections.

### Decision 3: stderr handling for persistent workers
**Chosen: redirect the worker's `stderr` to `DEVNULL` and return per-job stderr
in the terminal JSON line (`_stderr`).** A persistent worker's stderr cannot be
read per-job on a shared pipe without blocking or framing complexity. The worker
captures its own Python-level stderr into a buffer and includes the tail in the
response, so throttle/error detection still works.

### Decision 4: Timeout/kill against the pool
**Chosen: kill the timed-out worker's process group via `_terminate_worker_on_timeout`
(SIGTERM -> wait 3s -> /proc orphan walk -> SIGKILL -> wait 3s), then respawn the
slot.** This mirrors the previous per-call timeout block exactly, so ffmpeg
children that detached from the group are still reaped, and the pool returns to
full strength for subsequent jobs.

### Decision 5: Concurrency limiting
**Chosen: pool size == `YT_DLP_EXTRACTION_CONCURRENCY` with a free-worker queue.**
`submit` acquires a free worker, runs the job, and returns it (or respawns on
failure). The existing `_EXTRACTION_SEMAPHORE` in `extract_media_url` is retained
as a secondary guard; both derive from the same configured value so there is no
deadlock.
