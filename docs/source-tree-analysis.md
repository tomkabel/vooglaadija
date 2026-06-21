# Source Tree Analysis

**Project:** Vooglaadija — Media Link Processor **Repository Type:** Monorepo (4 parts)

---

## Repository Structure

```text
vooglaadija/
├── app/                          # Part: API Server (backend)
│   ├── main.py                   # Entry point — FastAPI application
│   ├── auth.py                   # JWT token creation/verification
│   ├── api/
│   │   ├── routes/               # HTTP route handlers
│   │   │   ├── auth.py           # Auth endpoints (register, login, refresh, me, logout)
│   │   │   ├── downloads.py      # Download job CRUD (REST API)
│   │   │   ├── web/              # HTMX/browser route package
│   │   │   │   ├── __init__.py   # Aggregate /web router and compatibility re-exports
│   │   │   │   ├── web_auth.py   # Login, register, demo login, logout, password
│   │   │   │   ├── web_auth_helpers.py # Auth-specific helper functions
│   │   │   │   ├── web_dashboard.py # Dashboard, chaos lab, slides
│   │   │   │   ├── web_downloads.py # Web download CRUD, SSE page data, file serving
│   │   │   │   ├── web_helpers.py # Package-local context/HTMX/file helpers
│   │   │   │   └── web_settings.py # Username and account settings
│   │   │   ├── web_helpers.py    # Shared HTML fragments and status badge helpers
│   │   │   ├── sse.py            # Server-Sent Events for real-time updates
│   │   │   ├── health.py         # Health check endpoints
│   │   │   ├── metrics.py        # Prometheus metrics endpoint
│   │   │   └── chaos.py          # Chaos engineering API (feature-gated)
│   │   ├── dependencies/         # FastAPI dependency injection
│   │   │   └── __init__.py       # DbSession, CurrentUser, get_current_user, etc.
│   │   ├── middleware/           # Request middleware package
│   │   │   ├── __init__.py       # Middleware re-exports
│   │   │   ├── prometheus.py     # Prometheus request metrics middleware
│   │   │   ├── request_body_size.py # Request body size limiting
│   │   │   ├── request_id.py     # Request ID context/header middleware
│   │   │   └── security_headers.py # CSP nonce and security headers
│   │   ├── docs.py               # Custom Swagger/ReDoc static docs routes
│   │   ├── exceptions.py         # Global exception handler registration
│   │   ├── startup.py            # Sentry, startup checks, lifespan cleanup
│   │   └── rate_limit_config.py  # SlowAPI rate limiter configuration
│   ├── schemas/                  # Pydantic V2 request/response schemas
│   │   └── error.py              # Standardized error response format
│   ├── services/                 # Business logic layer
│   │   ├── auth_service.py           # Password hashing/verification
│   │   ├── circuit_breaker.py        # Circuit breaker (YouTube protection)
│   │   ├── error_classifier.py       # Error classification + retry delay policy
│   │   ├── job_factory.py            # Demo job creation
│   │   ├── outbox_service.py         # Transactional outbox writer
│   │   ├── pubsub_service.py         # Redis Pub/Sub for real-time events
│   │   ├── throttle_predictor.py     # Rate-limit tracking via Redis
│   │   └── yt_dlp_service.py         # YouTube media extraction (yt-dlp subprocess)
│   ├── static/                  # Static assets (CSS, JS, images)
│   │   └── css/
│   │       └── styles.css       # Built Tailwind CSS output
│   ├── templates/               # Jinja2 server-rendered templates
│   └── utils/                   # Utility modules
│
├── core/                        # Shared infrastructure for API and worker
│   ├── config.py                # Pydantic Settings (env-based configuration)
│   ├── database.py              # Async SQLAlchemy engine + session factory
│   ├── logging_config.py        # Structlog configuration
│   ├── metrics.py               # Prometheus metric definitions
│   ├── queue.py                 # Redis queue helpers with deduplication
│   ├── redis_client.py          # Shared Redis client + chaos keys
│   ├── models/                  # SQLAlchemy ORM models
│   │   ├── user.py              # User model
│   │   ├── download_job.py      # DownloadJob model
│   │   ├── failed_job.py        # FailedJob (DLQ) model
│   │   └── outbox.py            # Outbox (transactional outbox) model
│   └── utils/
│       └── security.py          # Canonical path validation
│
├── worker/                      # Part: Worker (backend — background processor)
│   ├── main.py                  # Entry point — event loop, signal handling, orchestration
│   ├── processor.py             # Thin job-processing orchestrator
│   ├── job_claimer.py           # Queue pop normalization, DB claim, heartbeat helpers
│   ├── job_executor.py          # yt-dlp execution, progress, completion, file cleanup
│   ├── retry_scheduler.py       # Error classification, retry decisions, retry outbox writes
│   ├── dlq_manager.py           # DLQ movement, depth metrics, replay/reset helpers
│   ├── outbox_relay.py          # Pending outbox sync and stale terminal cleanup
│   ├── health.py                # Internal health HTTP server + Redis heartbeat
│   ├── state.py                 # Shared shutdown_event (lazy asyncio.Event)
│   └── zombie_sweeper.py        # Reclaims stuck "processing" jobs
│
├── frontend/                    # Part: Frontend (web — Tailwind CSS)
│   ├── css/
│   │   ├── src/
│   │   │   └── styles.css       # Tailwind source with custom utilities
│   │   └── dist/
│   │       └── styles.css       # Built output
│   ├── package.json             # Frontend dependency manifest
│   ├── tailwind.config.js       # Custom design system (colors, fonts, animations)
│   └── postcss.config.js        # PostCSS configuration
│
├── infra/                       # Part: Infrastructure
│   ├── nginx/                   # Nginx configs (reverse proxy, SSL)
│   ├── prometheus/              # Prometheus scrape configs
│   ├── grafana/                 # Grafana dashboard definitions
│   ├── netdata/                 # NetData monitoring configs
│   ├── redis/                   # Redis configuration
│   ├── ssl/                     # SSL/TLS certificates
│   ├── letsencrypt/             # Let's Encrypt auto-renewal
│   ├── certbot/                 # Certbot configuration
│   ├── otel-collector-config.yml # OpenTelemetry collector config
│   ├── deploy/                  # Deployment scripts
│   └── backup/                  # Backup scripts
│
├── alembic/                     # Database migrations
│   └── versions/                # Migration scripts (001-004)
│
├── tests/                       # Test suite
│   ├── test_services/           # Unit tests for services
│   ├── test_routes/             # Unit tests for routes
│   ├── test_api/                # Integration tests
│   └── conftest.py              # Shared fixtures (SQLite, test user helper)
│
├── docs/                        # Project documentation
├── scripts/                     # Utility scripts
├── storage/                     # Downloaded file storage
├── monitoring/                  # Monitoring configs
├── docker-compose*.yml          # Docker Compose (local, demo, production, test, monitoring)
├── Dockerfile                   # Multi-stage build
├── pyproject.toml               # Python project config (hatch, ruff, mypy, pytest)
└── package.json                 # Root JS config (pnpm workspace, linting)
```

## Architecture Patterns

- **Backend (API + Worker):** Layered architecture — Routes → Services → `core` Models/DB
- **Worker:** Event-driven async loop — BRPOP from Redis → Process → Classify → Retry/DLQ
- **Frontend:** Server-rendered HTML with HTMX for dynamic updates, SSE for real-time
- **Real-time:** Redis Pub/Sub — Worker publishes → API subscribes → SSE streams to browser
- **Resilience:** Circuit breaker, retry with jitter, error classification, transactional outbox

## Integration Points

| From         | To         | Type             | Details                                          |
| ------------ | ---------- | ---------------- | ------------------------------------------------ |
| API Server   | Worker     | Redis List       | Outbox → sync → `download_queue` (BRPOP)         |
| Worker       | API Server | Redis Pub/Sub    | `job_status:{user_id}`, `job_progress:{user_id}` |
| API + Worker | PostgreSQL | Async SQLAlchemy | Shared models, connection pool                   |
| API + Worker | Redis      | aioredis         | Queue, Pub/Sub, blacklist, health, chaos         |
| Worker       | YouTube    | Subprocess       | yt-dlp via subprocess with SSRF protection       |
| User Browser | API        | SSE (HTTP)       | Real-time job status via `/web/downloads/stream` |
