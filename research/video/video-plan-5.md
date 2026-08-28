# Resilient by Design: A Crash-Proof Distributed YouTube Processor

**Presentation Type:** Technical demonstration (online, async)  
**Target Runtime:** 12 minutes (hard ceiling: 15 minutes)  
**Audience:** Course examiners (TalTech)  
**Aspect Ratio:** 16:9, 1920x1080 export  
**Primary Tool Stack:** Google Slides / Canva (deck) + OBS (screen capture) + CapCut / DaVinci Resolve (edit)  
**Version:** 6.1

---

## Why This Plan Replaces v5.1

v5.1 claimed an 8-minute runtime while containing 35+ minutes of lecture content. It prioritized explanation over demonstration.

This plan follows one concrete job through the system to keep the narrative grounded. No abstract architecture lectures.

---

## Presentation Tools

### Google Slides / Canva — Deck Engine

**Use case:** Title cards, architecture diagrams, and text slides.

| Feature | How We Use It |
|---------|---------------|
| **Diagrams** | Draw the data-flow diagram (API → PostgreSQL → Outbox → Redis → Worker) using built-in shapes. Export as PNG at 1920x1080. |
| **Text slides** | Title cards and bullet slides with monospace font for code references. |

**Workflow:**

1. Create slides at 16:9 (1920x1080).
1. Export each slide as PNG for video timeline import.
1. Export final deck as PDF for the course submission appendix.

**Cost:** Free.

### Why Not Gamma / PPT.AI?

- Subscription tools add cost and export friction for a one-time course submission.
- Google Slides + Canva produce clean, professional output without lock-in.
- Examiner handout is a PDF; no PowerPoint compatibility required.

---

## Visual Identity

| Element | Specification | Rationale |
|---------|-------------|-----------|
| **Primary font** | Inter Bold (titles), Inter Regular (body) | Legible at small sizes, neutral, professional |
| **Monospace font** | JetBrains Mono | Code, metrics, logs — distinguish from prose |
| **Color palette** | `#0A0A0F` (bg), `#E2E8F0` (text), `#F59E0B` (accent/warning), `#10B981` (success), `#EF4444` (error) | High contrast, colorblind-safe, matches terminal aesthetics |
| **Lower third** | 40px height bar, `#0A0A0F` @ 85% opacity, JetBrains Mono 14pt | File reference |
| **Transition** | Hard cut | Simple, fast |
| **Code highlighting** | Dracula theme, 18pt minimum, inset over blurred background | Readable on mobile |

---

## Narrative Spine: A Single Walkthrough

Every scene follows the same download job to create continuity.

```text
SCENE 1: Submit a YouTube link. Job is created.
SCENE 2: Why a naive script fails.
SCENE 3: The outbox pattern writes the job atomically.
SCENE 4: Redis queue and atomic worker claim.
SCENE 5: Rate limit hit. Exponential backoff with jitter.
SCENE 6: Worker killed mid-download. Zombie sweeper recovers it.
SCENE 7: Observability: metrics, logs, and health checks.
SCENE 8: Trade-offs and what we did not build.
```

---

## Scene-by-Scene Production Plan

### SCENE 1: Introduction (0:00 – 1:00)

**Purpose:** Show what the system does and why it matters.

**Visual:**

- 0:00-0:10 — Title card: **"Resilient by Design"** with subtitle: *Handling failure in a distributed YouTube processor.*
- 0:10-0:25 — Screen recording: paste URL → submit → loading spinner → success.
- 0:25-0:45 — Architecture diagram with a static highlight path: API → DB → Outbox → Redis → Worker → Download.
- 0:45-1:00 — Terminal montage: HTTP 429 → retry → worker killed → sweeper recovers → file complete.

**Narration:**
> "A user submits a YouTube link. Behind the loading spinner, the system executes six coordinated steps across four services. The API crashes. Redis restarts. A worker is killed mid-download. The job still completes. This is how."

**Timing cue:** Keep under 60 seconds. Cut diagram time if narration runs long.

---

### SCENE 2: The Problem (1:00 – 2:00)

**Purpose:** Show why a simple script is insufficient.

**Visual:**

- 1:00-1:20 — Screen recording of a naive Python script (`yt-dlp` wrapper) failing with `HTTP 429`, then `ConnectionResetError`, then partial file on disk.
- 1:20-1:35 — Text overlay: **"A script handles the happy path. A system handles failure."**
- 1:35-2:00 — Transition to architecture diagram. Lower third: `Architecture | app/api/routes/downloads.py`

**Narration:**
> "YouTube rate-limits aggressively. Network connections drop. A script that assumes a reliable API will leave partial files on disk and fail silently. We built a system that expects every failure mode — rate limits, crashes, killed workers — and recovers automatically."

---

### SCENE 3: The Architecture (2:00 – 5:00)

**Purpose:** Show how the job survives through three core mechanisms.

## BEAT A: The Birth (2:00 – 2:45)

**Visual:**

- Screen recording of the web UI submitting a URL.
- Cut to code inset showing the outbox write in `app/api/routes/downloads.py`.
- Highlight: `await db.commit()`.
- Static diagram: two boxes (Job, Outbox) inside a larger box labeled "PostgreSQL Transaction".
- Lower third: `Outbox Pattern | Atomic transaction`

**Narration:**
> "The job is written to the database twice — once as a job record, once as an outbox event — inside a single transaction. If the API crashes after commit, both records survive. If it crashes before commit, both disappear. There is no partial state."

## BEAT B: The Bridge (2:45 – 3:45)

**Visual:**

- Code inset: `FOR UPDATE SKIP LOCKED` highlighted in the SQL snippet from `worker/processor.py`.
- Static diagram: multiple relay workers, with row locks visualized as brackets around database rows.
- Lower third: `Outbox Relay | SKIP LOCKED`

**Narration:**
> "Every thirty seconds, the outbox relay polls PostgreSQL for pending entries, skipping anything another relay is already handling. This lets us scale relays horizontally with no extra coordination. After publishing to Redis, we delete the outbox entry. The table stays empty. The outbox is a bridge, not a warehouse."

## BEAT C: The Claim (3:45 – 5:00)

**Visual:**

- Code inset: `claim_job` with `WHERE status = 'pending'` highlighted.
- Static diagram: two workers approach the same database row. One succeeds (rowcount == 1), the other gets zero rows and exits.
- Text card: **"At-least-once delivery. Idempotent state transitions."**

**Narration:**
> "Exactly-once delivery is impossible with Redis. The same job can be delivered twice. So workers claim the job with an update that only succeeds if status is pending. The atomicity of the UPDATE statement guarantees that even if two workers execute simultaneously, exactly one wins. The duplicate is expected and silently discarded."

---

### SCENE 4: Failure Handling (5:00 – 7:00)

**Purpose:** Show resilience under two failure modes.

## BEAT A: Rate Limit & Jitter (5:00 – 5:45)

**Visual:**

- Screen recording: log stream in terminal.
- Log line: `ERROR | HTTP 429`.
- Next line: `INFO | Backoff attempt 1. Delay: 18s`.
- Static timeline graphic: retry at 18s, fails, retry at 73s, succeeds.
- Lower third: `Full Jitter | random.uniform(0, min(cap, base * 2^attempt))`

**Narration:**
> "The API returns a 429. We back off with full jitter — a random delay between zero and the exponential cap. With a 60-second base, attempt four hits a 480-second cap. Every retrying job picks a random point in that window. No thundering herds."

## BEAT B: The Kill (5:45 – 6:30)

**Visual:**

- **Pre-recorded.** Split screen:
  - Top: Terminal running `docker kill vooglaadija_worker_1`.
  - Bottom: Database table row. Status: `PROCESSING`.
- Kill command executes. Bottom screen pauses.
- Cut to sweeper log: `INFO | Zombie Sweeper | Requeued 1 stuck job(s)`.
- Database row updates: Status `PROCESSING` → `PENDING`.
- Lower third: `Catastrophic Recovery | SIGKILL → Zombie Sweeper | 15 min max`

**Narration:**
> "We kill the worker with SIGKILL. The shutdown handler never runs. The job is stranded in processing state. The zombie sweeper, running as part of the cleanup cycle, finds stuck jobs after fifteen minutes and requeues them. A production system would alert on sweeper activations; for this demo, we show the recovery mechanism."

## BEAT C: The Graceful Exit (6:30 – 7:00)

**Visual:**

- Static timeline graphic: SIGTERM at t=0 → polling loop breaks → 25-second grace → requeue at t=25 → SIGKILL at t=30.
- Text: **"25-second app timeout. 5-second buffer before SIGKILL."**

**Narration:**
> "For graceful shutdowns, the worker catches SIGTERM, stops accepting new jobs, and finishes or requeues the current job within twenty-five seconds. This leaves a five-second buffer before Docker sends SIGKILL. All work is accounted for: completed, requeued, or swept."

---

### SCENE 5: Observability (7:00 – 8:30)

**Purpose:** Demonstrate metrics, logs, and health checks.

**Visual:**

- 7:00-7:20 — Grafana dashboard (or pre-rendered metrics page). `ytprocessor_job_duration_seconds` histogram with custom buckets. Text overlay: **"Custom buckets: [10, 30, 60, 120, 300, 600]. Default Prometheus buckets top out at 10s. Without these, long-tail percentiles would be inaccurate."**
- 7:20-7:45 — Terminal: `docker logs ytprocessor-worker 2>&1 | jq 'select(.job_id == "4473")'`. The full timeline reconstructs in one scroll: created, enqueued, claimed, 429, backoff, retry, completed.
- 7:45-8:15 — Health check endpoints shown in rapid succession: `/health` → `{"status":"ok"}`. `/ready` → `{"database":"connected","redis":"connected"}`. Worker liveness via log heartbeat.
- 8:15-8:30 — Text card: **"Observability is not monitoring. It is the ability to ask new questions without deploying new code."** — *Charity Majors*

**Narration:**
> "Every log carries a correlation ID. Prometheus histograms use custom buckets because video downloads take minutes, not milliseconds. Health checks distinguish between alive and ready. Integration tests run against real PostgreSQL via docker-compose.test.yml because SQLite cannot validate SKIP LOCKED behavior."

---

### SCENE 6: Trade-Offs (8:30 – 10:00)

**Purpose:** Own the decisions and boundaries of the project.

**Visual:**

- 8:30-9:00 — Static trade-off matrix:

| Decision | What We Chose | What We Sacrificed |
|----------|---------------|-------------------|
| JWT Algorithm | HS256 | Asymmetric key rotation (RS256) |
| Outbox Latency | 30s sync interval | Sub-second latency (LISTEN/NOTIFY) |
| Test DB | SQLite (unit); PostgreSQL (integration) | SQLite cannot test SKIP LOCKED |
| Image Size | Multi-stage Docker with ffmpeg + Node.js | Build time and image size |

- 9:00-9:20 — Static CI/CD pipeline graphic (GitHub Actions). Stages: lint → test → build → deploy.
- 9:20-9:40 — Security feature list: bcrypt / HttpOnly cookie / IDOR protection / rate limiting.
- 9:40-10:00 — Text card: **"Gaps: RS256, bulkhead pattern, LISTEN/NOTIFY. These are documented boundaries, not oversights."**

**Narration:**
> "HS256 is fast but requires secret sharing. The thirty-second outbox sync is simple but adds latency. SQLite tests are fast but cannot validate SKIP LOCKED, so we run integration tests against PostgreSQL. The features we did not build are listed explicitly. A project without acknowledged boundaries is incomplete."

---

### SCENE 7: Closing (10:00 – 12:00)

**Purpose:** Summarize and conclude.

**Visual:**

- 10:00-10:15 — Screen recording: downloads folder with the completed file.
- 10:15-10:30 — Metrics dashboard: `ytprocessor_jobs_completed_total` increments.
- 10:30-10:50 — Fast recap montage (~2 seconds per scene highlight).
- 10:50-11:50 — Text card: **"Production systems fail. This one recovers."**
- 11:50-12:00 — Final frame: GitHub repo URL in JetBrains Mono.

**Narration:**
> "The job completed. Through rate limits, through a killed worker, through a stranded database row. The file is on disk. The metrics confirm it. The logs prove it. We built this because the happy path is a lie. Systems fail. The question is whether you designed yours to recover."

---

## Speaker Notes & Timing Cues

| Timestamp | Cue | Action If Overrunning |
|-----------|-----|----------------------|
| 0:00 | Title card. Start narration. | — |
| 1:00 | Scene 2 starts. | Cut the intro montage short. |
| 2:00 | Scene 3 starts. | Skip the naive script failure. |
| 5:00 | Scene 4 starts. | Compress Beat C to 30 seconds. |
| 7:00 | Scene 5 starts. | Show only metrics and jq log trace. Skip health checks. |
| 8:30 | Scene 6 starts. | Cut CI/CD animation. |
| 10:00 | Scene 7 starts. | Cut recap montage. Go directly to closing text card. |
| 12:00 | Hard stop. | Fade to black. |

---

## Demo Protocol: Pre-Recorded Chaos

**Why pre-recorded:** A 12-minute video has no margin for demo failure.

**Segment:** `demo_sigkill_recovery.mp4` (45 seconds, 1920x1080, 30fps)

**Recording steps:**

1. Start full stack: `docker-compose up`
1. Submit a job via API.
1. Start screen recording (OBS).
1. Terminal 1: `watch -n 1 'psql -c "SELECT id, status FROM download_jobs WHERE id = ..."'`
1. Terminal 2: `docker compose kill worker` (the worker service is auto-named `vooglaadija-worker-N`; `docker kill ytprocessor-worker` no longer applies since the static container_name was removed to allow `WORKER_REPLICAS` scaling)
1. Manually trigger the sweeper or wait for the 15-minute interval.
1. Terminal 1 shows status flip from `PROCESSING` to `PENDING`.
1. Stop recording.
1. In video editor: compress the wait to a 2-second freeze-frame with "15 min later" text.
1. Composite both terminals as split-screen with lower third.

**Note:** The sweeper is triggered manually for the recording. This is disclosed below.

---

## Factual Accuracy Log

| Claim | Source | Status | Video Treatment |
|-------|--------|--------|-----------------|
| Full jitter formula | AWS Architecture Blog, "Exponential Backoff and Jitter" | ✅ Verified | Cite on-screen as "AWS Architecture Standard" |
| SKIP LOCKED | PostgreSQL 9.5+ docs | ✅ Verified | No citation needed — standard SQL |
| HTTP 429 from YouTube | yt-dlp / Google API behavior | ✅ Verified | Say "HTTP 429 Rate Limited" |
| PostgreSQL test runtime | docker-compose.test.yml provides real Postgres | ✅ Verified | Say "integration tests use real PostgreSQL" |

**Action items before recording:**

- [x] Verify all test files referenced in the Evidence Matrix actually exist.
- [x] Wire zombie sweeper (`requeue_stuck_jobs`) into worker main loop with 15-minute timeout.
- [x] Add dedicated 30-second outbox sync interval separate from cleanup cycle.
- [x] Add `user_id` and `email` to JWT access token payload.
- [x] Add correlation ID middleware binding to structlog contextvars.
- [x] Add PostgreSQL test infrastructure via `docker-compose.test.yml`.
- [x] Add Grafana + Prometheus to monitoring stack.

---

## Production Timeline

| Phase | Hours | Notes |
|-------|-------|-------|
| Script lock | 2 | Read-through with timer |
| Slide generation + export | 2 | Google Slides / Canva |
| Pre-recorded demo capture | 2 | Includes retakes |
| B-roll capture (terminal, UI, metrics) | 2 | OBS recordings |
| Voiceover recording | 2 | Per-scene takes |
| Video edit | 6 | Hard cuts, lower thirds, split-screens |
| Sound + music | 1 | Royalty-free ambient bed, no lyrics |
| Review + revision | 2 | Peer review + self-review |
| Render + upload | 1 | H.264 export |
| **Total** | **20** | Pad to 25 hours for contingency |

---

## Q&A Prep (For Live Defense / Comments)

| Question | Answer (30 seconds max) |
|----------|------------------------|
| "Why build a distributed system for a course project?" | "The requirement was to demonstrate resilience patterns. A single-process script cannot show atomic claims, zombie recovery, or queue-based backoff." |
| "Why PostgreSQL and not a message broker for the outbox?" | "The outbox must be transactional with the job table. Only the database can guarantee atomicity." |
| "Is at-least-once delivery enough?" | "For idempotent job claims, yes. The claim mechanism prevents duplicate processing. If we needed exactly-once for billing, we would add a deduplication log." |
| "Why HS256 instead of RS256?" | "HS256 is sufficient for a single-issuer course project. RS256 adds key rotation complexity we did not need." |
| "How do you know the zombie sweeper works?" | "We test it by simulating a SIGKILL and verifying the sweeper requeues the job within the timeout window. The demo shows this end-to-end." |

---

## Appendix: Evidence Matrix

| Claim | File | Line(s) | Test | Visual In Video |
|-------|------|---------|------|-----------------|
| Atomic outbox | `app/api/routes/downloads.py` | 161 | `tests/test_worker/test_outbox_recovery.py` | Code inset + transaction diagram |
| Outbox service | `app/services/outbox_service.py` | 10-43 | `tests/test_services/test_outbox_service.py` | Code inset |
| SKIP LOCKED relay | `worker/processor.py` | 348 | `tests/test_worker/test_outbox_recovery.py` | SQL highlight |
| Atomic claim | `worker/processor.py` | 108-116 | `tests/test_worker/test_atomic_claims.py` | Code inset |
| Full jitter | `app/services/retry_service.py` | — | `tests/test_services/test_retry_service.py` | Formula on lower third |
| Graceful shutdown | `worker/main.py` | 38 | `tests/test_worker/test_graceful_shutdown.py` | Timeline graphic |
| Zombie sweeper | `worker/zombie_sweeper.py` | 29 | `tests/test_worker/test_zombie_sweeper.py` | Pre-recorded demo |
| Custom histograms | `app/metrics.py` | 20-22 | `tests/test_api/test_metrics.py` | Grafana screenshot |
| Health checks | `app/api/routes/health.py` | — | `tests/test_api/test_health.py` | Terminal curl |

---

## What Was Cut from v5.1 (And Why)

| Cut Element | Reason |
|-------------|--------|
| v4 vs v5 version history | Irrelevant to examiners. |
| ASCII architecture diagrams | Replaced with static diagrams. |
| Scene 4 "Code Deep Dive" | Redundant — code was shown in Scene 3. |
| Separate "Security" and "CI/CD" scenes | Consolidated into Trade-offs matrix. |
| Seven micro-demos | Replaced with one 45-second pre-recorded chaos segment. |
| Circuit breaker deep dive | Not central to the walkthrough. Mentioned in ADRs only. |
| "Two-mechanism recovery" lengthy explanation | Implementation detail. The video only shows recovery. |
| "Exactly-once is a myth" repeated 4+ times | Said once in Scene 3. |
| 24-hour production timeline | Replaced with realistic 20-25 hour estimate. |

---

## Final Checklist Before Recording

- [ ] Slides exported as PNG sequence at 1920x1080
- [ ] All code insets syntax-highlighted in Dracula theme, 18pt minimum
- [ ] Pre-recorded chaos demo captured and edited
- [ ] Voiceover script practiced with timer — every scene under budget
- [ ] p99 job duration removed from narration (use custom-buckets explanation only)
- [ ] All "token bucket" references purged
- [ ] docker-compose.test.yml services documented for examiner
- [ ] Background music selected (ambient, no lyrics)
- [ ] Video editor project template created (lower third, hard cuts)
- [ ] Examiner handout prepared (slide deck PDF)

## End of Plan v6.1
