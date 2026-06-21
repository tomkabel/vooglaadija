# Component Inventory — Worker

**Part:** Worker (`worker/`)

---

## Core Components

| Component       | File                        | Responsibility                                                   |
| --------------- | --------------------------- | ---------------------------------------------------------------- |
| Main Loop       | `worker/main.py`            | Event loop, signal handling, orchestration, periodic maintenance |
| Processor       | `worker/processor.py`       | Thin orchestration for claim → execute → retry/DLQ/defer         |
| Job Claimer     | `worker/job_claimer.py`     | Queue pop normalization, atomic DB claim, heartbeat helpers      |
| Job Executor    | `worker/job_executor.py`    | yt-dlp execution, progress publishing, completion, file cleanup  |
| Retry Scheduler | `worker/retry_scheduler.py` | Error classification, retry decisions, retry outbox writes       |
| DLQ Manager     | `worker/dlq_manager.py`     | Failed-job movement, DLQ depth metrics, replay/reset helpers     |
| Outbox Relay    | `worker/outbox_relay.py`    | Pending outbox sync to Redis and stale terminal row cleanup      |
| Queue           | `core/queue.py`             | Redis queue wrappers with deduplication shared by API and worker |
| Health          | `worker/health.py`          | HTTP health server (port 8082) + Redis heartbeat                 |
| State           | `worker/state.py`           | Shared `shutdown_event` (lazy asyncio.Event)                     |
| Zombie Sweeper  | `worker/zombie_sweeper.py`  | Reclaims stuck processing jobs                                   |

## Queue Data Structures

| Structure                | Type                                  | Purpose                      |
| ------------------------ | ------------------------------------- | ---------------------------- |
| `download_queue`         | Redis List (BRPOP)                    | Primary work queue           |
| `retry_queue`            | Redis Sorted Set (ZADD/ZRANGEBYSCORE) | Delayed retry queue          |
| `circuit_deferred_queue` | Redis Sorted Set                      | Circuit-deferred jobs        |
| `outbox`                 | PostgreSQL Table                      | Transactional outbox pattern |

## Maintenance Tasks (periodic)

| Task                             | Interval | Purpose                                        |
| -------------------------------- | -------- | ---------------------------------------------- |
| `cleanup_expired_jobs()`         | 5min     | Delete expired completed jobs + files          |
| `requeue_stuck_jobs()`           | 5min     | Zombie sweeper                                 |
| `cleanup_expired_dlq()`          | 5min     | Purge expired DLQ entries                      |
| `cleanup_stale_outbox_entries()` | 5min     | Delete old outbox rows                         |
| `sync_outbox_to_queue()`         | 30s      | Push pending outbox entries to Redis           |
| Health heartbeat                 | 10s      | Write `worker:health:{worker_id}` with 30s TTL |
| Queue depth metric               | 5s       | Lua script summing all three queue depths      |
