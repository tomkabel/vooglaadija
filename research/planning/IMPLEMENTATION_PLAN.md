# Implementation Plan: Fix All Review Findings

## Overview

This plan addresses all issues identified in the comprehensive project review, organized by priority (P0 → P1 → P2). Each step specifies the exact file(s) to modify, what to change, and how to verify.

---

## Phase 1: P0 — Ship-Blocking Security Fixes

### Step 1.1: Fix File Name Injection / Path Traversal

**File:** `app/services/yt_dlp_service.py`
**Problem:** Video title from yt-dlp metadata is used directly in `os.path.join`, allowing path traversal via titles like `../../etc/passwd`.
**Changes:**

- Replace `_sanitize_filename` approach: use ONLY the UUID-based `file_id` for the actual file path on disk
- Store the `title` (sanitized) only in the `file_name` field returned to users (never used for filesystem operations)
- The `file_path` should always be `os.path.join(download_dir, f"{file_id}.{ext}")` — the title should never appear in the path
- Add validation that `file_path` resolves to within `download_dir` (resolve symlinks with `os.path.realpath` and verify prefix)
- Add `socket_timeout` to yt-dlp options (e.g., 60 seconds) to prevent indefinite hangs
- Use `asyncio.get_running_loop()` instead of deprecated `asyncio.get_event_loop()`
- Reuse a single `ThreadPoolExecutor` instance at module level instead of creating one per request

**Verification:** Create a test that simulates a malicious video title with `../../etc/passwd` and asserts the file stays within the download directory.

### Step 1.2: Fix Arbitrary File Read

**File:** `app/api/routes/downloads.py`
**Problem:** `FileResponse(path=job.file_path)` serves whatever path is in the DB without validation.
**Changes:**

- Before serving `FileResponse`, validate that `job.file_path` resolves to within `settings.storage_path/downloads/` using `os.path.realpath` and prefix check
- Return 403 if path is outside allowed directory
- Add file existence check before serving (return 404 if file missing)
- Check `expires_at` — return 410 Gone if download has expired
- Extract duplicated job lookup query into a helper function `_get_user_job(db, user_id, job_id)`

**Verification:** Test with a `file_path` of `/etc/passwd` in the DB — should return 403, not serve the file.

### Step 1.3: Fix Missing `is_active` Check

**File:** `app/api/dependencies/__init__.py`
**Problem:** `get_current_user` does not check `user.is_active`. Deactivated users retain access with existing tokens.
**Changes:**

- After fetching the user from DB, add `if not user.is_active:` check
- Raise `HTTPException(status_code=401, detail="User account is inactive")`
- Reuse `verify_token` from `app.auth` instead of duplicating `jwt.decode` logic

**Verification:** Create user, get token, set `is_active=False`, verify token is rejected on next request.

### Step 1.4: Fix Subdomain Bypass in URL Validation

**File:** `app/utils/validators.py`
**Problem:** `"youtube.com" in netloc` matches `youtube.com.evil.com`.
**Changes:**

- Parse the netloc to extract the hostname (strip port if present)
- Use exact domain matching: `hostname in ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "youtube-nocookie.com", ...)` or use `hostname.endswith(".youtube.com") or hostname == "youtube.com"` pattern
- Validate the URL has a video ID path component (not just the bare domain)

**Verification:** Test that `https://youtube.com.evil.com/video` returns False, while `https://www.youtube.com/watch?v=abc` returns True.

### Step 1.5: Fix Hardcoded Secrets in Docker Compose

**File:** `docker-compose.yml`
**Problem:** `POSTGRES_USER: user`, `POSTGRES_PASSWORD: pass`, `DATABASE_URL` with hardcoded creds.
**Changes:**

- Use environment variable substitution: `POSTGRES_USER: ${DB_USER:-postgres}`, `POSTGRES_PASSWORD: ${DB_PASSWORD:?DB_PASSWORD is required}`, `POSTGRES_DB: ${DB_NAME:-ytprocessor}`
- Update `DATABASE_URL` to use the same variables: `postgresql+asyncpg://${DB_USER:-postgres}:${DB_PASSWORD:?DB_PASSWORD is required}@db:5432/${DB_NAME:-ytprocessor}`
- Update `SECRET_KEY` default to fail loudly: `${SECRET_KEY:?SECRET_KEY is required}`
- Update `.env.example` with placeholder values and instructions
- Remove deprecated `version: "3.8"` key

**Verification:** `docker compose config` should fail without env vars set.

### Step 1.6: Fix TOCTOU Race in Registration

**File:** `app/api/routes/auth.py`
**Problem:** SELECT → check → INSERT is not atomic. Two concurrent requests can bypass the email uniqueness check.
**Changes:**

- Wrap the insert in a try/except catching `IntegrityError` from SQLAlchemy
- On `IntegrityError`, return `HTTPException(status_code=409, detail="Email already registered")`
- Remove the pre-check SELECT (or keep it as an optimization but handle the race via the except)

**Verification:** Concurrent registration test with same email — second request should get 409, not 500.

---

## Phase 2: P1 — Data Integrity & Architecture

### Step 2.1: Fix Worker Job Processing (Two-Commit Problem)

**File:** `worker/processor.py`
**Problem:** Job is committed as `"processing"` BEFORE the download starts. If worker crashes between commits, job is stuck forever.
**Changes:**

- Move the `"processing"` status update to use the same `AsyncSessionLocal` but WITHOUT committing before the download
- Instead, use a single session: fetch job → update to processing → do download → update to completed/failed → single commit
- If the entire operation is in one transaction, a crash rolls back and the job stays `"pending"` in Redis queue
- Add try/except around the entire `process_next_job()` call in `main.py` to prevent worker crashes
- Add `socket_timeout` and `retries` to yt-dlp options

**Verification:** Simulate a crash mid-download — job should remain in a recoverable state.

### Step 2.2: Add Job Timeout Recovery

**File:** `worker/main.py`
**Problem:** Jobs stuck in `"processing"` for too long have no recovery mechanism.
**Changes:**

- Add a `reset_stuck_jobs()` function that finds jobs with `status="processing"` and `updated_at < now() - timeout` (e.g., 10 minutes)
- Reset them back to `"pending"` and re-enqueue them
- Call this in the cleanup cycle (alongside `cleanup_expired_jobs`)
- Add a `max_retries` counter on the job (requires model change) or track retry count in Redis

**Verification:** Create a job with `status="processing"` and old `updated_at` — verify it gets reset.

### Step 2.3: Consolidate Database Engine

**Files:** `worker/processor.py`, `worker/main.py`, `app/database.py`
**Problem:** Three separate `create_async_engine()` calls create three connection pools.
**Changes:**

- Remove engine/session creation from `worker/processor.py` and `worker/main.py`
- Import `AsyncSessionLocal` from `app/database.py` (or create a shared `get_worker_db` dependency)
- The worker should reuse the same engine configuration as the API

**Verification:** Worker starts and processes jobs using the shared engine.

### Step 2.4: Fix Sync Redis Blocking Event Loop

**File:** `app/middleware/rate_limit.py`
**Problem:** `async def is_allowed()` calls synchronous `self.redis.pipeline()` which blocks the event loop.
**Changes:**

- Switch to `redis.asyncio.Redis` client
- Update all Redis calls to use `await`
- Update `worker/queue.py` to use `redis.asyncio` as well

**Verification:** Rate limiting works under async load without blocking.

### Step 2.5: Add Pagination to List Endpoint

**File:** `app/api/routes/downloads.py`
**Problem:** `GET /downloads` returns all jobs without pagination.
**Changes:**

- Add `page: int = 1` and `per_page: int = 20` query parameters (max 100)
- Apply `.offset()` and `.limit()` to the query
- Return `DownloadListResponse` with `pagination` field containing `page`, `per_page`, `total`
- Update `DownloadListResponse` schema to include `pagination` field

**File:** `app/schemas/download.py`

- Add `PaginationResponse` schema with `page`, `per_page`, `total` fields
- Update `DownloadListResponse` to include it

**Verification:** Test that page 1 returns first 20 items, page 2 returns next 20.

### Step 2.6: Add Graceful Shutdown to Worker

**File:** `worker/main.py`
**Problem:** Worker runs `while True` with no signal handling. Container stop = stuck jobs.
**Changes:**

- Add `SIGTERM`/`SIGINT` signal handlers
- Set a `shutdown_event = asyncio.Event()`
- On signal, set the event
- Main loop checks `shutdown_event.is_set()` instead of `while True`
- On shutdown, log status and exit cleanly

**Verification:** Send SIGTERM to worker process — it should exit cleanly within a few seconds.

### Step 2.7: Fix File Deletion Race Condition

**File:** `app/api/routes/downloads.py`
**Problem:** `os.remove()` before `db.delete()` — if commit fails, file is gone but DB record persists.
**Changes:**

- Reverse the order: delete DB record first, commit, then delete file
- If file deletion fails, log warning but don't fail the request (DB is source of truth)
- Or wrap in a transaction with proper rollback

**Verification:** Mock `os.remove` to fail — DB record should still be deleted.

### Step 2.8: Add File Path Validation on Delete

**File:** `app/api/routes/downloads.py`
**Problem:** `os.remove(job.file_path)` could delete arbitrary files if `file_path` was set maliciously.
**Changes:**

- Same path validation as Step 1.2: verify `job.file_path` resolves to within storage directory before `os.remove`

---

## Phase 3: P2 — Testing & Code Quality

### Step 3.1: Add Worker Tests

**File:** `tests/test_worker/test_processor.py` (new)
**Changes:**

- Test `process_next_job()` with mocked yt-dlp and Redis
- Test job completion flow (pending → processing → completed)
- Test job failure flow (pending → processing → failed)
- Test missing job_id in DB (should return without error)
- Test cleanup_expired_jobs() (expired jobs deleted, files removed)
- Test stuck job recovery

**File:** `tests/test_worker/test_queue.py` (new)
**Changes:**

- Test `enqueue_job()` pushes correct job_id to Redis

### Step 3.2: Add Rate Limiter Tests

**File:** `tests/test_middleware/test_rate_limit.py` (new)
**Changes:**

- Test that N requests within window are allowed
- Test that N+1 request is rejected
- Test `get_retry_after` returns correct value
- Test window resets after expiry

### Step 3.3: Add Auth Service Unit Tests

**File:** `tests/test_services/test_auth_service.py` (new)
**Changes:**

- Test `hash_password` returns bcrypt hash
- Test `verify_password` with correct password
- Test `verify_password` with incorrect password
- Test `hash_password` with different rounds

### Step 3.4: Add JWT Auth Unit Tests

**File:** `tests/test_auth.py` (new)
**Changes:**

- Test `create_access_token` returns valid JWT
- Test `create_refresh_token` includes `type: refresh`
- Test `verify_token` with valid token
- Test `verify_token` with expired token returns None
- Test `verify_token` with invalid token returns None

### Step 3.5: Add Edge Case Tests for Auth API

**File:** `tests/test_api/test_auth.py`
**Changes:**

- Test inactive user login (should fail)
- Test expired JWT token
- Test access token used as refresh token
- Test password exactly 8 characters (boundary)

### Step 3.6: Add Edge Case Tests for Downloads API

**File:** `tests/test_api/test_downloads.py`
**Changes:**

- Test download file with expired `expires_at` (should return 410)
- Test download file with path traversal in `file_path` (should return 403)
- Test pagination parameters
- Test user isolation (user A can't access user B's jobs)

### Step 3.7: Clean Up Dead Test Code

**File:** `tests/conftest.py`
**Changes:**

- Remove unused fixtures: `auth_headers`, `sample_user_data`, `sample_urls`, `invalid_urls`, `sample_download_data`
- Or convert them to actually-used fixtures

### Step 3.8: Fix Duplicated Mock Setup in Tests

**File:** `tests/test_services/test_yt_dlp.py`
**Changes:**

- Extract the `patch("app.services.yt_dlp_service.yt_dlp.YoutubeDL")` mock setup into a shared fixture in `conftest.py`
- Reuse the fixture across all yt_dlp tests

---

## Phase 4: P2 — Code Quality & Dead Code Removal

### Step 4.1: Remove Dead Code

- **`app/utils/exceptions.py`**: `YTDLPError` is defined but never used. Either use it (replace `StorageError` in `yt_dlp_service.py`) or delete the file.
- **`app/schemas/token.py`**: `TokenData` is defined but never used. Delete it.
- **`hatch.toml`**: 52 lines of boilerplate that does nothing. Delete the file.

### Step 4.2: Add `updated_at` to Models

**Files:** `app/models/user.py`, `app/models/download_job.py`
**Changes:**

- Add `updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())`
- Create a new Alembic migration `003_add_updated_at.py`

### Step 4.3: Use Enum for Job Status

**Files:** `app/models/download_job.py`, `app/schemas/download.py`, `worker/processor.py`, `app/api/routes/downloads.py`
**Changes:**

- Create `JobStatus` enum: `PENDING = "pending"`, `PROCESSING = "processing"`, `COMPLETED = "completed"`, `FAILED = "failed"`
- Use `Mapped[JobStatus]` with `Enum(JobStatus)` column type
- Replace all magic string `"pending"`, `"completed"`, etc. with enum values
- Add `CHECK` constraint in migration

### Step 4.4: Extract Business Logic from Auth Routes

**File:** `app/services/auth_service.py`
**Changes:**

- Move registration logic (email check, user creation) into `register_user()` service function
- Move login logic (password verification, token creation) into `authenticate_user()` service function
- Move refresh logic into `refresh_tokens()` service function
- Route handlers become thin wrappers calling service functions

### Step 4.5: Fix Thread Pool Leak

**File:** `app/services/yt_dlp_service.py`
**Changes:**

- Create a module-level `ThreadPoolExecutor(max_workers=4)` singleton
- Reuse it across all calls to `extract_media_url`
- Add atexit cleanup or rely on Python's garbage collection

### Step 4.6: Use Shared Redis Client

**Files:** `worker/queue.py`, `worker/processor.py`, `app/middleware/rate_limit.py`
**Changes:**

- Create a single `redis.asyncio` client in `app/redis.py` (new file)
- Import from there in all modules that need Redis
- Remove duplicate Redis client creation

### Step 4.7: Use `verify_token` in Dependencies

**File:** `app/api/dependencies/__init__.py`
**Changes:**

- Replace direct `jwt.decode` with `verify_token()` from `app.auth`
- This eliminates code duplication and centralizes JWT logic

---

## Phase 5: P2 — Docker & Infrastructure

### Step 5.1: Add Security Headers to Nginx

**File:** `infra/nginx/default.conf`
**Changes:**

- Add `X-Frame-Options: DENY`
- Add `X-Content-Type-Options: nosniff`
- Add `X-XSS-Protection: 1; mode=block`
- Add `Strict-Transport-Security` (if TLS is added)
- Add gzip compression

### Step 5.2: Add Worker Health Check to Dockerfile

**File:** `Dockerfile` (worker target)
**Changes:**

- Add `HEALTHCHECK` instruction (e.g., check that the process is running or check Redis connectivity)
- Note: Worker health check is already implemented in the Dockerfile

### Step 5.3: Add Resource Limits to Docker Compose

**File:** `docker-compose.yml`
**Changes:**

- Add `deploy.resources.limits` for each service (memory, CPU)
- Add `LOG_LEVEL` environment variable

---

## Execution Order

1. **Phase 1** (P0) — Do first, all 6 steps. These are security-critical.
1. **Phase 2** (P1) — Do next, all 8 steps. These prevent production data issues.
1. **Phase 3** (P2 Testing) — In parallel with Phase 4. Write tests to verify Phase 1 & 2 fixes.
1. **Phase 4** (P2 Code Quality) — After Phase 1 & 2 are stable.
1. **Phase 5** (P2 Infra) — After Phase 1 & 2 are stable.

## Verification Strategy

After each phase:

1. Run `ruff check app/ tests/` — no lint errors
1. Run `pytest tests/ -v` — all tests pass
1. Run `docker compose up --build` — services start and pass health checks
1. Manual smoke test: register → login → create download → check status → download file → delete

## Estimated Effort

| Phase                | Steps  | Estimated Time  |
| -------------------- | ------ | --------------- |
| Phase 1 (P0)         | 6      | 4-6 hours       |
| Phase 2 (P1)         | 8      | 6-8 hours       |
| Phase 3 (P2 Tests)   | 8      | 4-6 hours       |
| Phase 4 (P2 Quality) | 7      | 3-4 hours       |
| Phase 5 (P2 Infra)   | 3      | 1-2 hours       |
| **Total**            | **32** | **18-26 hours** |
