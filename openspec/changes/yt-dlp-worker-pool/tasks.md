## 1. Persistent Driver Script

- [x] 1.1 Create `app/services/yt_dlp_worker_driver.py` that imports `yt_dlp` once at process start and serves requests over stdin/stdout JSON lines
- [x] 1.2 Implement the request protocol: read a JSON request line, run extraction with a progress hook, emit `{"job_id": ..., "progress": true, ...}` lines and one terminal line (`{"job_id": ..., "result": {...}}` or `{"job_id": ..., "error": "..."}`); print `{"_worker_ready": true}` on startup
- [x] 1.3 Replicate the existing fallback/error logic: `prefer_free_formats` + `check_formats` for YouTube, extractor args for YouTube, cookies handling (including `cookiesfrombrowser` list -> tuple), single-pass `format`/`format_sort` extraction

## 2. Driver Pool in yt_dlp_service

- [x] 2.1 Add `YtDlpProcessPool` with a fixed list of slots (`proc`/`ready`/`busy`/`starting`/`stderr_lines`/`stderr_task`) and `ensure_started` to spawn all slots concurrently via `asyncio.gather`
- [x] 2.2 Add `_start_slot` to launch `python yt_dlp_worker_driver.py` (PIPE stdin/stdout/stderr, `start_new_session=True`), wait for the `{"_worker_ready": true}` handshake, and start a background `_drain_stderr` task per slot
- [x] 2.3 Add `run_job` to write the JSON request, stream stdout until the matching `job_id`'s terminal line, forward progress to `progress_callback`, and raise `RuntimeError`/`TimeoutError` appropriately
- [x] 2.4 Add `_kill_busy_slot` replicating SIGTERM -> wait -> orphan walk -> SIGKILL for in-job timeouts/failures, and `shutdown` (same kill sequence) for pool teardown; `_checkout`/`_release` handle lazy respawn of dead slots
- [x] 2.5 Replace `_extract_via_subprocess` body to build a request dict (platform-specific format chain, extractor args, cookies, output fields) and dispatch it to the singleton pool via `run_job`; preserve SSRF check, throttle detection, and return shape
- [x] 2.6 Add `_get_pool` / `shutdown_yt_dlp_pool` and wire `shutdown_yt_dlp_pool` into `close_api_resources`

## 3. Tests

- [x] 3.1 Add tests for the request dict built by `_extract_via_subprocess` (YouTube full chain + opts; TikTok/Instagram exclude YouTube-only opts)
- [x] 3.2 Add tests for the driver's `make_ydl_opts` (YouTube-only opts present, non-YouTube excluded, cookiesfrombrowser list -> tuple)
- [x] 3.3 Add `YtDlpProcessPool.run_job` tests: success returns result, error line raises `RuntimeError`, progress forwarded, driver death raises, timeout kills the slot and raises `TimeoutError`
- [x] 3.4 Add `_kill_busy_slot` / `shutdown` tests: SIGTERM before SIGKILL, `ProcessLookupError` silenced
- [x] 3.5 Add a spawn-shape assertion verifying the persistent driver is launched with `start_new_session=True`, PIPE stdin/stdout/stderr, and the driver script path
- [x] 3.6 Add `test_ensure_started_does_not_spawn_after_shutdown` / `test_pool_does_not_spawn_after_shutdown` regressions guarding the shutdown race on both entry points (`ensure_started` and `_checkout`)

## 4. Lifecycle / Shutdown

- [x] 4.1 Ensure application shutdown terminates all pooled drivers without error
