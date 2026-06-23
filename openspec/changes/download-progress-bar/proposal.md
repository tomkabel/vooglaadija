## Why

The download dashboard currently shows only a static "Processing" badge while yt-dlp is running — users have no visibility into how much of a video has been downloaded, remaining time, or transfer speed. This creates uncertainty about whether a job is making progress or stuck. Adding a real-time progress bar with percentage, speed, and ETA eliminates this blindspot and brings the UI to parity with common download managers.

## What Changes

- yt-dlp subprocess emits progress JSON lines to stdout during download (via `progress_hooks`)
- Worker streams stdout line-by-line and relays progress updates through the existing Redis pub/sub infrastructure
- A new `job_progress:{user_id}` pub/sub channel carries high-frequency progress events, separate from status transitions
- SSE endpoint subscribes to the progress channel and emits `progress_update` events to the browser
- Dashboard rows for `processing` jobs show a progress bar with percentage, download speed, and ETA
- Progress bar appears automatically when progress data arrives and is replaced by the download button on completion
- No database schema changes — progress is ephemeral and delivered entirely via pub/sub + SSE

## Capabilities

### New Capabilities
- `yt-dlp-progress-hooks`: Embed a `progress_hooks` callback in the yt-dlp subprocess script that prints structured JSON to stdout, throttled to ~every 0.5% change
- `stdout-stream-progress`: Change `_extract_via_subprocess` from `communicate()` to async line-by-line stdout parsing, identifying progress lines by `{"progress": true}` marker
- `progress-pubsub-channel`: Add `publish_job_progress()` to PubSubService using a new `job_progress:{user_id}` channel, keeping progress events separate from status updates
- `sse-progress-events`: Subscribe SSE endpoint to the progress channel, emit `progress_update` SSE events with job_id and progress payload
- `frontend-progress-bar`: Extend the HTMX SSE handler in dashboard.html to render an animated progress bar on processing rows, updating in real-time with speed and ETA display
- `circuit-breaker-progress-callback`: Thread an optional `progress_callback` parameter through `extract_media_with_circuit_breaker` → `_extract_media_url_internal` → `extract_media_url`

### Modified Capabilities

None — this is a purely additive change with no spec-level behavior changes to existing capabilities.

## Impact

- **`app/services/yt_dlp_service.py`**: Subprocess script gains `progress_hooks`; `_extract_via_subprocess` changes from `communicate()` to streaming read
- **`app/services/circuit_breaker.py`**: `extract_media_with_circuit_breaker()` adds optional `progress_callback` parameter
- **`app/services/pubsub_service.py`**: New `publish_job_progress()` method, new channel pattern `job_progress:{user_id}`
- **`worker/processor.py`**: Creates a progress callback closure that publishes to Redis pub/sub before calling extraction
- **`app/api/routes/sse.py`**: Dual-channel subscription — subscribes to both `job_status` and `job_progress` channels
- **`app/templates/dashboard.html`**: New SSE handler for `progress_update` events; `getRowHTML()` includes progress bar container; `updateDownloadRow()` animates bar
- **`app/static/css/styles.css`**: Progress bar CSS with amber/jade color tokens, smooth transitions
- **No database changes** — no migration required
- **No new dependencies** — reuses existing Redis, pub/sub, SSE, and yt-dlp `progress_hooks`
