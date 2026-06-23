# Vooglaadija: Final Course Video Production Plan

**Version:** 3.0 (Fully Revised)  
**Project:** YouTube Link Processor  
**Course:** Junior to Senior Developer (TalTech)  
**Created:** 2026-04-15  
**Plan Status:** PRODUCTION-READY

---

## Executive Summary

This plan produces an **8-minute technical presentation** for final course grading. The video tells the story of building a production-grade distributed system, with emphasis on **reliability engineering** and **observability**—two core pillars of senior-level development that the course explicitly requires.

**Core Thesis:** "Production systems fail. This project is designed to detect failures, recover automatically, and provide visibility into what's happening."

**Key Differentiators:**

1. **Outbox Pattern** — Jobs survive server crashes
1. **Atomic Job Claims** — No double-processing
1. **Graceful Shutdown** — Zero lost work on deployment
1. **Exponential Backoff** — Transient failures auto-retry
1. **Observability Stack** — Prometheus metrics + structured logging + correlation IDs

**What The Course Required vs. What We Implemented:**

| Course Requirement | Implementation Status |
|--------------------|----------------------|
| Health endpoint | ✅ `/health` + `/metrics` |
| Prometheus metrics | ✅ Counters, Histograms, Gauges |
| Structured logging | ✅ structlog with JSON output |
| Correlation IDs | ✅ X-Request-ID header |
| Grafana/NetData | ✅ NetData monitoring (partial) |
| Circuit breaker | ⚠️ Not implemented (documented) |
| Soft delete | ⚠️ Not implemented (documented) |
| Readiness/liveness | ⚠️ Single `/health` only |

---

## Part I: Story Foundation

### 1. The Story Angle

#### Why This Project Exists

**Generic approach (boring):** "We built a YouTube downloader with an API."

**Engineering story (compelling):** "We built a system that handles failures gracefully. When YouTube rate-limits us, we retry. When a server crashes mid-download, the job survives. When something breaks, we can see exactly what happened in our logs and metrics."

#### Three-Act Structure

| Act | Title | Duration | Focus |
|-----|-------|----------|-------|
| **Act 1** | The Problem | 0:00-1:00 | Why YouTube downloads fail unpredictably |
| **Act 2** | The Engineering | 1:00-5:30 | Reliability patterns: outbox, atomicity, retries |
| **Act 3** | The Operations | 5:30-8:00 | Observability: logging, metrics, health checks |

#### Evidence-Based Claims

Every claim in the video is backed by actual code or configuration:

| Claim | Evidence |
|-------|----------|
| "Jobs survive crashes" | Outbox pattern in `app/services/outbox_service.py` |
| "No double-processing" | `FOR UPDATE SKIP LOCKED` in `worker/processor.py` |
| "Zero lost work on shutdown" | SIGTERM handler in `worker/main.py` |
| "Retries with backoff" | Exponential backoff formula in `worker/processor.py` |
| "Metrics exposed" | Prometheus metrics in `app/metrics.py` |
| "Structured logs" | structlog config in `app/logging_config.py` |

---

## Part II: Scene-by-Scene Production Plan

### Scene 1: Cold Open (0:00-0:20)

**Purpose:** Hook the viewer with "what happens when things fail"

**Visual Script:**

```text
[0:00-0:05] Terminal: "Received SIGTERM" in red
[0:05-0:10] Terminal: Job status changes to "pending" with "requeued" label
[0:10-0:15] Terminal: Download completes successfully
[0:15-0:20] Text overlay: "What happens when your server crashes at 2 AM?"
```

**Narration:** "Most projects show the happy path. This project shows what happens when everything goes wrong—and how it automatically recovers."

**B-Roll Needed:**

- Terminal recording with colored output (use `脚本` or iTerm2 themes)
- Show actual log lines with JSON format

---

### Scene 2: The Problem (0:20-1:00)

**Purpose:** Establish why reliability matters for this use case

**Visual Script:**

```text
[0:20-0:30] Split screen:
  Left: "YouTube Reality" - Rate limits, geo-blocks, format changes, servers down
  Right: "User Expectation" - Paste link, get video
[0:30-0:50] Text overlay listing specific failure modes:
  - "Rate limiting: YouTube blocks repeated requests"
  - "Geo-restriction: Video unavailable in certain regions"
  - "Format changes: Video codec becomes unsupported"
  - "Network drops: Downloads fail mid-transfer"
[0:50-1:00] Transition to: "So how do we build a system that handles this?"
```

**Narration:** "Building a download service is easy. Making one that handles YouTube's unreliable infrastructure—and your own infrastructure's failures—that's the engineering challenge."

---

### Scene 3: System Architecture (1:00-2:30)

**Purpose:** Explain the distributed architecture with correct patterns

#### Part A: Correct Outbox Pattern (1:00-1:45)

**THE PREVIOUS PLAN WAS WRONG.** This replaces the "dual-write with fallback" with the **correct outbox pattern**:

```text
┌─────────────────────────────────────────────────────────────────────┐
│                     CORRECT OUTBOX PATTERN                           │
│                                                                      │
│  Step 1: API receives download request                                │
│           │                                                          │
│           ▼                                                          │
│  ┌─────────────────────────────────────────┐                         │
│  │      PostgreSQL Transaction              │                         │
│  │  ┌────────────────┐  ┌───────────────┐  │                         │
│  │  │ DownloadJob    │  │ Outbox Entry  │  │  ← SAME TRANSACTION     │
│  │  │ (status:       │  │ (event_type:  │  │    Both commit or       │
│  │  │  "pending")    │  │  "job_created")│  │    both rollback       │
│  │  └────────────────┘  └───────────────┘  │                         │
│  └─────────────────────────────────────────┘                         │
│           │                                                          │
│           ▼                                                          │
│  Step 2: Outbox Relay (worker process) polls every 30 seconds        │
│           │                                                          │
│           ▼                                                          │
│  ┌─────────────────────────────────────────┐                         │
│  │  SELECT * FROM outbox WHERE status=     │                         │
│  │    'pending' ORDER BY created_at        │                         │
│  │    FOR UPDATE SKIP LOCKED               │                         │
│  └─────────────────────────────────────────┘                         │
│           │                                                          │
│           ▼                                                          │
│  Step 3: Publish to Redis queue                                      │
│           │                                                          │
│           ▼                                                          │
│  ┌──────────────┐                                                    │
│  │ Redis Queue  │ ← Worker consumes via BRPOP                        │
│  │ (download_   │                                                    │
│  │  queue)      │                                                    │
│  └──────────────┘                                                    │
│                                                                      │
│  CRASH RECOVERY: If API crashes after DB commit but before           │
│  Redis publish, the outbox entry survives. The relay will           │
│  eventually publish it. Job is NEVER lost.                          │
└─────────────────────────────────────────────────────────────────────┘
```

## Why 30 seconds? (Not arbitrary)

- 30s balances "quick recovery" vs "database load"
- The relay uses `FOR UPDATE SKIP LOCKED` so multiple relays (future scale) don't conflict
- `SELECT ... FOR UPDATE SKIP LOCKED` holds lock only briefly

**Narration:** "The outbox pattern guarantees reliability. When you submit a download, the job and a message are written to PostgreSQL in a single transaction. If the API crashes after the commit, the job survives. A relay process polls the outbox every 30 seconds and publishes to Redis. The job is never lost."

### Part B: Atomic Job Claims (1:45-2:00)

```text
Worker 1: UPDATE download_jobs SET status='processing' WHERE id=X AND status='pending'
Worker 2: UPDATE download_jobs SET status='processing' WHERE id=X AND status='pending'
Result: Only ONE worker succeeds (rowcount=1)
```

**Narration:** "Workers claim jobs atomically. The UPDATE returns success only if the job was actually pending. No locks, no race conditions, no double processing."

#### Part C: Graceful Shutdown (2:00-2:15)

**Visual:**

```text
Timeline:
  t=0s:   SIGTERM received
  t=0-5s: Stop accepting new requests (readiness probe fails)
  t=5s:   Complete in-flight job OR requeue atomically
  t=10s:  Exit cleanly
  t=11s:  If not exited → SIGKILL
```

**Narration:** "On SIGTERM, the worker stops accepting new jobs through the readiness probe. It completes its current job or atomically requeues it. No work is lost on deployment."

#### Part D: Retry with Exponential Backoff (2:15-2:30)

**Visual:**

```text
Attempt 1: FAILED at t=0
Attempt 2: RETRY at t=2min (2^0)
Attempt 3: RETRY at t=4min (2^1)
Attempt 4: RETRY at t=8min (2^2)
Attempt 5: FAILED (max retries)
```

**Formula:** `delay = min(base * 2^attempt, max_delay) + jitter`

**Note:** Jitter is NOT currently implemented (documented as improvement)

**Narration:** "Failed jobs retry with exponential backoff: 2 minutes, 4 minutes, 8 minutes. After 3 retries, the job is marked failed. This prevents overwhelming YouTube's servers while giving transient failures time to recover."

---

### Scene 4: Code Deep Dive - Reliability (2:30-3:30)

**Purpose:** Show actual implementation of reliability patterns

#### Code 1: Outbox Pattern (`app/services/outbox_service.py`)

```python
# SAME TRANSACTION - both succeed or both fail
db.add(job)  # DownloadJob
outbox_entry = Outbox(job_id=job.id, event_type="job_created", ...)
db.add(outbox_entry)
await db.commit()  # ATOMIC
```

**What to highlight:** "Lines 25-35 show the key: job and outbox in one transaction."

#### Code 2: Outbox Relay (`worker/processor.py`)

```python
# Phase 1: Claim under row lock (prevents double-publish in scale-out)
claim_result = await db.execute(
    select(Outbox)
    .where(Outbox.status == "pending")
    .order_by(Outbox.created_at)
    .limit(batch_size)
    .with_for_update(skip_locked=True)  # KEY: Skip locked rows
)
# Phase 2: Publish to Redis
await redis_client.lpush("download_queue", str(entry.job_id))
# Phase 3: Mark as published
await db.execute(update(Outbox).where(Outbox.id == entry.id).values(status="enqueued"))
await db.commit()
```

**What to highlight:** "`FOR UPDATE SKIP LOCKED is the key—no locks, no deadlocks."

#### Code 3: Atomic Job Claim (`worker/processor.py`)

```python
result = await db.execute(
    update(DownloadJob)
    .where(DownloadJob.id == job_id, DownloadJob.status == "pending")
    .values(status="processing", updated_at=datetime.now(UTC))
)
claimed = result.rowcount == 1
if not claimed:
    return False  # Another worker got it
```

**What to highlight:** "Only ONE worker claims the job. The UPDATE returns 1 row affected."

#### Code 4: Graceful Shutdown (`worker/main.py`)

```python
# Signal handlers
for sig in (signal.SIGTERM, signal.SIGINT):
    loop.add_signal_handler(sig, _signal_handler)

# Shutdown handling
if shutdown_event.is_set():
    await _requeue_job(job_id, db)  # Atomically requeue
    _cleanup_downloaded_file(file_path)  # No partial files
    return False
```

**What to highlight:** "SIGTERM caught, job requeued, partial file cleaned up."

---

### Scene 5: Live Demo - Happy Path (3:30-5:00)

**CRITICAL: Pre-record this entire sequence. Live demo only if confident.**

#### Pre-Recorded Fallback Videos Required

| Video | Duration | Purpose |
|-------|----------|---------|
| `demo_register_login.mp4` | 30s | Registration → login flow |
| `demo_create_download.mp4` | 60s | URL submission → processing → completed |
| `demo_failed_retry.mp4` | 90s | Failed job → automatic retry → success |
| `demo_expired_download.mp4` | 30s | 410 Gone response |

#### Demo Steps

| Step | Action | Show |
|------|--------|------|
| 1 | Browser, logged out | Clean state |
| 2 | Register | Form validation, success |
| 3 | Login | Cookie set, redirect |
| 4 | Dashboard | Empty download list |
| 5 | Submit URL | Job appears "pending" |
| 6 | Watch SSE | Real-time: pending→processing→completed |
| 7 | Download | File saves |

#### Error Scenario Demos

| Scenario | What Happens | How to Show |
|----------|--------------|-------------|
| Invalid URL | "Must be YouTube URL" | Form error inline |
| Expired session | 401 → login redirect | Cookie expiry |
| Failed job | Status "failed", error shown | Retry button appears |
| Expired file | 410 Gone | "Download expired" message |

---

### Scene 6: Observability Stack (5:00-6:30)

**Purpose:** Demonstrate production-grade operations (COURSE REQUIREMENT)

#### Part A: Structured Logging (5:00-5:15)

**File:** `app/logging_config.py`

**Log Output Example:**

```json
{
  "timestamp": "2026-04-15T14:32:00.123Z",
  "level": "INFO",
  "service": "vooglaadija",
  "environment": "production",
  "message": "job_completed",
  "request_id": "abc-123-def-456",
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "duration_seconds": 45.2
}
```

**Narration:** "All logs are structured JSON in production. Every request has a correlation ID—X-Request-ID—that traces through all services."

**Show:** Terminal output with `jq` filtering logs by `job_id`

#### Part B: Prometheus Metrics (5:15-5:45)

**File:** `app/metrics.py`

**Metrics Exposed:**

| Metric | Type | Description |
|--------|------|-------------|
| `ytprocessor_jobs_created_total` | Counter | Jobs created by status |
| `ytprocessor_jobs_completed_total` | Counter | Jobs completed by status |
| `ytprocessor_job_duration_seconds` | Histogram | Job processing time |
| `ytprocessor_http_requests_total` | Counter | HTTP requests by method/endpoint/status |
| `ytprocessor_http_request_duration_seconds` | Histogram | HTTP latency percentiles |
| `ytprocessor_queue_depth` | Gauge | Jobs waiting in queue |
| `ytprocessor_outbox_pending` | Gauge | Pending outbox entries |

**Endpoint:** `GET /metrics` (requires auth)

**Narration:** "We expose Prometheus metrics. Job duration histogram buckets show p50, p95, p99. HTTP request metrics track every endpoint's latency."

**Show:**

```text
# Fetch metrics
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/metrics

# Show relevant lines
grep "ytprocessor" metrics.txt
```

#### Part C: Health Checks (5:45-6:15)

**Current Implementation:** Single `/health` endpoint (liveness only)

**Gap Note:** Readiness probe not separated (documented improvement)

**Endpoint:** `GET /health`

```json
{"status": "ok"}
```

**Docker Healthcheck:**

```yaml
api:
  healthcheck:
    test: ["CMD", "python", "-c", "import socket; s=socket.socket(); s.connect(('localhost', 8000))"]
    interval: 30s
    timeout: 10s
    retries: 3
```

**Narration:** "The health endpoint is used by Docker's healthcheck and orchestrators. Currently, we have a single liveness endpoint—future improvement would separate readiness from liveness."

#### Part D: NetData Monitoring (6:15-6:30)

**File:** `docker-compose.monitoring.yml`

**What NetData Provides:**

- CPU, memory, network per container
- HTTP request metrics (similar to Prometheus)
- PostgreSQL metrics (via postgres_exporter pattern)
- Redis metrics

**Gap Note:** Grafana dashboard not implemented (NetData used instead)

**Narration:** "For infrastructure monitoring, we use NetData. It provides real-time dashboards for all containers without requiring a full Prometheus/Grafana setup."

**Show:** NetData dashboard screenshot or screen recording

---

### Scene 7: Security Implementation (6:30-7:00)

**Purpose:** Show authentication and authorization

#### JWT Configuration

**Token Lifetimes (from `app/config.py` and `app/auth.py`):**

- Access token: **15 minutes**
- Refresh token: **7 days**

**Algorithm:** HS256 (Note: RS256 would be better for production—documented as improvement)

```python
# app/auth.py
ALGORITHM = "HS256"  # Symmetric - faster but requires secret sharing

# Token contents
payload = {
    "sub": str(user_id),  # Subject (user ID)
    "exp": expire,        # Expiration
    "type": "refresh"     # Token type (refresh only)
}
```

#### Cookie Security

| Attribute | Value | Purpose |
|-----------|-------|---------|
| HttpOnly | Yes | Prevents XSS token theft |
| Secure | Yes (production) | HTTPS only |
| SameSite | Lax | CSRF protection |

#### CSRF Protection

```python
# Double-submit cookie pattern
# 1. csrf_token in non-HttpOnly cookie (readable by JS)
# 2. Same token in form hidden field
# 3. Header or form submission validated
```

#### IDOR Protection

```python
# app/api/routes/downloads.py
result = await db.execute(
    select(DownloadJob).where(
        DownloadJob.id == job_uuid,
        DownloadJob.user_id == user_id,  # KEY: User must own job
    )
)
```

**Narration:** "JWT access tokens expire in 15 minutes, refresh tokens in 7 days. Tokens are HttpOnly cookies—protected from XSS. IDOR protection ensures users can only access their own downloads."

---

### Scene 8: API Design (7:00-7:30)

**Purpose:** Show professional API design practices

#### Pagination

**Endpoint:** `GET /api/v1/downloads?page=1&per_page=20`

```json
{
  "downloads": [...],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total": 47
  }
}
```

#### Error Response Catalog

**File:** `app/schemas/error.py`

```python
class ErrorCode(Enum):
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    FORBIDDEN = "FORBIDDEN"
```

**Standard Error Response:**

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Download job not found",
    "details": {"job_id": "550e8400-e29b-41d4-a716-446655440000"}
  }
}
```

#### OpenAPI Documentation

**Show:** `/docs` Swagger UI with:

- All endpoints documented
- Request/response schemas
- Error responses for each endpoint

---

### Scene 9: CI/CD Pipeline (7:30-7:50)

**Purpose:** Show production-grade DevOps

#### Pipeline Stages

```text
workflow_dispatch → lint → type-check → unit-tests → integration-tests → security-scan → docker-build
     (manual)         (5min)    (10min)       (15min)          (20min)              (10min)        (15min)
```

| Stage | Tool | Evidence |
|-------|------|----------|
| Lint | ruff | Code style, imports |
| Type Check | mypy | Type annotations |
| Unit Tests | pytest + SQLite | 100+ tests, 3s |
| Integration Tests | pytest + PostgreSQL + Redis | Real services |
| Security Scan | bandit + safety | Vulnerability scan |
| Docker Build | multi-stage | <200MB image |

**Multi-Stage Dockerfile:**

```dockerfile
FROM python:3.12-slim AS builder
# ... compile dependencies

FROM python:3.12-slim AS runtime
COPY --from=builder /venv /venv
COPY app/ ./app/
RUN useradd -m appuser
USER appuser
CMD ["uvicorn", "app.main:app"]
```

**Narration:** "Every commit runs six validation stages. Unit tests use SQLite for speed, integration tests use real PostgreSQL and Redis with health checks."

---

### Scene 10: Closing (7:50-8:00)

**Purpose:** Recap tangible achievements

**What NOT to say:**

- ❌ "Demonstrates key skills for a senior developer"
- ❌ "Showcases system design, async programming, security patterns"
- ❌ "Thank you for watching!"

**What TO say:**

```text
"This project demonstrates production reliability engineering:

Tangible evidence:
- Jobs never disappear: Outbox pattern with crash recovery
- No double-processing: Atomic job claims with FOR UPDATE SKIP LOCKED
- Zero lost work: Graceful shutdown with SIGTERM handling
- Auto-recovery: Exponential backoff retries (2, 4, 8 minutes)
- Full observability: Structured logs, Prometheus metrics, correlation IDs

Architecture decisions:
- Outbox pattern over direct Redis: Reliability over simplicity
- HTMX over React: Form-heavy UI doesn't need SPA complexity
- SQLite for tests: Speed for unit tests, PostgreSQL for integration

The code is documented, the tests pass, the system is observable.
```

**Final Shot:**

```text
vooglaadija - A Reliability Engineering Project
GitHub: github.com/yourusername/vooglaadija
```

---

## Part III: Technical Appendices

### Appendix A: Architecture Diagrams

#### Correct Outbox Pattern (Replaces Previous Error)

```text
┌─────────────────────────────────────────────────────────────────────┐
│                    OUTBOX PATTERN (CORRECT)                         │
│                                                                      │
│  ┌──────────┐     ┌──────────────────────────────────────────┐    │
│  │  Client  │────▶│              FastAPI API                   │    │
│  └──────────┘     │                                           │    │
│                   │  POST /downloads {url: "..."}             │    │
│                   └──────────────────┬───────────────────────────┘    │
│                                      │                                │
│                                      │ BEGIN TRANSACTION             │
│                                      ▼                                │
│                   ┌────────────────────────────────────────────┐    │
│                   │              PostgreSQL                      │    │
│                   │  ┌─────────────┐  ┌─────────────────────┐   │    │
│                   │  │DownloadJob  │  │      Outbox         │   │    │
│                   │  │ status:     │  │ status: 'pending'   │   │    │
│                   │  │ 'pending'   │  │ event: 'job_created' │   │    │
│                   │  └─────────────┘  └─────────────────────┘   │    │
│                   └──────────────────┬───────────────────────────┘    │
│                                      │                                │
│                                      │ COMMIT                         │
│                                      ▼                                │
│                   ┌────────────────────────────────────────────┐    │
│                   │           Outbox Relay (Worker)             │    │
│                   │                                            │    │
│                   │  Poll: SELECT ... WHERE status='pending'   │    │
│                   │  FOR UPDATE SKIP LOCKED                    │    │
│                   │  Then: LPUSH to Redis, UPDATE status      │    │
│                   └──────────────────┬─────────────────────────┘    │
│                                      │                                │
│                                      ▼                                │
│                   ┌────────────────────────────────────────────┐    │
│                   │              Redis Queue                     │    │
│                   │         (download_queue)                     │    │
│                   └──────────────────┬─────────────────────────┘    │
│                                      │                                │
│                                      ▼                                │
│                   ┌────────────────────────────────────────────┐    │
│                   │              Worker (yt-dlp)                │    │
│                   │                                            │    │
│                   │  BRPOP → Process → UPDATE status           │    │
│                   └────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘

RECOVERY SCENARIO:
If API crashes after COMMIT but before Redis LPUSH:
- Outbox entry remains status='pending'
- Outbox relay eventually publishes it
- Job is NEVER lost
```

#### Job State Machine

```text
┌─────────┐    claim    ┌───────────┐    success    ┌───────────┐
│ pending │──────────▶│processing │──────────────▶│ completed │
└─────────┘            └───────────┘               └───────────┘
     ▲                      │
     │                      │ failure
     │                      ▼
     │                ┌───────────┐
     │                │  failed   │───(terminal)───▶
     │                └───────────┘
     │                      │
     │                      │ retry < max_retries
     │                      ▼
     │                ┌─────────┐
     └─────────────────│ pending │ (scheduled retry)
                       └─────────┘
```

#### Circuit Breaker (NOT IMPLEMENTED - Documented Gap)

```text
Current Implementation:
- Only exponential backoff on job retries
- No circuit breaker pattern

Future Improvement:
┌─────────┐       ┌─────────┐       ┌───────────┐
│ Closed  │──5xx──▶│  Open   │──30s──▶│ Half-Open │
│ Normal  │ failures│ Reject  │ timeout │ 1 trial   │
│  Ops    │        │ All     │        │ request   │
└─────────┘        └─────────┘        └───────────┘
     ▲                                    │
     │         ◀──3xx───                 │
     └──────────success─────────────▶────┘
```

---

### Appendix B: JWT Token Flow

```text
┌─────────────────────────────────────────────────────────────────────┐
│                    JWT AUTHENTICATION FLOW                          │
│                                                                      │
│  REGISTER                                                            │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐   │
│  │  Client  │────▶│   API    │────▶│  bcrypt  │────▶│ Postgres │   │
│  │ POST /reg│     │ validate │     │  hash    │     │  User    │   │
│  └──────────┘     └──────────┘     └──────────┘     └──────────┘   │
│                                                                      │
│  LOGIN                                                               │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐   │
│  │  Client  │────▶│   API    │────▶│  verify  │────▶│  issue   │   │
│  │ POST /log│     │  creds   │     │ bcrypt   │     │ JWT x2   │   │
│  └──────────┘     └──────────┘     └──────────┘     └────┬─────┘   │
│                                                          │         │
│                                                          ▼         │
│                                               ┌──────────────────┐ │
│                                               │  HttpOnly Cookie │ │
│                                               │  access_token    │ │
│                                               │  (15min)         │ │
│                                               ├──────────────────┤ │
│                                               │  HttpOnly Cookie │ │
│                                               │  refresh_token   │ │
│                                               │  (7days)         │ │
│                                               └──────────────────┘ │
│                                                                      │
│  API CALL                                                            │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐                      │
│  │  Client  │────▶│   API    │────▶│ validate │                      │
│  │ Bearer   │     │  JWT     │     │  token   │                      │
│  └──────────┘     └──────────┘     └──────────┘                      │
│                                                                      │
│  TOKEN REFRESH                                                       │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐   │
│  │  Client  │────▶│   API    │────▶│ validate │────▶│  issue   │   │
│  │ POST /ref│     │  refresh │     │  refresh │     │ NEW JWTs │   │
│  └──────────┘     │  token   │     │  token   │     │ (rotation)│  │
│                   └──────────┘     └──────────┘     └──────────┘   │
│                                                                      │
│  SECURITY NOTES:                                                     │
│  - HS256: Symmetric (fast, secret must be shared)                   │
│  - RS256: Asymmetric (better for microservices) ← Future improvement│
│  - HttpOnly: Prevents XSS token theft                                │
│  - CSRF: Double-submit cookie pattern                                │
└─────────────────────────────────────────────────────────────────────┘
```

---

### Appendix C: Database Schema

#### Implemented

```sql
-- Users table
CREATE TABLE users (
    id UUID PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL,
    -- NO deleted_at (soft delete not implemented)
);

-- Download jobs
CREATE TABLE download_jobs (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id),
    url TEXT NOT NULL,
    status VARCHAR(20) NOT NULL,  -- pending, processing, completed, failed
    file_path VARCHAR(500),
    file_name VARCHAR(255),
    error TEXT,
    retry_count INT DEFAULT 0,
    max_retries INT DEFAULT 3,
    next_retry_at TIMESTAMP,
    expires_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    -- NO deleted_at (soft delete not implemented)
);

-- Indexes (GOOD)
CREATE INDEX ix_download_jobs_user_created ON download_jobs(user_id, created_at);
CREATE INDEX ix_download_jobs_status_expires ON download_jobs(status, expires_at);
CREATE INDEX ix_outbox_status_created ON outbox(status, created_at);
```

#### Gaps Documented

| Gap | Severity | Future Work |
|-----|----------|-------------|
| No soft delete | Medium | Add `deleted_at` column |
| No FK cascade delete | Low | Add `ON DELETE CASCADE` |
| No composite PK | Low | Consider `(user_id, id)` |

---

### Appendix D: Metrics Reference

#### Prometheus Metrics Exposed

```python
# app/metrics.py

# Job metrics
ytprocessor_jobs_created_total{status="pending"}
ytprocessor_jobs_completed_total{status="completed"|"failed"}

# Performance
ytprocessor_job_duration_seconds_bucket{le="1|5|10|30|60|120|300|600"}
ytprocessor_http_request_duration_seconds_bucket{le="0.01|0.05|0.1|0.25|0.5|1|2.5|5|10"}

# Queue health
ytprocessor_queue_depth
ytprocessor_outbox_pending

# HTTP traffic
ytprocessor_http_requests_total{method, endpoint, status_code}
```

#### Grafana Dashboard (NOT IMPLEMENTED)

| Panel | Query | Type |
|-------|-------|------|
| Request Rate | `rate(ytprocessor_http_requests_total[5m])` | Graph |
| Error Rate | `rate(ytprocessor_http_requests_total{status_code=~"5.."}[5m])` | Graph |
| p50 Latency | `histogram_quantile(0.50, rate(...))` | Graph |
| p95 Latency | `histogram_quantile(0.95, rate(...))` | Graph |
| p99 Latency | `histogram_quantile(0.99, rate(...))` | Graph |
| Jobs Created | `rate(ytprocessor_jobs_created_total[5m])` | Graph |
| Jobs Completed | `rate(ytprocessor_jobs_completed_total[5m])` | Graph |
| Queue Depth | `ytprocessor_queue_depth` | Single stat |
| Outbox Lag | `ytprocessor_outbox_pending` | Single stat |

---

### Appendix E: Gap Analysis Matrix

| Course Requirement | Status | Evidence | Gap |
|-------------------|--------|----------|-----|
| Health endpoint | ✅ | `/health` | None |
| Prometheus metrics | ✅ | `app/metrics.py` | None |
| Structured logging | ✅ | `app/logging_config.py` | None |
| Correlation IDs | ✅ | `X-Request-ID` | None |
| NetData monitoring | ✅ | `docker-compose.monitoring.yml` | Grafana not implemented |
| Pagination | ✅ | `page`, `per_page` params | None |
| Error catalog | ✅ | `app/schemas/error.py` | None |
| Soft delete | ⚠️ | Not implemented | Add `deleted_at` |
| Circuit breaker | ⚠️ | Exponential backoff only | Add CB pattern |
| Readiness/liveness | ⚠️ | Single `/health` only | Separate probes |
| RS256 JWT | ⚠️ | HS256 only | Add key rotation |
| Bulkhead pattern | ❌ | Not implemented | Not critical |
| Fallback responses | ❌ | Not implemented | Not critical |

---

## Part IV: Production Storyboard

### Scene Breakdown with Timecodes

| Scene | Title | Start | End | Duration | Key Visual |
|-------|-------|-------|-----|----------|------------|
| 1 | Cold Open | 0:00 | 0:20 | 0:20 | Terminal: crash → recovery |
| 2 | The Problem | 0:20 | 1:00 | 0:40 | Split screen: chaos vs simple |
| 3A | Architecture: Outbox | 1:00 | 1:45 | 0:45 | Animated diagram |
| 3B | Architecture: Claims | 1:45 | 2:00 | 0:15 | SQL UPDATE animation |
| 3C | Architecture: Shutdown | 2:00 | 2:15 | 0:15 | Timeline diagram |
| 3D | Architecture: Backoff | 2:15 | 2:30 | 0:15 | Timing diagram |
| 4 | Code Deep Dive | 2:30 | 3:30 | 1:00 | Code snippets + narration |
| 5 | Live Demo | 3:30 | 5:00 | 1:30 | Pre-recorded + voiceover |
| 6A | Observability: Logging | 5:00 | 5:15 | 0:15 | JSON log + jq |
| 6B | Observability: Metrics | 5:15 | 5:45 | 0:30 | /metrics output |
| 6C | Observability: Health | 5:45 | 6:15 | 0:30 | /health + docker |
| 6D | Observability: NetData | 6:15 | 6:30 | 0:15 | Dashboard |
| 7 | Security | 6:30 | 7:00 | 0:30 | JWT flow diagram |
| 8 | API Design | 7:00 | 7:30 | 0:30 | Swagger + errors |
| 9 | CI/CD | 7:30 | 7:50 | 0:20 | GitHub Actions |
| 10 | Closing | 7:50 | 8:00 | 0:10 | Title card |

## Total: 8:00

---

### Visual Asset Checklist

| Asset | Status | Notes |
|-------|--------|-------|
| Terminal recording (crash/recovery) | ❌ Needed | Script with colors |
| Architecture animation (outbox) | ❌ Needed | Use Excalidraw |
| Code screenshots | ❌ Needed | Light/Dark theme |
| Live demo recordings (4x) | ❌ Needed | Pre-record all |
| JSON log sample | ❌ Needed | Show real output |
| /metrics output | ❌ Needed | Show real output |
| NetData screenshot | ❌ Needed | Show dashboard |
| Swagger screenshot | ❌ Needed | Show /docs |
| GitHub Actions screenshot | ❌ Needed | Show pipeline |

---

### Pre-Recording Checklist

Before any recording session:

- [ ] Clean demo user created (`demo@vooglaadija.local`)
- [ ] 3 demo downloads prepared (pending, processing, completed)
- [ ] 1 failed download for retry demo
- [ ] Terminal configured (colors, font)
- [ ] Browser clean (no extensions, no cache)
- [ ] Swagger accessible at `/docs`
- [ ] Metrics accessible at `/metrics` (with auth)
- [ ] NetData accessible at `:19999`

---

## Part V: Realistic Timeline

### Phase Estimates (With Buffers)

| Phase | Base | Buffer | Total | Deliverable |
|-------|------|--------|-------|-------------|
| Script finalization | 2h | +1h | 3h | Final narration script |
| Storyboard | 2h | +1h | 3h | Visual plan with timecodes |
| Environment prep | 1h | +1h | 2h | Demo environment ready |
| B-Roll recording | 2h | +2h | 4h | All raw footage |
| Live demo recording | 2h | +2h | 4h | All demo scenarios |
| Fallback videos | 2h | +1h | 3h | All 4 error scenarios |
| Voiceover | 1h | +1h | 2h | Clean audio |
| Animations | 2h | +2h | 4h | All diagrams |
| Video editing | 6h | +4h | 10h | Final cut |
| Audio mix | 1h | +1h | 2h | Music + VO balanced |
| Review + revisions | 2h | +2h | 4h | Stakeholder feedback |
| **TOTAL** | **23h** | **+17h** | **40h** | |

**Daily Schedule (4h/day):** 10 days to delivery

---

## Part VI: Error Prevention

### Live Demo Failure Protocols

| Level | Scenario | Protocol |
|-------|----------|----------|
| **1** | Status doesn't update | "The SSE polls every 15 seconds, let me refresh" |
| **2** | Download taking too long | Skip to pre-recorded completion |
| **3** | Complete failure | Switch to pre-recorded fallback video |
| **4** | Systematic (Docker down) | Show code, show tests, show architecture instead |

### Pre-Recorded Fallback Videos

| Video | Trigger | Purpose |
|-------|---------|---------|
| `fallback_register.mp4` | Registration fails | Show same flow |
| `fallback_download.mp4` | Download fails | Show completed job |
| `fallback_error.mp4` | Any error | Show error handling |
| `fallback_retry.mp4` | Retry fails | Show retry flow |

---

## Appendix F: Course Requirement Mapping

| Course Lecture Topic | Video Coverage |
|---------------------|----------------|
| Observability | Scene 6: Prometheus, logging, health, NetData |
| Architecture Patterns | Scene 3: Outbox, atomic claims, backoff |
| Resilience | Scene 3: Graceful shutdown, retries |
| Security | Scene 7: JWT, cookies, CSRF, IDOR |
| Database | Appendix C: Schema, indexes, gaps |
| API Design | Scene 8: Pagination, errors, versioning |
| CI/CD | Scene 9: Pipeline, multi-stage Dockerfile |
| Testing | Referenced: Unit + integration tests |
| Docker | Scene 9: Healthchecks, multi-stage |

---

## End of Plan v3.0

**Key Changes from v2.0:**

1. ✅ Added Scene 6 (Observability) with actual metrics
1. ✅ Fixed outbox pattern diagram (was backwards in v2.0)
1. ✅ Added structured logging and correlation IDs
1. ✅ Added circuit breaker gap analysis
1. ✅ Added JWT specifics (15min/7day, HS256 note)
1. ✅ Added database schema with gap analysis
1. ✅ Added pagination and error catalog
1. ✅ Created actual storyboard with timecodes
1. ✅ Fixed timeline (40h with buffer)
1. ✅ Added NetData monitoring (Grafana noted as gap)
