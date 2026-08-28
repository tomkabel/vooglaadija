## Why

`_extract_via_subprocess` builds a large Python script via f-string and runs
`python -c` for every job, importing `yt_dlp` (and initializing its extractors)
fresh on each call. That import + init cost is hundreds of milliseconds and
dominates short downloads (e.g. small TikTok clips). At high job volume this
fixed per-call overhead is significant.

## What Changes

- Introduce a persistent **yt-dlp driver subprocess pool**: one long-lived
  driver process per pool slot (`YT_DLP_POOL_SIZE`, defaulting to
  `YT_DLP_EXTRACTION_CONCURRENCY`).
- Each driver imports `yt_dlp` **once** at process start and then serves
  extraction requests over stdin/stdout using newline-delimited JSON.
- The per-job script is replaced by a small request payload (URL, output
  template, platform, format spec/format_sort, extractor args, cookies,
  output fields) plus the existing progress-hook / result JSON line protocol.
- Slots are reused across jobs; on a per-job timeout, or if the driver dies or
  fails its handshake, the offending slot is killed (same process-group
  SIGTERM -> SIGKILL + orphan-walk semantics) and respawned lazily on the next
  checkout so the pool stays at full strength.
- Application shutdown terminates the pool (wired into the existing lifespan
  `close_api_resources`).

## Capabilities

### New Capabilities
- `yt-dlp-persistent-worker-pool`: A `YtDlpProcessPool` of `N` fixed slots,
  each backed by a persistent driver subprocess (launched as
  `python app/services/yt_dlp_worker_driver.py`) that imports `yt_dlp` once
  and serves requests over stdin/stdout JSON lines. `ensure_started` spawns
  all slots concurrently; `_checkout`/`_release` hand out and return ready
  slots, respawning dead ones lazily.
- `yt-dlp-worker-request-protocol`: Per-job work is described by a JSON
  request (`job_id`, `mode`, `url`, `output_template`, `platform`, `format`,
  `format_sort`, `extractor_args`, `cookies_opts`, `youtube_opts`) and
  answered with progress lines (`{"job_id": ..., "progress": true, ...}`) and
  a single terminal line (`{"job_id": ..., "result": {...}}` or
  `{"job_id": ..., "error": "..."}`). Each driver's stderr is drained
  continuously by a background task into a per-slot buffer, which
  `run_job` checks for throttle/error signals after each job — there is no
  per-job `_stderr` response field.

### Modified Capabilities
- `yt-dlp-extraction-kill-semantics`: The timeout-kill sequence (process-group
  SIGTERM -> wait -> /proc orphan walk -> SIGKILL) is preserved against the
  persistent driver pool via `_kill_busy_slot` (in-job timeout/failure) and
  `shutdown` (pool teardown); a killed slot is respawned on its next checkout.

## Impact

- **`app/services/yt_dlp_worker_driver.py`** (new): the persistent driver
  script.
- **`app/services/yt_dlp_service.py`**: `_extract_via_subprocess` now builds a
  request and dispatches it to the singleton `YtDlpProcessPool` via
  `run_job`; adds `YtDlpProcessPool`, `_get_pool`, `shutdown_yt_dlp_pool`.
- **`app/api/startup.py`**: `close_api_resources` now shuts down the pool.
- **No database changes, no new dependencies.** Behavior, output paths, titles,
  and progress-callback contract are unchanged.
