## 1. Pub/Sub Progress Channel

- [x] 1.1 Add `publish_job_progress(user_id, job_data)` method to `PubSubService` that publishes to `job_progress:{user_id}` channel
- [x] 1.2 Verify progress channel is independent from status channel (separate channel pattern, no dedup collision)

## 2. yt-dlp Subprocess Progress Hooks

- [x] 2.1 Add a `progress_hooks` callback function inside the subprocess `extract_script` that writes JSON progress lines to stdout with `flush=True`
- [x] 2.2 Implement throttling in the hook: skip stdout write if `percent` hasn't changed by >= 0.5 since last emit
- [x] 2.3 Ensure the progress line contains `"progress": true`, `percent`, `downloaded_bytes`, `total_bytes`, `speed`, `eta`
- [x] 2.4 Ensure the final extraction result line (without `"progress"` key) is still the last stdout line

## 3. Stdout Streaming in _extract_via_subprocess

- [x] 3.1 Change `_extract_via_subprocess` from `process.communicate()` to async line-by-line reading of `process.stdout`
- [x] 3.2 For each line: attempt JSON parse — if `"progress": true` and `progress_callback` is set, await the callback; otherwise treat as final result
- [x] 3.3 Wrap the async iteration in `asyncio.wait_for` with `YT_DLP_TIMEOUT` for timeout handling
- [x] 3.4 Add `progress_callback` parameter to `_extract_via_subprocess`
- [x] 3.5 Add same `progress_callback` parameter to `extract_media_url()` and forward it

## 4. Circuit Breaker Pass-Through

- [x] 4.1 Add optional `progress_callback` parameter to `extract_media_with_circuit_breaker()` in `circuit_breaker.py`
- [x] 4.2 Thread the callback through `_extract_media_url_internal` to `extract_media_url`
- [x] 4.3 Verify that the circuit breaker does NOT count progress callbacks as success/failure signals

## 5. Worker Wiring

- [x] 5.1 In `process_next_job`, create a closure that receives progress dicts and calls `pubsub.publish_job_progress(job.user_id, progress_data)`
- [x] 5.2 Pass the closure as `progress_callback` to `extract_media_with_circuit_breaker`
- [x] 5.3 Ensure the progress publish is fire-and-forget (log but don't fail extraction on pub/sub error)

## 6. SSE Dual-Channel Subscription

- [x] 6.1 In `sse.py`, subscribe to `job_progress:{user_id}` alongside the existing `job_status:{user_id}` subscription
- [x] 6.2 Emit progress events as `ServerSentEvent(event="progress_update", data=json.dumps(job_data))`
- [x] 6.3 Handle fallback to polling gracefully (no progress events when pub/sub is unavailable)

## 7. Frontend Progress Bar CSS

- [x] 7.1 Add `.download-progress` container styles (full-width bar, height ~4px, rounded, animated fill)
- [x] 7.2 Add `.download-progress-fill` with amber/jade color tokens matching existing design system
- [x] 7.3 Add `.download-progress-text` style for percentage/speed/ETA label alongside the bar
- [x] 7.4 Add indeterminate animation class for unknown total_bytes cases
- [x] 7.5 Add smooth CSS transition on the fill width (`transition: width 0.4s ease-out`)

## 8. Frontend Dashboard Integration

- [x] 8.1 Add `progress_update` event listener in the SSE handler alongside existing `job_update` listener
- [x] 8.2 Extend `getRowHTML(data)` to include a hidden `.download-progress` container with bar and label
- [x] 8.3 Extend `updateDownloadRow(row, data)` to animate the progress bar when `data.progress` is present
- [x] 8.4 Add `createDownloadRow(data)` fallback for progress events arriving before a `job_update` for that job
- [x] 8.5 Ensure progress bar hides and download button appears on `status: "completed"`
- [x] 8.6 Add human-readable formatting helpers for speed (bytes/sec → MB/s) and ETA (seconds → "Xs remaining")
