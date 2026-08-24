## 1. Persistent Worker Script

- [x] 1.1 Create `app/services/yt_dlp_worker.py` that imports `yt_dlp` once (lazily on first request) and serves requests over stdin/stdout JSON lines
- [x] 1.2 Implement the request protocol: read a JSON request line, run the format fallback loop with progress hooks, emit `{"progress": true, ...}` lines and one terminal line (result dict or `{"error": ...}`), including per-job stderr in a `_stderr` field
- [x] 1.3 Replicate the existing fallback/error logic: `prefer_free_formats` + `check_formats` for YouTube, extractor args for YouTube, cookies handling, "Requested format ... not available" -> next format, all-formats-failed message

## 2. Worker Pool in yt_dlp_service

- [x] 2.1 Add `YtDlpProcessPool` with `_ensure_started` (spawn `N` workers), `_spawn_worker` (launch `python yt_dlp_worker.py` with PIPE stdin/stdout, DEVNULL stderr, `start_new_session=True`), and a free-worker `asyncio.Queue`
- [x] 2.2 Add `_run_on_worker` to write the JSON request, stream stdout until the terminal line, forward progress to `progress_callback`, and raise `RuntimeError`/`TimeoutError` appropriately
- [x] 2.3 Add `_terminate_worker_on_timeout` replicating SIGTERM -> wait -> orphan walk -> SIGKILL, and keep `_kill_process_group` for shutdown
- [x] 2.4 Add `_build_extract_request` encapsulating platform-specific format chain, extractor args, cookies, and output fields
- [x] 2.5 Replace `_extract_via_subprocess` body to build a request and `submit` it to the singleton pool; preserve SSRF check, throttle detection, and return shape
- [x] 2.6 Add `shutdown_yt_dlp_pool` / `reset_yt_dlp_pool` and wire `shutdown_yt_dlp_pool` into `close_api_resources`

## 3. Tests

- [x] 3.1 Add tests for `_build_extract_request` (YouTube full chain + opts; TikTok/Instagram exclude YouTube-only opts)
- [x] 3.2 Add tests for worker `make_ydl_opts` (YouTube-only opts present, non-YouTube excluded, cookiesfrombrowser list -> tuple)
- [x] 3.3 Add `YtDlpProcessPool._run_on_worker` tests: success strips `_stderr`, error line raises `RuntimeError`, progress forwarded, worker death raises, timeout kills worker and raises `TimeoutError`
- [x] 3.4 Add `_terminate_worker_on_timeout` tests: SIGTERM before SIGKILL, `ProcessLookupError` silenced
- [x] 3.5 Update spawn-shape assertion to verify the persistent worker is launched with `start_new_session=True`, PIPE stdout, DEVNULL stderr, and the worker script path

## 4. Lifecycle / Shutdown

- [x] 4.1 Ensure application shutdown terminates all pooled workers without error
