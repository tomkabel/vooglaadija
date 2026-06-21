# API Contracts — API Server

**Part:** API Server (`app/`)
**Base URL:** All REST endpoints are served under `/api/v1/` prefix.
**Auth:** JWT Bearer tokens (access + refresh), with cookie-based fallback for browser routes.
**Format:** JSON request/response bodies.

---

## Authentication Endpoints

| Method | Path | Auth | Rate Limit | Description |
|--------|------|------|------------|-------------|
| `POST` | `/api/v1/auth/register` | None | 5/min | Create account (email + password) |
| `POST` | `/api/v1/auth/login` | None | 5/min | Authenticate, return JWT tokens + set cookies |
| `POST` | `/api/v1/auth/refresh` | None | 5/min | Exchange refresh token for new token pair (body or cookie) |
| `GET` | `/api/v1/auth/me` | Bearer | — | Return authenticated user profile |
| `POST` | `/api/v1/auth/logout` | None | — | Blacklist tokens, clear cookies, redirect to login |

**Error format:** `{"error": {"code": "ERROR_CODE", "message": "..."}}` with optional `details` field.

---

## Download Job Endpoints (REST API)

| Method | Path | Auth | Rate Limit | Description |
|--------|------|------|------------|-------------|
| `POST` | `/api/v1/downloads` | Bearer | 10/min | Create new download job |
| `GET` | `/api/v1/downloads` | Bearer | — | List paginated jobs for user |
| `GET` | `/api/v1/downloads/{job_id}` | Bearer | — | Get single job by ID |
| `GET` | `/api/v1/downloads/{job_id}/file` | Bearer | — | Download completed job's file |
| `POST` | `/api/v1/downloads/{job_id}/retry` | Bearer | 10/min | Retry a failed/deferred job |
| `DELETE` | `/api/v1/downloads/{job_id}` | Bearer | 30/min | Delete job + file from disk |
| `GET` | `/api/v1/downloads/failed` | Bearer | — | List DLQ failed jobs (with optional category filter) |
| `POST` | `/api/v1/downloads/failed/{failed_job_id}/replay` | Bearer | 10/min | Replay single failed job from DLQ |
| `POST` | `/api/v1/downloads/failed/replay-all` | Bearer | 5/min | Replay all failed jobs (batch, capped at 500) |

---

## Web/HTMX Routes (HTML Responses)

These routes render Jinja2 templates with HTMX partial updates (fragment responses).

| Method | Path | Auth | Rate Limit | Description |
|--------|------|------|------------|-------------|
| `GET` | `/web/login` | None | — | Render login page |
| `POST` | `/web/login` | None | 5/min | Handle login form submission |
| `GET` | `/web/register` | None | — | Render registration page |
| `POST` | `/web/register` | None | 5/min | Handle registration form submission |
| `GET` | `/web/demo-login` | None | 3/min | One-click demo login, primes demo jobs |
| `POST` | `/web/logout` | Cookie | — | Clear cookies, redirect to login |
| `GET` | `/web/chaos-lab` | None | — | Render chaos engineering lab (gated by feature flag) |
| `GET` | `/web/chaos-lab/status` | None | — | HTMX partial: current chaos flag status |
| `GET` | `/web/slides` | None | — | Presentation slides |
| `GET` | `/web/downloads` | Cookie | — | Main dashboard with download job list |
| `GET` | `/web/settings` | Cookie | — | User settings page |
| `POST` | `/web/settings/username` | Cookie | 10/min | Update username |
| `POST` | `/web/settings/password` | Cookie | 10/min | Change password (invalidates all sessions) |
| `POST` | `/web/settings/delete-account` | Cookie | 3/min | Delete account + all jobs/files |
| `POST` | `/web/downloads` | Cookie | 10/min | HTMX: create download, returns HTML fragment |
| `POST` | `/web/downloads/full` | Cookie | 10/min | Full-page form submit fallback |
| `DELETE` | `/web/downloads/{job_id}` | Cookie | 30/min | HTMX: delete job, returns empty HTML |
| `GET` | `/web/downloads/{job_id}/file` | Cookie | — | Download file (cookie-based auth variant) |

---

## Server-Sent Events (SSE)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/web/downloads/stream` | Cookie | Real-time SSE stream of job updates via Redis Pub/Sub, with polling fallback (15s). Max 3 reconnection attempts. |

---

## Health & Metrics

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | None | DB + Redis connectivity check |
| `GET` | `/health/ready` | None | K8s readiness probe (503 if dependencies down) |
| `GET` | `/metrics` | Bearer | Prometheus metrics (human access) |
| `GET` | `/prometheus-metrics` | None | Prometheus metrics (container scraping) |

---

## Chaos Engineering API

All chaos endpoints return 404 when `feature_chaos_api_enabled` is false.

| Method | Path | Auth | Rate Limit | Description |
|--------|------|------|------------|-------------|
| `POST` | `/api/v1/chaos/inject` | Cookie | — | Inject chaos scenario (circuit_breaker_open, worker_crash, db_failover, throttle_spike, slow_processing) |
| `POST` | `/api/v1/chaos/reset` | Cookie | — | Reset all chaos scenarios |
| `GET` | `/api/v1/chaos/status` | Cookie | — | Get current chaos flag status |
| `POST` | `/api/v1/chaos/submit-videos` | Cookie | — | Bulk submit demo videos for load testing (capped at 50) |
