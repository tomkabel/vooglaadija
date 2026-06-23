# Vooglaadija: Resilient by Design

## Gamma.app Setup & Import Guide (2026-Verified)

**Read this section before importing into Gamma.**

### How to Import

1. Go to [gamma.app](https://gamma.app) and create a new Presentation.
1. Select **Paste** mode.
1. Paste everything from `# Slide 1` onward.
1. Theme: **Technical** → **Dark mode, monospace accent**.
1. Gamma splits cards at `---` separators.

### Recommended Plan (April 2026)

| Plan | Price | Why |
|------|-------|-----|
| **Pro** | $18/mo | 60 cards/prompt, premium models, custom branding |
| **Ultra** | $100/mo | 75 cards, **Studio Mode** for cinematic title/closing cards |

**One month of Pro is sufficient.** Upgrade to Ultra only if you want Studio Mode cinematic motion on Slide 1 and Slide 9.

### Theme Settings

| Element | Value |
|---------|-------|
| Background | `#0A0A0F` |
| Primary text | `#E2E8F0` |
| Accent / Warning | `#F59E0B` |
| Success | `#10B981` |
| Error | `#EF4444` |
| Title font | Inter Bold |
| Body font | Inter Regular |
| Monospace font | JetBrains Mono |

### Diagrams: Two Methods

1. **Gamma Smart Diagrams** — Type `/smart`, select type, paste node list. Preferred.
1. **ASCII Art** — Paste into Gamma code blocks as fallback.

### Export for Video

- PNG at **1920x1080**, **2x scale**
- Aspect ratio: **16:9**
- PDF handout for examiner
- PPTX backup for PowerPoint users

---

## DECK CONTENT STARTS BELOW

**Paste everything from `# Slide 1` into Gamma.**

---

## Slide 1 — Title

### Resilient by Design

#### A Crash-Proof Distributed YouTube Processor

## Job #4473

A single download job. Four services. Six coordinated steps. Three catastrophic failures. One successful recovery.

Production systems fail. This one recovers.

> **Gamma direction:** Use Studio Mode (Ultra) for subtle parallax. Title in Inter Bold 72pt. Subtitle in Inter Regular 28pt.

---

## Slide 2 — The Promise

### This is Job #4473

A user pasted a YouTube link. What they saw was a progress bar. What actually happened was a distributed system executing six coordinated steps across four services.

Then the API crashed. Then Redis went down. Then the worker was killed mid-download.

Job #4473 still finished.

#### The Six Steps

1. User submits URL → API receives request
1. API writes job + outbox event in **atomic transaction**
1. Outbox relay publishes to Redis queue
1. Worker **claims job atomically** from database
1. Worker downloads via yt-dlp with circuit breaker protection
1. File delivered. Metrics recorded. Logs prove it.

#### Visual: System Flow

Use Gamma Smart Diagram `/smart` → **Flowchart** → paste:

```text
User
FastAPI API
PostgreSQL
Outbox Relay
Redis Queue
Worker (yt-dlp)
Storage
```

#### ASCII Backup

```text
                    +---------+
                    |  User   |
                    +----+----+
                         |
                         v
+------------------+  +------------------+
|  FastAPI API     |  |  PostgreSQL      |
|  /api/v1/        |<->|  Jobs + Outbox   |
|  downloads       |  |  (atomic tx)     |
+--------+---------+  +--------+---------+
         |                     |
         |            +--------v---------+
         |            | Outbox Relay     |
         |            | SKIP LOCKED      |
         |            +--------+---------+
         |                     |
         |            +--------v---------+
         |            | Redis Queue      |
         |            +--------+---------+
         |                     |
         |            +--------v---------+
         |            | Worker           |
         |            | Atomic Claim     |
         |            | Circuit Breaker  |
         |            +--------+---------+
         |                     |
         |            +--------v---------+
         +----------->| Storage          |
                      +------------------+
```

---

## Slide 3 — The Gap

### Why Scripts Fail in Production

#### 55 min

Average API downtime per week, Q1 2025  
*Source: Uptrends State of API Reliability 2025 (2B+ checks)*

#### +60%

Year-over-year increase in API downtime

#### 51%

Automated bot traffic share in 2024  
*Source: Imperva Bad Bot Report 2025 (analyzing 2024 data)*

#### The Lesson

A script handles the happy path. A system handles reality.

Platforms respond to automated traffic with aggressive rate limiting. A script that assumes a reliable API will fail in production.

We did not build a script. We built a system that **expects failure**.

---

## Slide 4 — The Architecture: Outbox Pattern

### The Birth & The Bridge

#### Atomic Transaction

When Job #4473 is born, it is written to the database twice — once as a job record, once as an outbox event — inside a single transaction.

If the API crashes one millisecond after commit, both records survive.  
If it crashes before commit, both disappear.

**There is no partial state.**

#### The Code

File: `app/api/routes/downloads.py:160-162`

```python
job = DownloadJob(id=job_id, user_id=current_user.id, url=data.url, status="pending")
db.add(job)
await write_job_to_outbox(db, job_id)   # Same session, same transaction
await db.commit()                       # Atomic: both or neither
```

#### The Relay: A Bridge, Not a Warehouse

Every thirty seconds, the outbox relay asks PostgreSQL: "Give me pending entries, but skip anything another relay is already handling." After publishing to Redis, it **deletes** the outbox entry. The table stays empty. No bloat.

File: `worker/processor.py:348-414`

```python
async def sync_outbox_to_queue(batch_size: int = 100) -> int:
    claim_result = await db.execute(
        select(Outbox)
        .where(Outbox.status == "pending")
        .order_by(Outbox.created_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)   # Skip locked rows
    )
    entries = claim_result.scalars().all()

    for entry in entries:
        await redis_client.lpush("download_queue", str(entry.job_id))
        await db.execute(delete(Outbox).where(Outbox.id == entry.id))
    await db.commit()
```

#### Visual: The Atomic Transaction

Use Gamma Smart Diagram `/smart` → **Venn Diagram** → outer circle: **PostgreSQL Transaction**, inner circles: **Job Record** + **Outbox Event**.

#### ASCII Backup — Transaction Boundary

```text
+---------------------------------------------------+
|           PostgreSQL TRANSACTION                  |
|                                                   |
|   +----------------+    +---------------------+   |
|   | download_jobs  |    | outbox              |   |
|   |----------------|    |---------------------|   |
|   | id: #4473      |    | id: <uuid>          |   |
|   | status: pending|    | job_id: #4473       |   |
|   | url: youtube...|    | event_type: enqueue |   |
|   +----------------+    | status: pending     |   |
|                         +---------------------+   |
|                                                   |
|              await db.commit()                    |
|                                                   |
|   Crash here?  BOTH rolled back = safe            |
|   Crash after? BOTH committed = recoverable       |
+---------------------------------------------------+
```

---

## Slide 5 — The Architecture: Atomic Claims

### Exactly-Once Delivery Is a Myth

**Exactly-once delivery** is impossible in distributed systems. Redis can deliver the same job twice. What we guarantee is **exactly-once processing** via idempotent state transitions.

Our workers do not ask permission — they **claim** the job with an update that only succeeds if status is `pending`.

PostgreSQL's MVCC guarantees that even if two workers execute simultaneously, exactly one wins. The duplicate is not an error. It is expected, and silently discarded.

#### The Code

File: `worker/processor.py:104-124`

```python
# Atomic guarded claim
result = await db.execute(
    update(DownloadJob)
    .where(DownloadJob.id == job_id, DownloadJob.status == "pending")
    .values(status="processing", updated_at=datetime.now(UTC))
)
await db.commit()

claimed = result.rowcount == 1
if not claimed:
    logger.info("job_not_claimed", job_id=str(job_id))
    return False   # Another worker got it — expected behavior
```

#### At-Least-Once Delivery + Idempotent State Transitions

| Delivery | Worker A | Worker B | Outcome |
|----------|----------|----------|---------|
| First | Claims job (rowcount=1) | — | Worker A processes |
| Duplicate | — | Attempts claim (rowcount=0) | Worker B silently exits |

#### Visual: The Race

Use Gamma Smart Diagram `/smart` → **Sequence Diagram** → actors: `Worker A`, `PostgreSQL`, `Worker B`.

#### ASCII Backup — Race Condition Resolution

```text
Time ->

Worker A                         PostgreSQL                      Worker B
   |                                 |                              |
   |-- UPDATE #4473 SET status='processing' WHERE status='pending' ->|
   |                                 |<-- UPDATE #4473 ... ----------|
   |                                 |                              |
   |<-- rowcount = 1 (WINS) --------|                              |
   |                                 |-- rowcount = 0 (LOSES) ----->|
   |                                 |                              |
   |-- Proceed with download ------->|                              |
   |                                 |                              |
   |<-- Commit ---------------------|                              |
   |                                 |<-- Commit (no-op) -----------|

Result: Exactly one worker processes Job #4473.
       The duplicate is not an error. It is by design.
```

---

## Slide 6 — The Storm: Rate Limits & Backoff

### The YouTube API Returns HTTP 429

We do not panic. We calculate an AWS-standard **full jitter backoff**.

#### The Formula

```text
delay = random.uniform(0, min(cap, base * 2^attempt))
```

| Attempt | Max Delay | Example Actual |
|---------|-----------|----------------|
| 1 | 60s | 18s |
| 2 | 120s | 73s |
| 3 | 240s | 195s |
| 4 | 480s | 421s |
| 5+ | 600s (cap) | 0-600s |

Every retrying job picks a random point in the window. No thundering herds. No coordinated stampedes. Just statistical dispersion.

#### The Code

File: `app/services/retry_service.py:29-57`

```python
def calculate_retry_with_jitter(retry_count: int) -> datetime:
    """Exponential Backoff with Full Jitter per AWS Well-Architected Framework."""
    RETRY_BASE_SECONDS = 60   # 1 minute
    RETRY_CAP_SECONDS = 600   # 10 minutes

    cap_delay = min(RETRY_CAP_SECONDS, RETRY_BASE_SECONDS * (2 ** retry_count))
    delay = random.uniform(0, cap_delay)
    return datetime.now(UTC) + timedelta(seconds=delay)
```

#### References

- AWS Architecture Blog: "Exponential Backoff and Jitter"
- Google SRE Book, Chapter 6 (Handling Overload)
- Netflix Tech Blog: "HASTINGS Presents: Exponential Backoff"

#### Visual: Jitter Timeline

Use Gamma Smart Diagram `/smart` → **Timeline**.

#### ASCII Backup

```text
Attempt 1          Attempt 2              Attempt 3
|----+----|        |----+----+----+----|  |----+----+----+----+----+----+----|
0    30s   60s     0        60s     120s  0              120s           240s
[====18s==>]       [========73s======>]   [==============195s=============>]

Attempt 4                        Attempt 5+
|----+----+----+----+----+----+----|  |----+----+----+----+----+----+----|
0                  240s           480s   0                  300s           600s
[==================421s=============>]  [==================587s=============>]

CAP HIT at 600s — all subsequent attempts use 0-600s window
```

---

## Slide 7 — The Storm: Catastrophic Recovery

### SIGKILL & SIGTERM: Two Failure Modes, One Recovery

#### SIGKILL: When Graceful Shutdown Is Impossible

We kill the worker. Not gracefully — `SIGKILL`, like an OOM kill in Kubernetes. The shutdown handler never runs. Job #4473 is stranded in `processing` state.

Fifteen minutes later, the zombie sweeper finds it, marks it `pending`, and another worker picks it up.

**Critical fix:** The original sweeper performed a naked dual-write (DB `UPDATE` then direct Redis `LPUSH`). We fixed this to route recovery through the **transactional outbox**, consistent with the rest of the architecture.

#### The Code (Fixed)

File: `worker/zombie_sweeper.py:68-87`

```python
# Create outbox entry atomically with status update.
# Prevents the dual-write problem: if Redis is down,
# the outbox relay will enqueue when it recovers.
outbox_entry = Outbox(
    id=uuid.uuid4(),
    job_id=job.id,
    event_type="zombie_recovery",
    payload=json.dumps({"recovered_at": datetime.now(UTC).isoformat()}),
    status="pending",
)
db.add(outbox_entry)

await db.execute(
    update(DownloadJob)
    .where(DownloadJob.id == job.id)
    .values(status="pending", updated_at=datetime.now(UTC))
)
```

#### SIGTERM: The Polite Failure

For planned shutdowns, we handle `SIGTERM` with a 25-second grace period.

| Time | Event |
|------|-------|
| t=0 | SIGTERM received; `shutdown_event.set()` |
| t=0-25 | Current job completes OR is requeued via outbox |
| t=25 | Worker exits cleanly |
| t=30 | Kubernetes sends SIGKILL (5s runway) |

The `_requeue_job` helper also uses the outbox pattern — no direct Redis call.

#### ASCII Backup — SIGKILL Recovery (Outbox-Safe)

```text
  t=0                    t=30s                   t=15min
   |                        |                        |
   v                        v                        v
+-------+             +----------+             +-------------------+
|Worker |  SIGKILL    | Job #4473|             |Zombie Sweeper     |
|Active |-----------> | status:  |------------>|detects stale job   |
|       |   (dead)    |PROCESSING|   (stuck)   +-------------------+
+-------+             +----------+                        |
   |                        |                             v
   |                        |            +---------------------------+
   |                        |            | Atomic Transaction        |
   |                        |            |---------------------------|
   |                        |            | UPDATE status = "pending" |
   |                        |            | INSERT outbox             |
   |                        |            |   (zombie_recovery)       |
   |                        |            +---------------------------+
   |                        |                        |
   |                        |                        v
   |                        |            +-------------------+
   |                        +----------->| Outbox Relay      |
   |                                     | (30s sync cycle)  |
   |                                     +-------------------+
   |                                               |
   |                                               v
   |                                     +-------------------+
   +------------------------------------>| New Worker        |
                                         | Claims Job #4473  |
                                         | Download Completes|
                                         +-------------------+
```

---

## Slide 8 — The Operator's View

### Observability: Three in the Morning Engineering

We do not just build systems. We operate them at three in the morning.

#### Custom Histogram Buckets

Default Prometheus buckets top out at 10 seconds. Without custom buckets, p99 for video downloads would be mathematically invalid.

File: `app/metrics.py:19-23`

```python
JOB_DURATION_SECONDS = Histogram(
    "ytprocessor_job_duration_seconds",
    "Time spent processing a job",
    buckets=[10, 30, 60, 120, 300, 600],  # Up to 10 minutes
)
```

#### Structured Logging with Correlation IDs

Every log carries a `request_id`. Reconstruct Job #4473's entire lifecycle in one `jq` query:

```bash
jq 'select(.job_id == "4473")' logs.jsonl
```

```json
{"event": "job_created",       "job_id": "4473", "request_id": "abc-123"}
{"event": "outbox_synced",     "job_id": "4473", "request_id": "abc-123"}
{"event": "job_claimed",       "job_id": "4473", "worker_id": "worker-1"}
{"event": "http_429_received", "job_id": "4473"}
{"event": "backoff_scheduled", "job_id": "4473", "delay_seconds": 73}
{"event": "job_completed",     "job_id": "4473", "duration_seconds": 142}
```

#### Health Checks: Alive vs. Ready

File: `app/api/routes/health.py`

```python
GET /health       → {"status": "healthy",  "dependencies": {"database": "ok", "redis": "ok"}}
GET /health/ready → {"status": "ready",    "database": "connected", "redis": "connected"}
# Returns HTTP 503 if dependencies are down — K8s removes pod from service
```

#### Worker Health

```python
GET :8082/health → {
    "status": "running",
    "last_heartbeat": "2026-04-29T20:15:00Z",
    "current_job_started_at": "2026-04-29T20:14:30Z",
    "uptime_seconds": 86400
}
```

> "Observability is not monitoring. It is the ability to ask new questions without deploying new code."

---

## Slide 9 — Trade-Offs & Closing

### Engineering Maturity: Owning the Decisions

#### Decision Matrix

| Decision | What We Chose | What We Sacrificed | Future |
|----------|---------------|-------------------|--------|
| **JWT Algorithm** | HS256 (sub, user_id, email) | Secret sharing burden | RS256 (asymmetric rotation) |
| **Outbox Latency** | 30s sync interval | Real-time immediacy | PostgreSQL LISTEN/NOTIFY |
| **Test Database** | SQLite per-worker (fast); PostgreSQL via docker-compose.test.yml (integration) | 5s vs SQLite speed | SKIP LOCKED fidelity |
| **Circuit Breaker** | 5 failures / 30s reset / 3 successes | Potential false positives | Tuned per endpoint |

#### Security Grid

| Feature | Implementation |
|---------|---------------|
| Password hashing | bcrypt, cost factor 12 |
| JWT tokens | HS256, 15-min access, 7-day refresh, HttpOnly cookies |
| IDOR protection | `WHERE id = :id AND user_id = :current_user` |
| Rate limiting | 10/minute per IP (SlowAPI) |
| CSRF + Security headers | CSP, X-Content-Type-Options, X-Frame-Options |
| Path traversal | `os.path.realpath` prefix validation |

#### Gaps & Post-Review Fix

What we explicitly did **not** build: RS256 rotation, bulkhead isolation, LISTEN/NOTIFY, multi-region failover.

**Architecture evolution:** During senior review, we fixed a critical flaw in the zombie sweeper. The original implementation performed a **naked dual-write** (DB `UPDATE` followed by direct Redis `LPUSH`), violating the transactional outbox pattern. The fix routes recovery through the outbox table, making the sweeper consistent with the rest of the architecture.

---

### Job #4473 Made It

Through rate limits. Through a dead Redis. Through a killed worker.

The file is on disk. The metrics confirm it. The logs prove it.

We did not build this to handle the happy path. We built it because **the happy path is a lie**.

#### Production systems fail

#### The question is not whether yours will survive

#### It is whether you designed it to recover

> **Gamma direction:** Use Studio Mode (Ultra) for cinematic parallax. Fade from dark to slightly lighter `#0A0A0F` gradient.

---

## Slide 10 — Credits

### Vooglaadija

## Resilient by Design

### Built by

- @tomkabel
- @Kevindaman
- @triinum

**Course:** Junior to Senior Developer (TalTech)  
**Date:** April 2026  
**Version:** 1.0.0

#### Links

- GitHub Repository: `github.com/tomkabel/team21-vooglaadija`
- Gamma Leave-Behind Deck: `[Private Gamma Link]`
- API Docs: `/docs` (Swagger UI)
- Metrics: `/metrics` (Prometheus)

#### Tech Stack

Python 3.12 · FastAPI · PostgreSQL · Redis · yt-dlp · Docker · Prometheus · Grafana · OpenTelemetry · Sentry

---

### End of Gamma Deck Content

**Import everything above this line into Gamma.app.**
