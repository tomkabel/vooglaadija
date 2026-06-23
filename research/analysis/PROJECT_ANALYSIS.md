# Project Analysis: Vooglaadija (YouTube Link Processor)

**Generated:** 2026-04-10

---

## Overview

**Vooglaadija** is a production-grade asynchronous YouTube media download service built with FastAPI. It provides authenticated users the ability to queue YouTube video download jobs, track their status in real-time, and download processed files. The system uses a distributed architecture with separate API and worker processes communicating via Redis queues.

---

## Senior Developer Noteworthy Points

### 1. Transactional Outbox Pattern

The project implements the **transactional outbox pattern** for reliable job enqueueing. Jobs are written to an `outbox` table in the same transaction as the `DownloadJob`, guaranteeing atomicity. A background worker (`sync_outbox_to_queue`) processes the outbox and marks entries as enqueued. This prevents the classic dual-write problem where a database commit succeeds but Redis enqueue fails.

**Key files:**

- `app/models/outbox.py` - Outbox model definition
- `app/services/outbox_service.py` - Outbox write helper
- `worker/processor.py` - `sync_outbox_to_queue()` implementation

### 2. Graceful Degradation with Path Traversal Prevention

- Jobs use UUID-only file paths (never user-provided titles) for filesystem operations
- Title sanitization (`_sanitize_title`) only affects display names, not storage
- Path validation using `os.path.realpath()` ensures files can't escape the download directory
- `_validate_path_within()` in `yt_dlp_service.py` and `_validate_file_path()` in `downloads.py`

### 3. Subprocess Isolation for yt-dlp

Media extraction runs via `asyncio.create_subprocess_exec` with `start_new_session=True`, allowing the worker to kill the entire process group (including ffmpeg descendants) on timeout using `os.killpg()`. This avoids orphaned processes.

**Key file:** `app/services/yt_dlp_service.py`

### 4. Atomic Job Claiming

Workers use `UPDATE ... WHERE status='pending' ... rowcount` checks to prevent race conditions where multiple workers claim the same job.

```python
result = await db.execute(
    update(DownloadJob)
    .where(DownloadJob.id == job_id, DownloadJob.status == "pending")
    .values(status="processing", updated_at=datetime.now(UTC))
)
claimed = result.rowcount == 1
```

### 5. Exponential Backoff Retry with ZSET

Retry logic uses Redis Sorted Sets (`ZRANGEBYSCORE`) to delay retries. A Lua script atomically moves due jobs from `retry_queue` to `download_queue`, preventing race conditions between workers.

### 6. Security Headers Middleware

CSP headers are dynamically generated with per-request nonces for inline scripts, demonstrating deep CSP understanding:

```python
nonce = uuid.uuid4().hex
response.headers["Content-Security-Policy"] = (
    f"default-src 'self'; "
    f"script-src 'self' 'nonce-{nonce}'; "
    ...
)
```

### 7. Secret Key Entropy Validation

The config validator computes Shannon entropy of the `SECRET_KEY` and rejects keys with entropy < 2.9 bits/character, preventing weak keys:

```python
def _estimate_entropy(text: str) -> float:
    """Estimate Shannon entropy of a string in bits."""
    # Rejects keys below 2.9 bits/char and < 32 characters
```

### 8. SQLite/PostgreSQL Compatibility Handling

The code normalizes timezone-aware vs naive datetime comparisons (SQLite quirk) in expiration checks:

```python
if expires_at.tzinfo is not None:
    expires_at = expires_at.replace(tzinfo=None)
now_naive = now_utc.replace(tzinfo=None)
if expires_at < now_naive:
    raise HTTPException(status_code=status.HTTP_410_GONE, ...)
```

### 9. Lazy Initialization Patterns

Database engine and Redis clients use lazy initialization to avoid import-time side effects and allow test environment overrides:

- `_EngineFactory` class in `app/database.py`
- `_get_redis_client()` function in `worker/queue.py`

### 10. CSRF Protection for HTMX

Dual authentication system (cookie-based for web, bearer tokens for API) with CSRF token validation for state-changing HTMX requests using `X-CSRF-Token` header.

---

## Expertise Domains

### Distributed Systems & Reliability

- Outbox pattern, retry queues, crash recovery
- Atomic job claiming, stuck job detection
- Two-phase outbox sync (claim → Redis push → mark)

### Security

- JWT with access/refresh token separation
- bcrypt password hashing (12 rounds)
- Path traversal prevention at multiple layers
- Open redirect protection via allowlist
- Rate limiting (slowapi) per endpoint
- Security headers (CSP, X-Frame-Options, etc.)
- Secret key entropy validation

### Async Python / FastAPI

- Full async/await throughout (SQLAlchemy 2.0 async, redis.asyncio)
- `asynccontextmanager` lifespan events
- `asyncpg` for PostgreSQL, `aiosqlite` for tests
- Background tasks and graceful shutdown

### Container Orchestration

- Multi-stage Dockerfile (builder → frontend-builder → app-builder → runtime-base → api/worker)
- Docker Compose with health checks, resource limits, security contexts
- Non-root user execution, read-only filesystems, capability dropping

---

## Technologies & Frameworks

| Category              | Technology                                              |
| --------------------- | ------------------------------------------------------- |
| **Backend API**       | FastAPI 0.100+, Pydantic v2                             |
| **Database**          | PostgreSQL 15 (async via asyncpg), SQLite for tests     |
| **ORM**               | SQLAlchemy 2.0 (async, declarative base, mapped_column) |
| **Migrations**        | Alembic with numbered revisions                         |
| **Queue**             | Redis 7 (async, sorted sets for retry)                  |
| **Worker**            | asyncio-based consumer with BRPOP blocking              |
| **Auth**              | JWT (python-jose), bcrypt, passlib                      |
| **Rate Limiting**     | slowapi                                                 |
| **SSE**               | sse-starlette for real-time job status                  |
| **Web UI**            | HTMX + Jinja2 templates + Tailwind CSS (build via pnpm) |
| **Media Processing**  | yt-dlp + ffmpeg                                         |
| **Metrics**           | prometheus-client                                       |
| **Testing**           | pytest, pytest-asyncio, pytest-xdist (parallel), httpx  |
| **Linting**           | ruff (with isort, pyupgrade, bugbear rules)             |
| **Type Checking**     | mypy with pydantic plugin                               |
| **Security Scanning** | bandit, safety                                          |

---

## Architectural Stack

```text
┌─────────────────────────────────────────────────────────────┐
│                        Client (Browser)                       │
│              HTMX + Tailwind CSS + SSE (via nginx)           │
└────────────────────────┬──────────────────────────────────────┘
                         │
┌────────────────────────▼──────────────────────────────────────┐
│                     nginx (reverse proxy)                      │
│              Port 8080 → API :8000, :3000 (Swagger)           │
└────────────────────────┬──────────────────────────────────────┘
                         │
┌────────────────────────▼──────────────────────────────────────┐
│                     FastAPI Application                         │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐   │
│  │  REST API   │  │  Web (HTMX)  │  │  SSE /downloads/  │   │
│  │ /api/v1/*   │  │   /web/*     │  │     stream         │   │
│  └──────┬──────┘  └──────┬───────┘  └─────────┬──────────┘   │
│         │                │                     │              │
│         └────────────────┼─────────────────────┘              │
│                          │                                    │
│              ┌────────────▼────────────┐                       │
│              │   JWT Auth / CSRF       │                       │
│              │   Rate Limiting         │                       │
│              └────────────┬────────────┘                       │
│                           │                                    │
│  ┌────────────────────────▼────────────────────────────────┐  │
│  │              PostgreSQL (asyncpg)                        │  │
│  │     users │ download_jobs │ outbox                       │  │
│  └──────────────────────────────────────────────────────────┘  │
│                           │                                    │
│  ┌────────────────────────▼────────────────────────────────┐  │
│  │              Redis (async)                               │  │
│  │   download_queue │ retry_queue (ZSET)                    │  │
│  └──────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────┘
                         │
┌────────────────────────▼──────────────────────────────────────┐
│                    Worker Process                               │
│  ┌─────────────────┐  ┌────────────────┐  ┌────────────────┐  │
│  │  BRPOP Consumer │  │  Retry Manager │  │  File Cleanup  │  │
│  │  (yt-dlp+ffmpeg)│  │  (Lua script)  │  │  (expired jobs)│  │
│  └─────────────────┘  └────────────────┘  └────────────────┘  │
│                                                              │
│  Health endpoint on :8081 for orchestration                   │
└───────────────────────────────────────────────────────────────┘
```

---

## Test Infrastructure Highlights

- **Per-worker SQLite databases** via `PYTEST_XDIST_WORKER` to enable parallel test execution without race conditions
- **Dependency overrides** at the FastAPI app level for test database injection
- **Transaction rollback** pattern (`autouse` fixture) for clean test isolation
- **Test matrix** in `pyproject.toml` (Python 3.12 × SQLite)
- **Security-aware fixtures** with proper cleanup

**Key file:** `tests/conftest.py`

---

## Docker/Kubernetes-Grade Production Features

- **Multi-stage builds** minimizing image size (7 stages)
- **SBOM generation** (cyclonedx-bom)
- **SLSA provenance** metadata
- **Read-only root filesystem** + `tmpfs` for temp directories
- **Capability dropping** (`CAP_DROP: ALL`)
- **Security opt** (`no-new-privileges`)
- **Health checks** (TCP for API, HTTP for worker)
- **Named volumes** with SELinux labels
- **Resource limits/reservations** per service
- **JSON logging** with max-size rotation
- **OpenTelemetry** integration hooks

---

## Project Structure

```text
vooglaadija/
├── app/
│   ├── api/
│   │   ├── routes/          # auth.py, downloads.py, health.py, web.py, sse.py, metrics.py
│   │   ├── dependencies/    # CurrentUser, DbSession
│   │   ├── middleware.py    # PrometheusMiddleware
│   │   └── rate_limit_config.py
│   ├── models/              # user.py, download_job.py, outbox.py
│   ├── schemas/             # download.py, user.py, token.py, error.py
│   ├── services/            # auth_service.py, yt_dlp_service.py, outbox_service.py
│   ├── utils/               # validators.py, exceptions.py
│   ├── main.py              # FastAPI app with lifespan, middleware, routes
│   ├── config.py            # Settings with entropy validation
│   ├── auth.py              # JWT create/verify, cookie helpers
│   ├── database.py          # Async engine factory (lazy init)
│   └── metrics.py           # Prometheus metrics
├── worker/
│   ├── main.py              # Worker loop with graceful shutdown
│   ├── processor.py         # Job processing, retry logic
│   ├── queue.py             # Redis queue operations (lazy init)
│   └── health.py            # Health server for orchestration
├── alembic/versions/       # Numbered migrations (001-008)
├── frontend/               # Tailwind CSS build (pnpm)
├── tests/                  # Unit & integration tests
│   ├── conftest.py          # Per-worker DB fixtures
│   ├── test_api/            # API endpoint tests
│   ├── test_services/       # Service layer tests
│   └── test_worker/         # Worker tests
├── docker-compose.yml       # Full stack with observability
├── Dockerfile              # Multi-stage build (7 stages)
└── pyproject.toml          # Hatch config, pytest, ruff, mypy
```

---

## Key API Endpoints

| Method | Path                           | Description                  |
| ------ | ------------------------------ | ---------------------------- |
| POST   | `/api/v1/auth/register`        | User registration            |
| POST   | `/api/v1/auth/login`           | User login (returns JWT)     |
| POST   | `/api/v1/auth/refresh`         | Refresh access token         |
| GET    | `/api/v1/auth/me`              | Current user profile         |
| POST   | `/api/v1/downloads`            | Create download job          |
| GET    | `/api/v1/downloads`            | List user's jobs (paginated) |
| GET    | `/api/v1/downloads/{id}`       | Get job status               |
| GET    | `/api/v1/downloads/{id}/file`  | Download file                |
| POST   | `/api/v1/downloads/{id}/retry` | Retry failed job             |
| DELETE | `/api/v1/downloads/{id}`       | Delete job                   |
| GET    | `/web/downloads`               | Dashboard (HTMX)             |
| GET    | `/web/downloads/stream`        | SSE real-time updates        |
| GET    | `/health`                      | Health check                 |
| GET    | `/metrics`                     | Prometheus metrics           |

---

## Security Considerations Implemented

1. **Authentication**: JWT access (15min) + refresh (7 days) tokens
1. **Passwords**: bcrypt with 12 rounds, minimum 8 characters
1. **Cookies**: HttpOnly, Secure, SameSite=Lax
1. **CSRF**: Dual tokens for HTMX form submissions
1. **Rate Limiting**: 5/min for auth, 10/min for downloads
1. **CORS**: Configurable allowed origins
1. **Path Traversal**: UUID-based paths, realpath validation
1. **Open Redirect**: Allowlist-based URL validation
1. **Secret Keys**: Entropy-based validation (>2.9 bits/char, ≥32 chars)
1. **Security Headers**: CSP, X-Frame-Options, X-Content-Type-Options, etc.
