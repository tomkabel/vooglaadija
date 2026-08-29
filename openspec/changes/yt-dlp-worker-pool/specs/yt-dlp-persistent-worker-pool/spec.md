## ADDED Requirements

### Requirement: yt-dlp extraction uses a persistent worker pool

The extraction path SHALL use a pool of persistent yt-dlp worker subprocesses
instead of spawning a fresh `python -c` subprocess per job. Each worker SHALL
import `yt_dlp` once and serve multiple extraction requests.

#### Scenario: Worker imports yt_dlp once per slot
- **WHEN** the pool is started with `N` workers
- **THEN** each worker process SHALL import `yt_dlp` a single time and reuse it
  across all jobs dispatched to that slot

#### Scenario: Jobs are dispatched over stdin/stdout JSON
- **WHEN** an extraction job is submitted
- **THEN** the caller SHALL write a JSON request line to the worker's stdin
- **THEN** the worker SHALL emit progress lines (`{"progress": true, ...}`) and
  exactly one terminal JSON line (result dict or `{"error": ...}`) to stdout

#### Scenario: Workers are reused across jobs
- **WHEN** a job completes successfully
- **THEN** the worker SHALL remain alive and be returned to the pool for reuse

#### Scenario: Pool size matches concurrency
- **WHEN** the pool is created
- **THEN** its size SHALL equal `YT_DLP_EXTRACTION_CONCURRENCY`

### Requirement: Timeout/kill semantics preserved against the pool

The per-job timeout handling SHALL kill the timed-out worker's process group
using SIGTERM, wait briefly, walk `/proc` for detached ffmpeg children, then
escalate to SIGKILL, and SHALL respawn the slot so the pool stays at full size.

#### Scenario: Timed-out worker is killed and respawned
- **WHEN** a worker does not respond within `YT_DLP_TIMEOUT`
- **THEN** the pool SHALL send SIGTERM (and SIGCONT) to the worker process group
- **THEN** the pool SHALL walk `/proc` for orphaned children and send SIGKILL
- **THEN** the pool SHALL respawn a replacement worker for the slot

#### Scenario: killpg failures are ignored
- **WHEN** `os.killpg` raises `ProcessLookupError` or `OSError`
- **THEN** the pool SHALL silently continue cleanup

### Requirement: Public extraction contract unchanged

`extract_media_url` SHALL retain its signature, return type, UUID-only file
paths, SSRF checks, progress-callback forwarding, and throttle detection.

#### Scenario: extract_media_url behavior is preserved
- **WHEN** `extract_media_url` is called
- **THEN** it SHALL return `(file_path, file_name, title)` as before
- **THEN** it SHALL forward `progress_callback` to the underlying extraction
- **THEN** throttle detection SHALL still run on the returned per-job stderr
