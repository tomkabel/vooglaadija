# Implementation TODO — Addressing video-plan-5.md Critique

## Legend

- 🔴 CRITICAL — Code is factually wrong or dead; video claim is a lie
- 🟠 HIGH — Important mismatch between video and reality; credibility risk
- 🟡 MEDIUM — Enhancement needed to support video claims
- 🟢 LOW — Script/video cleanup only

---

## 🔴 CRITICAL

### C-1: Zombie Sweeper Is Dead Code

**File:** `worker/main.py:238`, `worker/processor.py:318-346`, `worker/zombie_sweeper.py`
**Video Claim:** "Fifteen minutes later, the zombie sweeper finds it, marks it pending, and another worker picks it up."
**Reality:** `worker/main.py` calls `reset_stuck_jobs(timeout_minutes=10)`, which marks stuck jobs as `failed`. `requeue_stuck_jobs()` in `zombie_sweeper.py` is never invoked.
**Fix:** Replace `reset_stuck_jobs` call in `worker/main.py` with `requeue_stuck_jobs`. Ensure timeout is 15 minutes to match video claim.

### C-2: JWT Payload Violates Project's Own Auth Rules

**File:** `app/auth.py:15-18`
**Video/Rule Claim:** "Include `user_id` and `email` in payload"
**Reality:** Payload only contains `sub` (user UUID string) and `exp`.
**Fix:** Add `user_id` and `email` to access token payload. Ensure `sub` remains for backward compatibility.

### C-3: `reset_stuck_jobs` Marks Jobs as `failed`, Not Requeued

**File:** `worker/processor.py:318-346`
**Reality:** This function sets `status="failed"`, error="Job timed out". The video promises recovery, not permanent failure.
**Fix:** After wiring `requeue_stuck_jobs` in main.py, either remove `reset_stuck_jobs` or repurpose it as a last-resort fail mechanism with a longer timeout.

---

## 🟠 HIGH

### H-1: Tests Run on SQLite, Not PostgreSQL

**File:** `tests/conftest.py`, `pyproject.toml`
**Video Claim:** "Because we use Testcontainers with real PostgreSQL, our concurrency tests actually test the database engine that runs in production."
**Reality:** Tests force `sqlite+aiosqlite:///` per worker. No Testcontainers. No PostgreSQL in CI.
**Fix:** Add optional PostgreSQL test target via `docker-compose.test.yml`. Update `conftest.py` to support PostgreSQL URL override. Update Hatch test matrix with a `postgres` variant. Keep SQLite as fast default; PostgreSQL as integration target.

### H-2: Outbox Sync Interval Is 5 Minutes, Not 30 Seconds

**File:** `worker/main.py:165-167`, `worker/processor.py:348`
**Video Claim:** "Every thirty seconds, the outbox relay asks PostgreSQL..."
**Reality:** `sync_outbox_to_queue` runs inside cleanup cycle every 5 minutes (`CLEANUP_INTERVAL_MINUTES=5`).
**Fix:** Add a dedicated outbox sync interval (default 30s) separate from cleanup. Run `sync_outbox_to_queue` more frequently in the main loop.

### H-3: No Automatic Correlation ID in Logs

**File:** `app/logging_config.py`, `app/main.py`
**Video Claim:** "Every log carries a correlation ID."
**Reality:** Correlation IDs are manual opt-in via `get_logger(..., request_id="abc")`. No middleware auto-injects them.
**Fix:** Add FastAPI middleware that generates `X-Request-ID` / `x-correlation-id`, stores it in `contextvars`, and ensures all logs within a request include it.

### H-4: Graceful Shutdown Behavior Is Undefined Mid-Download

**File:** `worker/processor.py:143-160`, `worker/main.py:177-185`
**Video Claim:** "We finish the current job or requeue it within twenty-five seconds."
**Reality:** If SIGTERM arrives _during_ download, the loop checks at BRPOP boundaries but `extract_media_with_circuit_breaker` is a long blocking call. The worker may SIGKILL mid-download.
**Fix:** Add periodic `shutdown_event` polling inside download logic, or wrap yt-dlp in an asyncio-shielded task with explicit cancellation handling.

---

## 🟡 MEDIUM

### M-1: Grafana Does Not Exist in Infrastructure

**File:** `docker-compose.monitoring.yml`
**Video Claim:** Grafana dashboard with p99 at 142s.
**Reality:** Monitoring stack is NetData. No Grafana service.
**Fix:** Add Grafana + Prometheus services to `docker-compose.monitoring.yml` (commented or enabled). Provide a basic dashboard JSON for `ytprocessor_job_duration_seconds`.

### M-2: Docker Image Size Claim Is False

**File:** `Dockerfile`
**Video Claim:** "Multi-stage Docker <200MB"
**Reality:** Runtime installs ffmpeg + Node.js + redis-tools + gosu on python:3.12-slim. Likely 600MB+.
**Fix:** Document actual size in video plan. Do not claim <200MB. Optionally investigate `python:3.12-alpine` or separate ffmpeg/node layers, but do not block on this.

### M-3: Worker Health Check Uses urllib, Not Proper Health Endpoint

**File:** `docker-compose.yml:136-144`
**Reality:** Worker health check in compose hits `localhost:8082/health` via urllib. The health server exists but the check could be more robust.
**Fix:** Ensure worker health endpoint returns proper JSON and compose healthcheck validates it.

### M-4: Missing `create_job_with_outbox` Function Name

**File:** Video plan references
**Video Claim:** `create_job_with_outbox`
**Reality:** Function is `write_job_to_outbox` in `app/services/outbox_service.py`. Job creation + outbox write is in `downloads.py`.
**Fix:** Update video script to reference correct function names and file paths.

---

## 🟢 LOW (Video Script Corrections)

### L-1: Wrong Container Name in Demo Script

**Video:** `docker kill vooglaadija_worker_1`
**Reality:** Container is `ytprocessor-worker`.
**Fix:** Update demo protocol in video plan.

### L-2: Unverified p99 Metric

**Video:** "p99 at 142s"
**Reality:** No benchmark exists. Factual Accuracy Log admits this is unverified.
**Fix:** Remove specific p99 from narration, or add a benchmark script to measure it.

### L-3: "Token Bucket" References

**Video/Logs:** Mentions "token bucket exhausted"
**Reality:** YouTube does not document token bucket algorithm.
**Fix:** Remove all "token bucket" language. Use "HTTP 429 Rate Limited" only.

### L-4: Video Claims Testcontainers Runtime "8s"

**Video:** "Testcontainers runtime 8s"
**Reality:** Hypothetical.
**Fix:** Say "seconds, not minutes" without exact number.

---

## Summary of Changes Required

| Area              | Files to Touch                                                          |
| ----------------- | ----------------------------------------------------------------------- |
| Worker resilience | `worker/main.py`, `worker/processor.py`, `worker/zombie_sweeper.py`     |
| Auth / JWT        | `app/auth.py`, `app/api/dependencies/__init__.py`                       |
| Observability     | `app/logging_config.py`, `app/main.py`, `docker-compose.monitoring.yml` |
| Testing           | `tests/conftest.py`, `docker-compose.test.yml`, `pyproject.toml`        |
| API / Downloads   | `app/api/routes/downloads.py` (minor docstring fixes)                   |
| Video plan        | `video-plan-5.md`                                                       |
