# Vooglaadija — Harsh Architecture & Implementation Critique

## The Architecture Is a Lie

The docs claim two processes — "API" and "Worker" — but there's no boundary between them. The worker imports everything from `app/`: models, config, database, services, logging, even schemas and API-specific code. It's not a separate service; it's the same monolith with a second entry point. If you ever wanted to scale the worker independently, you'd be shipping the entire FastAPI web server into every worker container. The project directory `worker/` is a polite fiction.

```text
worker/main.py       → app.config, app.database, app.logging_config, app.metrics, app.models
worker/processor.py  → app.config, app.database, app.logging_config, app.metrics,
                       app.models, app.services.{circuit_breaker,error_classifier,
                       pubsub_service,throttle_predictor,yt_dlp_service}
worker/queue.py      → app.services.redis_client
worker/health.py     → app.logging_config, app.services.redis_client, app.config
worker/zombie_sweeper.py → app.database, app.logging_config, app.models, app.services.redis_client
```

**`app/services/job_factory.py` imports from `worker/queue.py`.** That's backwards. The dependency arrow should never point from the API layer into the worker. This is a circular dependency waiting to happen — it only works because `worker/queue.py` doesn't import from `app/services/`, but the minute someone adds that import, everything breaks.

**Fix**: Extract a shared `core/` package with `config`, `database`, `models`, `metrics`, and `redis_client`. The worker should not depend on `app.api`, `app.schemas`, or `app.services` that are API-specific.

---

## Three God Modules That Do Everything

### `app/api/routes/web.py` — 1,131 lines

This single file handles: login, registration, CSRF generation/validation/rotation, demo login, logout, chaos lab page + polling, presentation slides, dashboard, settings (username, password, account deletion), download CRUD (HTMX + full-page forms), file download, error resolution (5 separate error mappers with 40+ lines of mapping data), HTMX detection, redirect validation, and template context building. It's a controller, view layer, form handler, and business logic module all merged into one file.

The REST API counterpart (`downloads.py`) implements identical operations with **completely separate code** — zero shared logic. A bug fix in one must be manually replicated in the other.

### `worker/processor.py` — 811 lines

This module does ALL of:

| Responsibility | Approx. Lines |
|---|---|
| Job claiming (DB UPDATE WHERE status=pending) | ~40 |
| Chaos injection checking | ~50 |
| Circuit breaker interaction + deferred queue management | ~25 + helpers |
| Format failure caching in Redis | ~30 |
| DLQ management (move to failed_jobs table) | ~30 |
| Pub/sub publishing for status + progress | ~30 |
| Progress callback setup | ~30 |
| Per-attempt timeout escalation | ~10 |
| File cleanup | ~15 |
| Requeue logic (outbox + DB update + Redis ZADD) | ~25 |
| Heartbeat | ~5 |
| Error classification + per-category retry decisions | ~100 |
| `sync_outbox_to_queue` | ~50 |
| `cleanup_stale_outbox_entries` | ~20 |
| `reset_stuck_jobs` | ~40 |
| `_drain_circuit_deferred` | ~40 |

The function `process_next_job` has deeply nested try/except blocks spanning hundreds of lines where DB updates, Redis ops, pubsub publishing, chaos checks, error handling, and retry scheduling are all intertwined.

**Fix**: Split into: `job_claimer.py`, `job_executor.py`, `retry_scheduler.py`, `dlq_manager.py`, `outbox_relay.py`.

### `app/main.py` — 616 lines

The lifespan function alone does: template file verification, signal handler installation, worker health polling (async task with exponential backoff), pubsub cleanup, metrics initialization, Sentry initialization. Five distinct startup concerns crammed into one async context manager.

Also defines three middleware classes inline, custom `/docs`/`/redoc` routes with CSP headers, three exception handlers, security headers middleware, and CORS configuration — all in `main.py` rather than in separate modules.

---

## Dead Code and Duplication Rot

### Dead code with no callers

- **`app/services/retry_service.py`** — 40 lines of jitter calculation. **Never imported anywhere.** The worker uses `error_classifier.py`'s `calculate_delay` which has its own duplicate jitter implementation (DECORRELATED, FULL, NONE).

- **`app/utils/exceptions.py`** — `YTDLPError` is **never raised or caught**. `StorageError` is defined here and **redefined** in `yt_dlp_service.py`.

- **`src/` directory** — mirrors the entire project structure (`app/api/`, `app/models/`, `app/schemas/`, `worker/`, etc.) but contains only `__pycache__` directories with no `.py` files. A failed `src/`-layout migration left as garbage.

- **Template partials `_error.html` and `_status_badge.html`** are dead code. Error HTML is built in Python via `_error_html()` in `web.py`. Status badges are duplicated inline across `_download_list.html`, `_download_item.html`, AND the unused partial.

### Duplication

- **Password strength evaluator** duplicated — identical logic in `register.html` (inline `<script>`) and `settings.js`. Different variable declarations (`var` vs `const`).

- **Three path validation functions** at different layers: `validate_file_path`, `validate_path_within`, `_validate_path_within`. Nearly identical logic. The worker's `cleanup_expired_jobs` duplicates the same `os.path.realpath + startswith` inline instead of using any of them.

- **Status badge HTML** duplicated in three places (two templates + JavaScript `getRowHTML()`).

- **Two orthogonal token revocation strategies** with different semantics: JTI blacklist (per-token) and token version (per-user). Both checked on every authenticated request. No documentation explains why both exist or when each should be used.

- **Two Redis connection pools**: the main singleton in `redis_client.py` plus `pubsub_service.py` creating its own independent client with `max_connections=20`. Same Redis instance, two pools, undocumented.

- **`worker/queue.py`** is a 52-line thin proxy over `app/services/redis_client.py`. It adds exactly one function (`enqueue_job`) plus metrics. That's not a worker queue module — it's an indirection layer that hides the fact the worker has no independent Redis connection.

- **`worker/health.py`** duplicates Redis URL construction from `app/config.py`. Two different fallback chains that could silently diverge.

- **REST API (`downloads.py`) and web UI (`web.py`)** implement nearly identical operations (create download, delete download, get file) with completely separate code. No shared service, no common validation.

---

## Data Layer Issues

### N+1 Query

`replay_all_failed_jobs` in `downloads.py` does N+1 queries. For every failed job, it re-queries `DownloadJob` to find the original:

```python
for failed_job in failed_jobs:
    if failed_job.original_job_id:
        result = await db.execute(
            select(DownloadJob).where(
                DownloadJob.id == failed_job.original_job_id,
                DownloadJob.user_id == current_user.id,
            )
        )
```

**Fix**: Batch-load with a single `WHERE id IN (...)` query.

### Triple round-trip for job claim

The worker's claim loop performs three round-trips for one row:

1. `UPDATE` to claim → 2. `SELECT` to re-fetch → 3. `UPDATE` to heartbeat → 4. `SELECT` to re-fetch again

PostgreSQL supports `UPDATE ... RETURNING`. Use it to eliminate two round-trips.

### Missing composite indexes

| Missing Index | Query Pattern |
|---|---|
| `download_jobs(user_id, status)` | Worker claim: `WHERE user_id=X AND status='pending'` |
| `download_jobs(user_id, created_at DESC)` | Paginated listing with ORDER BY |
| `download_jobs(status, updated_at)` | Zombie sweeper + `reset_stuck_jobs` |
| `failed_jobs(user_id, failed_at DESC)` | DLQ listing with ORDER BY |

The zombie sweeper query scans ALL rows with `status='processing'` then filters `updated_at` in memory because there's no `(status, updated_at)` composite index.

Also redundant: `ix_users_email` (non-unique) exists alongside `ix_users_email_active` (partial unique, same column). The non-unique one is wasted.

### No ORM relationships

Every model has `ForeignKey` at the column level but zero `relationship()` definitions. This prevents accidental N+1 (good) but also prevents intentional eager loading where it would help. All cross-table queries are manual.

### Unconstrained status strings

```python
status: Mapped[str] = mapped_column(String(20), default="pending")
```

No `CHECK` constraint or PostgreSQL `ENUM`. A bug could write `"pening"` or `"unknown"` and silently break all status-filtering logic. Same issue in `outbox.status`.

**Recommendation**:
```sql
ALTER TABLE download_jobs ADD CONSTRAINT ck_download_jobs_status
CHECK (status IN ('pending', 'processing', 'completed', 'failed', 'deferred'));
```

### No FK on `outbox.job_id`

Orphaned outbox entries can exist indefinitely with no DB-level protection. `cleanup_stale_outbox_entries` is the only defense (24-hour TTL sweep).

### Error history as unstructured concatenated text

```python
accumulated_error = f"{previous} → {formatted_error}"
```

Errors are concatenated with `→` delimiters into a single `Text` column. Impossible to query, impossible to aggregate, unbounded growth. The DLQ already captures structured fields; the live job table should store only the last error.

### Migration/model index inconsistency

`failed_job.py` declares `index=True` on `original_job_id`, but the migration creates indexes on `user_id`, `error_category`, and `expires_at` with no corresponding `index=True` in the model. These indexes will be lost if migrations are ever regenerated from models.

### Hardcoded DB pool configuration

```python
self._engine = create_async_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=5,
    pool_timeout=30,
    pool_recycle=1800,
    pool_pre_ping=True,
)
```

No environment variable overrides. Production tuning requires code changes. The worker and API server share the same `database.py` module meaning both use identical pool configuration even though the worker (single process, sequential) needs fewer connections than the API server (multiple concurrent requests).

---

## Frontend: Solid Design, Significant Gaps

### What's good

- Dark theme with consistent Tailwind custom design system
- CSP with per-request UUID nonces
- CSRF protection is thorough (double-submit cookie, rotation on state changes)
- Focus trap in confirm modal correctly implemented
- `prefers-reduced-motion` respected in both CSS and JS
- `htmx.config.allowEval = false` and `allowScriptTags = false`
- Debounced URL validation, relative timestamps

### Accessibility

- **The download list has no `aria-live` region.** Screen readers won't announce new downloads or status changes from SSE. This is the primary use case of the dashboard and it's silent to assistive tech.

- **Color contrast**: `text-gray-500` (#6b7280) on surface-900 (#0d0f14) has ~5.1:1 contrast — borderline AA.

### Performance

- **Potential memory leak in SSE health monitoring.** `dashboard.js` runs a `setInterval` (5s) that creates another `setInterval` (3s) inside it. If the outer interval fires before the inner one clears, multiple nested intervals accumulate. Inner intervals captured in local `const check` variables that aren't independently clearable.

- **`document.body` listener re-enables wrong submit button.** Any HTMX request anywhere on the page re-enables the download form's submit button — not just requests from that specific form.

- **Skeleton loader is time-based, not data-driven.** Vanishes after 5 seconds regardless of whether data has arrived. If SSE is slow, users see an empty list with no loading indicator.

- **SVG icon sprite inlined in every page.** ~90 lines of uncompressed, uncacheable SVG in `base.html` served on every request. External file with cache headers would be trivially better.

- **Google Fonts CSS blocks first paint.** No `media="print" onload="this.media='all'"` pattern. The font CSS is render-blocking on every page.

- **SSE extension loaded on all pages** including login/register where it's never used (~11KB wasted).

- **Duplicate row detection walks entire DOM** on every `htmx:afterOnLoad` — O(n) on every HTMX swap.

### JavaScript anti-patterns

- **Global namespace pollution** — independent IIFEs all export to `window.*` with no unified namespace
- **Variable reassignment for monkey-patching** (`handleJobUpdate` wrapped via reassignment — brittle)
- **Missing preconnect for `fonts.gstatic.com`** (only `fonts.googleapis.com` is preconnected)

### Missing states

- **No error state for failed SSE connection** in the list area — shows whatever was last rendered
- **No loading state for settings username save** — no HTMX indicator or disabled state
- **No error state for chaos lab polling failure** — infinite spinner on 404
- **Success message after password change has no auto-dismiss**

---

## Security: Good Intentions, Sloppy Execution

### Critical

- **`.env` is committed to git** with live secrets including `SECRET_KEY=4a23961d...`, `DB_PASSWORD=localdevpass`, `NETDATA_CLAIM_TOKEN=ESiYkYdz...`. These need immediate rotation.

- **Grafana admin password `admin/admin` hardcoded** in `docker-compose.demo.yml`.

- **CodeQL is disabled** — `codeql.yml` is `workflow_dispatch` only with scheduled triggers commented out.

- **Security scans are non-blocking** — Bandit and Safety run with `|| true`. Vulnerabilities never prevent merging.

- **Secrets passed via SSH environment variables** in `remote-deploy.sh` — visible in `/proc/*/environ` and potentially shell history.

### Moderate

- **`deploy.sh` builds Docker images on production VPS** — needs all build deps installed, increases attack surface.
- **Rollback doesn't verify images exist** — if tags have been garbage-collected from GHCR, rollback fails silently.
- **Domain `youtube.tomabel.ee` hardcoded** in 5+ files including CI workflows and nginx configs.
- **Safety policy suppresses CVEs** with no expiration date or justification comment.

---

## Observability: Wired to /dev/null

- **OTel Collector exports only to `debug` (stdout).** The OTLP exporter is commented out. All traces and metrics go nowhere.

- **Production deployment disables observability** — `FEATURE_METRICS_ENABLED=false`, `FEATURE_TRACING_ENABLED=false`. The otel-collector and swagger-ui services are disabled via profiles. You're flying blind in production by design.

- **No alerting rules** — Prometheus `rule_files: []`.

- **No centralized log aggregation** — all services use local `json-file` driver with rotation only.

- **Worker health port 8082 not exposed** — no `EXPOSE 8082` in Dockerfile. Production compose overrides healthcheck to a raw socket connect that only verifies the port is open, not that the app is healthy.

- **The worker health server is stdlib `http.server.HTTPServer`** while the API uses uvicorn/FastAPI. Two different HTTP servers, two different metrics formats, two different health check protocols — in the same application process.

---

## Configuration & Deployment: Entropy Wins

- **`deploy.sh` generates `.env` with `FEATURE_METRICS_ENABLED=false`**, but `docker-compose.yml` defaults to `true`. Inconsistent defaults across three `.env` generation paths.

- **`environment` field in `config.py` defaults to `"development"`** — could accidentally run prod as dev.

- **No validation that `CORS_ORIGINS` is well-formed**, that port numbers are valid integers, or that `STORAGE_PATH` is writable at config time.

- **`worker/health.py`** has an overly complex Redis URL fallback chain (4 different approaches); misconfiguration could silently fall through to `redis://localhost:6379`.

- **Resource limits missing** for nginx, swagger-ui, and otel-collector.
- **Worker has no explicit resource override** — shares 1G limit with API, which may be insufficient for yt-dlp processing.
- **Multi-arch builds** (amd64 + arm64) with SBOM and provenance attestation — **good**.

---

## Positive Patterns (to keep)

1. CSRF protection is thorough — double-submit cookie pattern, rotation after state changes
2. CSP with nonces — well-implemented, generated per-request UUID nonce
3. Non-root Docker user (1000:1000) with `cap_drop: [ALL]`, `read_only: true`, `no-new-privileges=true`
4. Comprehensive `.dockerignore` (128 lines)
5. Pinned Swagger UI version with SRI integrity comment
6. Immutable Docker image tags (commit SHA + version)
7. Migration locking via Redis distributed lock
8. Focus trap in custom confirm modal with tab cycling and Escape handling
9. Reduced motion support in both CSS and JS
10. `htmx.config.allowEval = false` and `allowScriptTags = false`
11. Debounced URL validation (300ms)
12. Redis `maxmemory` with `allkeys-lru` eviction policy
13. PostgreSQL `--data-checksums`
14. Docker Compose test workflow is exceptionally thorough (10 steps, artifact collection)
15. Multi-arch builds with provenance attestation

---

## Severity Summary

| # | Severity | Flaw | Category |
|---|----------|------|----------|
| 1 | 🔴 Critical | No boundary between app/ and worker/ | Missing boundary |
| 2 | 🔴 Critical | `app/services/` imports `worker/queue.py` | Reverse dependency |
| 3 | 🔴 Critical | Dual Redis connection pools | Hidden duplication |
| 4 | 🔴 Critical | `.env` committed with live secrets | Security |
| 5 | 🔴 Critical | Grafana admin/admin hardcoded | Security |
| 6 | 🔴 Critical | CodeQL disabled | Security |
| 7 | 🔴 Critical | OTel collector exports to nowhere | Observability |
| 8 | 🟠 Major | `worker/processor.py` — 811-line god module | God module |
| 9 | 🟠 Major | `app/api/routes/web.py` — 1131-line god module | God module |
| 10 | 🟠 Major | `app/main.py` — 616-line god module | God module |
| 11 | 🟠 Major | Dead `retry_service.py` with duplicate jitter logic | Dead code |
| 12 | 🟠 Major | Missing composite indexes | Performance |
| 13 | 🟠 Major | N+1 in `replay_all_failed_jobs` | Performance |
| 14 | 🟠 Major | REST and web routes share zero common code | Duplication |
| 15 | 🟠 Major | Missing `aria-live` on SSE download list | Accessibility |
| 16 | 🟡 Moderate | Potential JS memory leak in SSE health monitor | Bug risk |
| 17 | 🟡 Moderate | Dead `src/` directory, template partials, unused exception classes | Dead code |
| 18 | 🟡 Moderate | Unconstrained status strings in DB | Data integrity |
| 19 | 🟡 Moderate | No FK on `outbox.job_id` | Data integrity |
| 20 | 🟡 Moderate | Error history as concatenated text | Data design |
| 21 | 🟡 Moderate | Triple round-trip for job claim | Performance |
| 22 | 🟡 Moderate | Production observability disabled | Observability |
| 23 | 🟡 Moderate | Worker health port not exposed | Deployment |
| 24 | 🟡 Moderate | Hardcoded domain/IP in 5+ files | Config management |
| 25 | 🟡 Moderate | Password strength evaluator duplicated | Duplication |

---

## Top 5 Recommended Fixes

1. **Rotate all secrets** and add `.env` to `.gitignore`. Re-enable CodeQL on push/schedule.

2. **Extract `core/` package**: Move `config`, `database`, `models`, `metrics`, and `redis_client` into a shared `core/` that both `app/` and `worker/` import. Worker should not depend on `app.api` or `app.schemas`.

3. **Split `worker/processor.py`**: Separate into `job_claimer.py`, `retry_scheduler.py`, `dlq_manager.py`, `outbox_relay.py`, with `processor.py` orchestrating them. Use `UPDATE ... RETURNING` to eliminate redundant round-trips.

4. **Create domain services**: Move business logic from route handlers into `app/services/download_service.py` and `app/services/user_service.py` that both the REST API and web UI call. Eliminates the REST/web code duplication.

5. **Add missing database constraints**: Composite indexes for the claim and zombie sweeper queries, `CHECK` constraints on status columns, and a `ForeignKey` on `outbox.job_id`.
