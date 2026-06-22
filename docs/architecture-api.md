# Architecture — API Server

**Part:** API Server (`app/`) **Type:** Async Python backend (FastAPI)

---

## Executive Summary

The API Server is a FastAPI-based REST service that handles user authentication, download job
management, and serves the HTMX frontend. It runs on Uvicorn (port 8000), connects to PostgreSQL via
async SQLAlchemy, uses Redis for caching and Pub/Sub, and exposes Prometheus metrics.

## Technology Stack

| Category      | Technology                      | Version          | Purpose                                     |
| ------------- | ------------------------------- | ---------------- | ------------------------------------------- |
| Framework     | FastAPI + Uvicorn               | 0.135.2 / 0.42.0 | HTTP API server                             |
| Database      | SQLAlchemy 2.0 + asyncpg        | 2.0.48 / 0.31.0  | Async ORM + PostgreSQL driver               |
| Database      | PostgreSQL 15                   | alpine           | Primary data store                          |
| Cache/Queue   | Redis 7 + aioredis              | 7-alpine         | Queue, Pub/Sub, token blacklist             |
| Auth          | python-jose + passlib + bcrypt  | —                | JWT + bcrypt password hashing               |
| Real-time     | SSE-Starlette                   | 3.3.4            | Server-Sent Events                          |
| Templates     | Jinja2 + HTMX                   | 3.1.6 / 1.9.12   | Server-rendered HTML with dynamic updates   |
| Observability | structlog + Prometheus + Sentry | —                | Structured logging, metrics, error tracking |

## Architecture Pattern

Layered architecture with clear separation:

- **Routes** (`app/api/routes/`) — HTTP concerns only (parameter parsing, response formatting)
- **Services** (`app/services/`) — Business logic
- **Core infrastructure** (`core/`) — shared config, database, models, metrics, Redis, queue,
  logging, and utilities
- **Models** (`core/models/`) — SQLAlchemy ORM entities shared by API and worker
- **Schemas** (`app/schemas/`) — Pydantic V2 request/response models

## Data Architecture

4 SQLAlchemy models in `core/models/`: `User`, `DownloadJob`, `FailedJob` (DLQ), `Outbox`
(transactional outbox). 4 Alembic migrations (001-004). Async sessions with connection pooling
(pool_size=10, max_overflow=5).

## API Design

- All REST endpoints prefixed with `/api/v1/`
- Auth: JWT access tokens (15min) + refresh tokens (7d) with bcrypt password hashing
- Rate limiting via SlowAPI (configurable per-route)
- Error responses use standardized format: `{"error": {"code": "...", "message": "..."}}`
- See [API Contracts — API Server](./api-contracts-api.md) for full endpoint reference

## Services Layer

| Service                 | Responsibility                                                   |
| ----------------------- | ---------------------------------------------------------------- |
| `auth_service.py`       | Password hashing/verification via bcrypt                         |
| `circuit_breaker.py`    | YouTube API circuit breaker (CLOSED → OPEN → HALF_OPEN)          |
| `error_classifier.py`   | Error classification, retry policy, and jitter delay calculation |
| `job_factory.py`        | Demo job creation for testing                                    |
| `outbox_service.py`     | Atomic outbox insertion (crash-safe queue writes)                |
| `pubsub_service.py`     | Redis Pub/Sub for real-time SSE events                           |
| `throttle_predictor.py` | Rate-limit risk tracking via Redis sorted sets                   |
| `yt_dlp_service.py`     | YouTube media extraction via yt-dlp subprocess                   |

Shared infrastructure such as Redis connectivity, queue helpers, config, database sessions, logging,
metrics, ORM models, and canonical path validation lives in `core/`.

## Security

- JWT-based auth with token blacklisting (Redis) and versioning
- bcrypt password hashing with configurable rounds
- Rate limiting on auth and download endpoints
- SSRF protection on yt-dlp URL processing
- CSRF tokens on state-changing HTMX routes
- CSP headers with nonce on every response
- Path traversal protection on file download routes

## Development Workflow

- **Run:** `hatch run dev` (uvicorn with hot reload)
- **Test:** `hatch run test:unit` (SQLite), `hatch run test:integration` (Postgres+Redis in CI)
- **Migrations:** `hatch run db-migrate`
- **DB Reset:** Individual migration rollback (no single command)
