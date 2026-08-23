# Migration to Celery Job Queue

Replaces the hand-rolled BRPOP worker with Celery + Redis for durable, observable, horizontally-scalable job processing.

## What Changed

| Legacy (BRPOP) | New (Celery) |
|---|---|
| `worker/main.py` event loop | `celery-worker` service |
| Custom Lua scripts for retry | Celery built-in retry with backoff |
| `retry_queue` Redis sorted set | `retries` Celery queue |
| DLQ via DB table | `dlq` Celery queue + DB table |
| `zombie_sweeper.py` | Celery Beat `requeue-stuck-jobs` |
| Custom outbox relay | Celery Beat `enqueue-pending` |
| Health server on :8082 | Flower dashboard on :5555 |

## Architecture

```
                    ┌─────────────────┐
                    │   FastAPI API   │
                    └────────┬────────┘
                             │ enqueue_job()
                             ▼
                    ┌─────────────────┐
                    │  Redis (broker) │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
     ┌────────────┐  ┌────────────┐  ┌────────────┐
     │  celery-   │  │  celery-   │  │   flower   │
     │  worker    │  │   beat     │  │ (monitor)  │
     └────────────┘  └────────────┘  └────────────┘
              │              │
              ▼              ▼
     ┌────────────┐  ┌─────────────────────┐
     │ PostgreSQL │  │ Scheduled tasks:    │
     │ (results)  │  │ - cleanup-expired   │
     └────────────┘  │ - requeue-stuck     │
                      │ - cleanup-dlq       │
                      └─────────────────────┘
```

## Queue Design

| Queue | Purpose | Routing Key |
|---|---|---|
| `downloads` | New download jobs | `downloads` |
| `retries` | Retry attempts with delay | `retries` |
| `dlq` | Permanently failed jobs | `dlq` |

## Deployment

### Production (any VPS)

No changes needed to `bootstrap.sh` — the `docker-compose.yml` now includes `celery-worker`, `celery-beat`, and `flower` services.

### Local Development

```bash
# Start everything (including Celery)
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build

# Access points:
# - Web Dashboard: http://localhost:8000/web/downloads
# - Flower:        http://localhost:5555 (admin:admin)
```

### Manual Worker Start (debugging)

```bash
# Worker only
python -m worker.celery_main worker

# Beat scheduler only
python -m worker.celery_main beat

# Flower dashboard only
python -m worker.celery_main flower

# Enqueue pending jobs (recovery)
python -m worker.celery_main enqueue
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `CELERY_QUEUES` | `downloads,retries,dlq` | Comma-separated queue list |
| `CELERY_CONCURRENCY` | `2` | Worker concurrency |
| `FLOWER_PORT` | `5555` | Flower dashboard port |
| `FLOWER_BASIC_AUTH` | `admin:admin` | Flower HTTP basic auth |

## Monitoring

### Flower Dashboard

Access at `http://localhost:5555` to view:
- Active/received/succeeded/failed tasks
- Worker status and concurrency
- Queue lengths
- Task history and retry chains

### Key Metrics

| Metric | Description |
|---|---|
| `vooglaadija_jobs_completed_total` | Jobs completed (by status) |
| `vooglaadija_job_duration_seconds` | Job processing duration |
| `vooglaadija_dlq_depth` | Dead-letter queue size |
| `vooglaadija_queue_depth` | Combined queue depth |

## Retry Behavior

Celery tasks use exponential backoff with jitter:

| Attempt | Base Delay | Max Delay |
|---|---|---|
| 1 | 10s | 15s |
| 2 | 20s | 30s |
| 3 | 40s | 60s |

Max 3 retries per task (configurable via `task_max_retries`).

## Backward Compatibility

The legacy Redis list keys (`download_queue`, `retry_queue`, `circuit_deferred_queue`) are no longer used. Existing queued jobs will be processed by the legacy worker during migration.

To migrate existing pending jobs:

```bash
# Enqueue all pending jobs in the database
python -m worker.celery_main enqueue
```

## Rollback

To revert to the legacy worker:

1. Revert `docker-compose.yml` to use the `worker` service
2. Revert `core/queue.py` to use `lpush("download_queue")`
3. Deploy the previous version

## References

- [Celery Documentation](https://docs.celeryq.dev/)
- [Flower Documentation](https://flower.readthedocs.io/)
- [Celery Best Practices](https://denibertovic.com/posts/celery-best-practices/)
