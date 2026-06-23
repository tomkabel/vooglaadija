# Integration Architecture

**Project:** Vooglaadija — Media Link Processor **Repository Type:** Monorepo (4 parts)

---

## Part Overview

| Part           | Type    | Root        | Primary Tech           | Port            |
| -------------- | ------- | ----------- | ---------------------- | --------------- |
| API Server     | backend | `app/`      | FastAPI + Uvicorn      | 8000            |
| Worker         | backend | `worker/`   | Python 3.12 async      | 8082 (health)   |
| Frontend       | web     | `frontend/` | Tailwind CSS + HTMX    | (served by API) |
| Infrastructure | infra   | `infra/`    | Docker Compose + Nginx | 80/443          |

## Integration Points

### API Server → Worker (Redis Queue)

```text
API Server                 Worker
    │                         │
    ├── POST /api/v1/downloads
    │   ├── INSERT DownloadJob (DB) ──────────────────┐
    │   └── INSERT Outbox (DB)    ──────────────────┐ │
    │                                                │ │
    │   Outbox Sync (every 30s)                     ▼ ▼
    │   sync_outbox_to_queue() ──→ Redis download_queue
    │                                                │
    │                                           BRPOP ◄──
    │                                           process_next_job()
```

The `DownloadJob` and `Outbox` entry are written atomically in the same DB transaction. The outbox
sync loop pushes pending entries to Redis, providing crash-safety for queue writes.

### Worker → API Server (Redis Pub/Sub)

```text
Worker                              API Server
    │                                    │
    ├── Job completed/failed        ──→ Redis Pub/Sub
    │   publish_job_status()            │
    │   publish_job_progress()          │
    │                                   │
    │                              subscribe(user_id)
    │                              SSE stream to browser
    │                              /web/downloads/stream
```

Two channels per user: `job_status:{user_id}` (status transitions) and `job_progress:{user_id}`
(download progress).

### Shared Database (PostgreSQL)

Both API Server and Worker use the same PostgreSQL database through `core.database`. They share all
4 models (`User`, `DownloadJob`, `FailedJob`, `Outbox`) from `core.models`. Shared infrastructure
flows through `core/` so `app/` and `worker/` do not depend on each other for database ownership.

### Shared Redis

| Key Pattern                 | Purpose                 | Written By       | Read By      |
| --------------------------- | ----------------------- | ---------------- | ------------ |
| `download_queue`            | Primary job queue       | API (via Outbox) | Worker       |
| `retry_queue`               | Delayed retry queue     | Worker           | Worker       |
| `circuit_deferred_queue`    | Circuit-deferred jobs   | Worker           | Worker       |
| `job_status:{user_id}`      | Status Pub/Sub          | Worker           | API (SSE)    |
| `job_progress:{user_id}`    | Progress Pub/Sub        | Worker           | API (SSE)    |
| `worker:health:{worker_id}` | Worker heartbeat        | Worker           | (external)   |
| `cb:*`                      | Circuit breaker state   | Worker           | Worker       |
| `token:blacklist:*`         | JWT blacklist           | API              | API          |
| `chaos:*`                   | Chaos engineering flags | API              | API + Worker |

### Worker → YouTube (Subprocess)

```text
Worker
    │
    ├── extract_media_with_circuit_breaker(url)
    │   ├── yt-dlp subprocess (SSRF-protected)
    │   │   ├── resolve_video_title()    (15s timeout)
    │   │   └── extract_media_url()      (300s+ timeout)
    │   └── FFmpeg orphan cleanup
```

SSRF protection via DNS validation. Format fallback chain (5 formats). Semaphore-limited to 5
concurrent extractions.

### API → Database (Async SQLAlchemy)

```text
API FastAPI route → Depends(DbSession) → service → model
                                              │
                                         AsyncSession
                                              │
                                     asyncpg → PostgreSQL
```

Each route gets a session via the `get_db()` dependency. Sessions are yielded, committed on success,
rolled back on exception.

### API → Redis (aioredis)

```text
API route → core.redis_client.get_redis_client()
    ├── Token blacklist (token_blacklist.py)
    ├── Pub/Sub (pubsub_service.py)
    └── Chaos flags (redis_client.py)
```

Redis connectivity is centralized in `core.redis_client`. Pub/Sub creates subscriptions from the
shared Redis client instead of maintaining a separate application pool.

## Shared Dependencies

- **Python packages:** All dependencies managed via `pyproject.toml` and Hatch. The Worker and API
  share the same package set.
- **Core infrastructure:** `core/` — shared config, database, models, metrics, Redis client, queue,
  logging, and utilities.
- **Models:** `core/models/*.py` — imported by both API and Worker.
- **Services:** `app/services/*.py` — API-owned services that may also be used by the Worker when
  they are API-independent (`circuit_breaker`, `error_classifier`, `outbox_service`,
  `pubsub_service`, `throttle_predictor`, `yt_dlp_service`).
- **Config:** `core/config.py` — `Settings` class shared by both processes.
- **Database:** `core/database.py` — Engine and session factory.

## Deployment Architecture

```text
                           ┌──────────────┐
                           │    Nginx      │ (443/80)
                           │  SSL term     │
                           │  Static files │
                           └──────┬───────┘
                                  │
                          ┌───────┴────────┐
                          │   API Server    │
                          │   Uvicorn:8000  │
                          └───────┬────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │                   │                   │
        ┌─────┴─────┐      ┌─────┴─────┐      ┌──────┴──────┐
        │ PostgreSQL │      │  Redis 7  │      │   Worker    │
        │     :5432  │      │   :6379   │      │  :8082/hlth │
        └───────────┘      └───────────┘      └──────┬──────┘
                                                      │
                                              ┌───────┴───────┐
                                              │  yt-dlp + FFmpeg│
                                              │  (subprocess)   │
                                              └───────────────┘
```
