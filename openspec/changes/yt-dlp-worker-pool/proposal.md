## Why

`_extract_via_subprocess` builds a large Python script via f-string and runs
`python -c` for every job, importing `yt_dlp` (and initializing its extractors)
fresh on each call. That import + init cost is hundreds of milliseconds and
dominates short downloads (e.g. small TikTok clips). At high job volume this
fixed per-call overhead is significant.

## What Changes

- Introduce a persistent **yt-dlp worker subprocess pool**: one long-lived
  worker process per extraction concurrency slot (`YT_DLP_EXTRACTION_CONCURRENCY`).
- Each worker imports `yt_dlp` **once** at first use and then serves extraction
  requests over stdin/stdout using newline-delimited JSON.
- The per-job script is replaced by a small request payload (URL, output
  template, platform, format fallback chain, extractor args, cookies, output
  fields) plus the existing progress-hook / result JSON line protocol.
- Workers are reused across jobs; on a per-job timeout the offending worker is
  killed (same process-group SIGTERM -> SIGKILL + orphan-walk semantics) and
  immediately respawned so the pool stays at full strength.
- Application shutdown terminates the pool (wired into the existing lifespan
  `close_api_resources`).

## Capabilities

### New Capabilities
- `yt-dlp-persistent-worker-pool`: A `YtDlpProcessPool` of `N` persistent
  worker subprocesses (launched as `python app/services/yt_dlp_worker.py`),
  each importing `yt_dlp` once and serving requests over stdin/stdout JSON lines.
- `yt-dlp-worker-request-protocol`: Per-job work is described by a JSON request
  (`url`, `output_template`, `platform`, `fallback_chain`, `extractor_args`,
  `cookies_opts`, `output_fields`, `youtube_opts`) and answered with progress
  lines (`{"progress": true, ...}`) and a single terminal line (result dict or
  `{"error": ...}`), with per-job stderr returned in a `_stderr` field for
  throttle/error detection.

### Modified Capabilities
- `yt-dlp-extraction-kill-semantics`: The timeout-kill sequence (process-group
  SIGTERM -> wait -> /proc orphan walk -> SIGKILL) is preserved against the
  persistent worker pool via `_terminate_worker_on_timeout`, and the pool
  respawns the slot afterwards.

## Impact

- **`app/services/yt_dlp_worker.py`** (new): the persistent worker script.
- **`app/services/yt_dlp_service.py`**: `_extract_via_subprocess` now builds a
  request via `_build_extract_request` and dispatches it to the singleton
  `YtDlpProcessPool`; adds `YtDlpProcessPool`, `_terminate_worker_on_timeout`,
  `shutdown_yt_dlp_pool`, `reset_yt_dlp_pool`.
- **`app/api/startup.py`**: `close_api_resources` now shuts down the pool.
- **No database changes, no new dependencies.** Behavior, output paths, titles,
  and progress-callback contract are unchanged.
