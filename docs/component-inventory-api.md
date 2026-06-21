# Component Inventory — API Server

**Part:** API Server (`app/`)

---

## Route Components

| Component | File | Endpoints | Type |
|-----------|------|-----------|------|
| Auth Routes | `app/api/routes/auth.py` | register, login, refresh, me, logout | REST API (JSON) |
| Download Routes | `app/api/routes/downloads.py` | CRUD + retry + DLQ management | REST API (JSON) |
| Web Routes | `app/api/routes/web.py` | login page, register, dashboard, settings, chaos lab | HTMX (HTML) |
| SSE Routes | `app/api/routes/sse.py` | download status stream | SSE (text/event-stream) |
| Health Routes | `app/api/routes/health.py` | health check, readiness probe | REST API (JSON) |
| Metrics Routes | `app/api/routes/metrics.py` | Prometheus metrics | REST API (text) |
| Chaos Routes | `app/api/routes/chaos.py` | inject, reset, status, submit-videos | REST API (JSON) |

## Service Components

| Component | File | Responsibility |
|-----------|------|---------------|
| Auth Service | `app/services/auth_service.py` | Password hashing/verification |
| Circuit Breaker | `app/services/circuit_breaker.py` | YouTube API fault protection |
| Error Classifier | `app/services/error_classifier.py` | Error pattern matching & retry policy |
| Job Factory | `app/services/job_factory.py` | Demo job creation |
| Outbox Service | `app/services/outbox_service.py` | Transactional outbox writer |
| Pub/Sub Service | `app/services/pubsub_service.py` | Redis Pub/Sub for real-time updates |
| Retry Service | `app/services/retry_service.py` | Retry delay calculation with jitter |
| Throttle Predictor | `app/services/throttle_predictor.py` | Rate-limit risk tracking |
| yt-dlp Service | `app/services/yt_dlp_service.py` | YouTube media extraction |

## Data Model Components

| Component | File | Table |
|-----------|------|-------|
| User Model | `core/models/user.py` | users |
| DownloadJob Model | `core/models/download_job.py` | download_jobs |
| FailedJob Model | `core/models/failed_job.py` | failed_jobs |
| Outbox Model | `core/models/outbox.py` | outbox |

## Schema Components

| Component | File | Purpose |
|-----------|------|---------|
| Error Schema | `app/schemas/error.py` | Standardized error responses (ErrorCode enum) |

## Infrastructure Components

| Component | File | Purpose |
|-----------|------|---------|
| Config | `core/config.py` | Pydantic Settings (env-based) |
| Database | `core/database.py` | Async SQLAlchemy engine + session factory |
| Auth | `app/auth.py` | JWT token creation/verification |
| Metrics | `core/metrics.py` | Prometheus metric definitions |
| Logging | `core/logging_config.py` | Structlog configuration |
| Redis Client | `core/redis_client.py` | Shared Redis connection + chaos keys |
| Queue | `core/queue.py` | Redis queue helpers used by API and worker |
| Dependencies | `app/api/dependencies/` | FastDI: DbSession, CurrentUser, CurrentUserFromCookie |
| Middleware | `app/api/middleware.py` | Prometheus request metrics |
| Rate Limit | `app/api/rate_limit_config.py` | SlowAPI rate limiter configuration |
