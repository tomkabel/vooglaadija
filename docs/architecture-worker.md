# Architecture — Worker

**Part:** Worker (`worker/`) **Type:** Background async job processor

---

## Executive Summary

The Worker is a standalone async process that consumes download jobs from a Redis queue, executes
yt-dlp subprocesses, classifies errors, and manages retries with circuit breaker protection. It runs
a health HTTP server on port 8082 and publishes real-time status updates via Redis Pub/Sub.

## Technology Stack

| Category      | Technology                         | Purpose                                                                         |
| ------------- | ---------------------------------- | ------------------------------------------------------------------------------- |
| Runtime       | Python 3.12 async                  | Event loop                                                                      |
| Queue         | Redis 7                            | Job queue (List), retry queue (Sorted Set), circuit deferred queue (Sorted Set) |
| Processing    | yt-dlp                             | YouTube video download/extraction                                               |
| Media         | FFmpeg                             | Video/audio format processing                                                   |
| Resilience    | Circuit Breaker + Error Classifier | Fault tolerance                                                                 |
| Observability | Prometheus + structlog             | Metrics and structured logging                                                  |

## Architecture Pattern

Event-driven async loop with three queue data structures:

- **`download_queue`** (Redis List) — Primary work queue, BRPOP consumption
- **`retry_queue`** (Redis Sorted Set) — Timestamp-scored delayed retries
- **`circuit_deferred_queue`** (Redis Sorted Set) — Jobs waiting for circuit recovery

Worker processing is decomposed into focused modules:

| Module                      | Ownership                                                         |
| --------------------------- | ----------------------------------------------------------------- |
| `worker/processor.py`       | Orchestrates job processing and circuit deferral                  |
| `worker/job_claimer.py`     | Queue ID normalization, atomic DB claim, heartbeat helpers        |
| `worker/job_executor.py`    | yt-dlp execution, progress publishing, completion, file cleanup   |
| `worker/retry_scheduler.py` | Error classification, retry budget decisions, retry outbox writes |
| `worker/dlq_manager.py`     | DLQ writes, DLQ depth metrics, failed-job replay/reset helpers    |
| `worker/outbox_relay.py`    | Pending outbox recovery and stale terminal outbox cleanup         |
| `worker/zombie_sweeper.py`  | Requeue stuck processing jobs                                     |

## Job Processing Flow

1. **Claim** — Atomically updates DB job status from `pending` → `processing`
1. **Heartbeat** — Background task bumps `updated_at` every 30s during extraction
1. **Throttle Check** — Pre-emptive risk scoring from `throttle_predictor`
1. **Extraction** — yt-dlp subprocess via circuit breaker with attempt-aware timeout
1. **Completion** — Update DB, publish via Pub/Sub, store file
1. **Error Handling** — Classify → check retry budget → retry/DLQ/defer

## Circuit Breaker

States: CLOSED → OPEN (5 failures) → HALF_OPEN (30s timeout) → CLOSED (3 successes). Jobs are
**deferred** (not failed) when the circuit is open — they auto-drain when the circuit recovers.
Distributed state via Redis when `CIRCUIT_BREAKER_USE_REDIS=1`.

## Error Classification & Retry Policies

| Category           | Max Retries | Base Delay | Max Delay | Jitter Type  | Circuit Breaker |
| ------------------ | ----------- | ---------- | --------- | ------------ | --------------- |
| RATE_LIMITED       | 5           | 60s        | 1200s     | Decorrelated | No              |
| TRANSIENT          | 3           | 10s        | 600s      | Decorrelated | Yes             |
| BLOCKED            | 0           | —          | —         | None         | No              |
| NOT_FOUND          | 0           | —          | —         | None         | No              |
| FORMAT_UNAVAILABLE | 0           | —          | —         | None         | No              |
| TIMEOUT            | 2           | 30s        | 600s      | Full         | Yes             |
| STORAGE            | 1           | 300s       | 300s      | Full         | No              |
| UNKNOWN            | 2           | 30s        | 600s      | Full         | Yes             |

**Retry Budget:** Sliding 60s window — retries must be <10% of total requests. Prevents cascading
failure.

## Health Server (port 8082)

| Endpoint       | Response                                         |
| -------------- | ------------------------------------------------ |
| `GET /health`  | JSON with worker state, uptime, status (200/503) |
| `GET /metrics` | Prometheus `generate_latest()`                   |

Redis heartbeat every 10s with 30s TTL (key: `worker:health:{worker_id}`).

## Zombie Sweeper

Periodic poll (every 5 minutes) that finds jobs stuck in `processing` with `updated_at > 15 minutes`
cutoff and requeues them. Protected by the heartbeat mechanism to prevent false-positive claims on
active workers.

## Graceful Shutdown

- Captures SIGTERM/SIGINT with configurable grace period (default 25s)
- Shortens BRPOP and extraction timeouts during shutdown
- Requeues in-flight jobs before exit
