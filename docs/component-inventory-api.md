# Component Inventory — API Server

**Part:** API Server (`app/`)

---

## Route Components

| Component            | File                                  | Endpoints                                                   | Type                    |
| -------------------- | ------------------------------------- | ----------------------------------------------------------- | ----------------------- |
| Auth Routes          | `app/api/routes/auth.py`              | register, login, refresh, me, logout                        | REST API (JSON)         |
| Download Routes      | `app/api/routes/downloads.py`         | CRUD + retry + DLQ management                               | REST API (JSON)         |
| Web Router Aggregate | `app/api/routes/web/__init__.py`      | `/web` router aggregation and compatibility re-exports      | HTMX (HTML)             |
| Web Auth Routes      | `app/api/routes/web/web_auth.py`      | login, register, demo login, logout, password change        | HTMX (HTML)             |
| Web Download Routes  | `app/api/routes/web/web_downloads.py` | dashboard, create, delete, file download                    | HTMX (HTML)             |
| Web Dashboard Routes | `app/api/routes/web/web_dashboard.py` | chaos lab, chaos lab status, slides                         | HTMX (HTML)             |
| Web Settings Routes  | `app/api/routes/web/web_settings.py`  | settings, username update, account deletion                 | HTMX (HTML)             |
| Web Package Helpers  | `app/api/routes/web/web_helpers.py`   | template context, HTMX, file/path helper ownership          | Route helpers           |
| Web Fragment Helpers | `app/api/routes/web_helpers.py`       | status badge, success, error, and HTMX rate-limit fragments | HTML helpers            |
| SSE Routes           | `app/api/routes/sse.py`               | download status stream                                      | SSE (text/event-stream) |
| Health Routes        | `app/api/routes/health.py`            | health check, readiness probe                               | REST API (JSON)         |
| Metrics Routes       | `app/api/routes/metrics.py`           | Prometheus metrics                                          | REST API (text)         |
| Chaos Routes         | `app/api/routes/chaos.py`             | inject, reset, status, submit-videos                        | REST API (JSON)         |

## Service Components

| Component          | File                                 | Responsibility                        |
| ------------------ | ------------------------------------ | ------------------------------------- |
| Auth Service       | `app/services/auth_service.py`       | Password hashing/verification         |
| Circuit Breaker    | `app/services/circuit_breaker.py`    | YouTube API fault protection          |
| Error Classifier   | `app/services/error_classifier.py`   | Error pattern matching & retry policy |
| Job Factory        | `app/services/job_factory.py`        | Demo job creation                     |
| Outbox Service     | `app/services/outbox_service.py`     | Transactional outbox writer           |
| Pub/Sub Service    | `app/services/pubsub_service.py`     | Redis Pub/Sub for real-time updates   |
| Throttle Predictor | `app/services/throttle_predictor.py` | Rate-limit risk tracking              |
| yt-dlp Service     | `app/services/yt_dlp_service.py`     | YouTube media extraction              |

## Data Model Components

| Component         | File                          | Table         |
| ----------------- | ----------------------------- | ------------- |
| User Model        | `core/models/user.py`         | users         |
| DownloadJob Model | `core/models/download_job.py` | download_jobs |
| FailedJob Model   | `core/models/failed_job.py`   | failed_jobs   |
| Outbox Model      | `core/models/outbox.py`       | outbox        |

## Schema Components

| Component    | File                   | Purpose                                       |
| ------------ | ---------------------- | --------------------------------------------- |
| Error Schema | `app/schemas/error.py` | Standardized error responses (ErrorCode enum) |

## Infrastructure Components

| Component          | File                           | Purpose                                                                |
| ------------------ | ------------------------------ | ---------------------------------------------------------------------- |
| Config             | `core/config.py`               | Pydantic Settings (env-based)                                          |
| Database           | `core/database.py`             | Async SQLAlchemy engine + session factory                              |
| Auth               | `app/auth.py`                  | JWT token creation/verification                                        |
| Metrics            | `core/metrics.py`              | Prometheus metric definitions                                          |
| Logging            | `core/logging_config.py`       | Structlog configuration                                                |
| Redis Client       | `core/redis_client.py`         | Shared Redis connection + chaos keys                                   |
| Queue              | `core/queue.py`                | Redis queue helpers used by API and worker                             |
| Path Validation    | `core/utils/security.py`       | Canonical filesystem containment and writability checks                |
| Dependencies       | `app/api/dependencies/`        | FastDI: DbSession, CurrentUser, CurrentUserFromCookie                  |
| Middleware Package | `app/api/middleware/`          | Prometheus metrics, request body limits, request IDs, security headers |
| API Docs           | `app/api/docs.py`              | Custom Swagger/ReDoc static asset mounting and CSP-aware docs routes   |
| Exceptions         | `app/api/exceptions.py`        | Global standardized exception handler registration                     |
| Startup            | `app/api/startup.py`           | Sentry, template/static checks, worker polling, lifespan cleanup       |
| Rate Limit         | `app/api/rate_limit_config.py` | SlowAPI rate limiter configuration                                     |
