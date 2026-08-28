# Migration to Celery Job Queue

Replaces the hand-rolled BRPOP worker with Celery + Redis for durable, observable,
horizontally-scalable job processing.

## What Changed

| Legacy (BRPOP)                 | New (Celery)                      |
| ------------------------------ | --------------------------------- |
| `worker/main.py` event loop    | `worker` + `celery-beat` services |
| Custom Lua scripts for retry   | DB-driven retry with backoff      |
| `retry_queue` Redis sorted set | `retries` Celery queue            |
| DLQ via DB table               | `dlq` Celery queue + DB table     |
| `zombie_sweeper.py`            | Celery Beat `requeue-stuck-jobs`  |
| Custom outbox relay            | Celery Beat `enqueue-pending`     |
| Health server on :8082         | Flower dashboard on :5555         |

## Architecture

```text
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
     │   worker   │  │ celery-    │  │   flower   │
     │            │  │ beat       │  │ (monitor)  │
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

| Queue       | Purpose                   | Routing Key |
| ----------- | ------------------------- | ----------- |
| `downloads` | New download jobs         | `downloads` |
| `retries`   | Retry attempts with delay | `retries`   |
| `dlq`       | Permanently failed jobs   | `dlq`       |

## Deployment

### Production (any VPS)

No changes needed to `bootstrap.sh` — the `docker-compose.yml` now includes the `worker`,
`celery-beat`, and `flower` services. Set `FLOWER_BASIC_AUTH` in the environment (compose fails fast
if unset).

### Local Development

```bash
# Start everything (including Celery)
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build

# Access points:
# - Web Dashboard: http://localhost:8000/web/downloads
# - Flower:        http://localhost:5555 (vooglaadija:vooglaadija in local dev)
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

| Variable             | Default                 | Description                            |
| -------------------- | ----------------------- | -------------------------------------- |
| `CELERY_QUEUES`      | `downloads,retries,dlq` | Comma-separated queue list             |
| `CELERY_CONCURRENCY` | `2`                     | Worker concurrency                     |
| `FLOWER_PORT`        | `5555`                  | Flower dashboard port                  |
| `FLOWER_BASIC_AUTH`  | required                | Flower HTTP basic auth (user:password) |

## Monitoring

### Flower Dashboard

Access at `http://localhost:5555` to view:

- Active/received/succeeded/failed tasks
- Worker status and concurrency
- Queue lengths
- Task history and retry chains

### Key Metrics

| Metric                             | Description                |
| ---------------------------------- | -------------------------- |
| `vooglaadija_jobs_completed_total` | Jobs completed (by status) |
| `vooglaadija_job_duration_seconds` | Job processing duration    |
| `vooglaadija_dlq_depth`            | Dead-letter queue size     |
| `vooglaadija_queue_depth`          | Combined queue depth       |

## Retry Behavior

Retries are DB-driven: `_handle_result` records `retry_count` / `next_retry_at` on the job and
re-dispatches the task to the `retries` queue with exponential backoff plus jitter (single retry
budget — no Celery autoretry):

| Attempt | Base Delay | Max Delay |
| ------- | ---------- | --------- |
| 1       | 10s        | 15s       |
| 2       | 20s        | 30s       |
| 3       | 40s        | 60s       |

Max 3 retries per job (configurable via `DownloadJob.max_retries`).

## Backward Compatibility

The legacy Redis list keys (`download_queue`, `retry_queue`, `circuit_deferred_queue`) are no longer
used.

### Migration Procedure

1. **Stop the legacy worker** before deploying the new Celery worker to prevent duplicate
   processing.
2. **Deploy** the new Celery worker and Beat scheduler.
3. **Drain existing pending jobs** from the database into Celery:

   ```bash
   # Enqueue all pending jobs in the database
   python -m worker.celery_main enqueue
   ```

4. **Verify** that affected database jobs remain `pending` or move to `processing`:

   ```sql
   SELECT id, status, retry_count FROM download_jobs WHERE status = 'pending';
   ```

Jobs stuck in `pending` after the worker has been running for several minutes indicate a
configuration issue.

## Rollback

To revert to the legacy worker:

1. Revert `docker-compose.yml` to use the `worker` service
2. Revert `core/queue.py` to use `lpush("download_queue")`
3. Deploy the previous version

## References

- [Celery Documentation](https://docs.celeryq.dev/)
- [Flower Documentation](https://flower.readthedocs.io/)
- [Celery Best Practices](https://denibertovic.com/posts/celery-best-practices/)
