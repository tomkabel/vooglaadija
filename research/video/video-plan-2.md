# Vooglaadija: Final Course Video Production Plan

**Version:** 2.0 (Major Overhaul)  
**Project:** YouTube Link Processor  
**Course:** Junior to Senior Developer (TalTech)  
**Created:** 2026-04-15  

---

## Executive Summary

This plan produces an 8-10 minute technical presentation video for final course grading. The video tells the story of building a production-grade distributed system, with emphasis on **reliability engineering**—the patterns that separate hobby projects from systems that actually work in production.

**Core Thesis:** "Most tutorials show you how to make things work once. This project shows how to make things keep working."

---

## Part I: Strategic Foundation

### 1. The Story Angle (Why This Video Exists)

#### The Problem with Generic Project Videos

Every capstone project video makes the same claims: "We built a REST API," "We used Docker," "We wrote tests." These claims are meaningless without evidence of **engineering judgment**—the ability to make trade-offs, handle failures, and design for reliability.

#### The Vooglaadija Story: Reliability Engineering

**Angle:** This project demonstrates the shift from "it works on my machine" to "it works in production"—a core competency of senior developers.

**Three Acts:**

| Act | Focus | Evidence |
|-----|-------|----------|
| **Act 1: The Challenge** | Why YouTube downloads are hard | Rate limiting, geo-blocks, ephemeral content, format variability |
| **Act 2: The Engineering** | Production reliability patterns | Outbox pattern, atomic job claims, graceful shutdown, exponential backoff |
| **Act 3: The Real-World UX** | How users actually interact | HTMX SSE, cookie-based auth, real-time status, download expiration |

#### Unique Differentiators (vs. youtube-dl wrappers)

1. **Outbox Pattern** — Survives crashes mid-transaction
1. **Atomic Job Claims** — No double-processing in concurrent workers
1. **Graceful Shutdown** — Jobs aren't lost on deployment
1. **Exponential Backoff** — Transient failures auto-recover
1. **HTMX Real-Time** — No WebSocket complexity for simple SSE

---

### 2. Technical Accuracy Requirements

#### Outbox Pattern: The Full Story

**Why not direct Redis publishing?**

```text
Problem: Web server crashes AFTER database commit BUT BEFORE Redis publish

Naive approach: Job is lost forever (DB says "pending", Redis says "nothing")

Outbox solution:
1. Write job + outbox entry in SAME database transaction
2. Commit both atomically
3. Background process syncs outbox → Redis
4. If web server crashes, pending outbox entry survives
5. Recovery process eventually publishes the job
```

**Trade-off explained:**

- Direct Redis: Faster (no polling delay), simpler
- Outbox: Survives crashes, enables retry scheduling, supports distributed transactions
- For a download service where reliability matters, outbox wins

#### HTMX vs. React: The Full Trade-off

**Why HTMX?**

| Factor | HTMX | React/Vue |
|--------|------|-----------|
| Initial load | ~50KB (htmx.min.js) | ~150KB+ (framework) |
| Server calls | Inline (hx-get, hx-post) | fetch() + state management |
| Real-time | SSE (server-sent) | WebSocket or polling |
| Complexity | Form submissions work like HTML | Need useEffect, state, reducers |
| SEO | Works naturally | SSR required |
| **Best for** | **Form-heavy apps, dashboards** | **Highly interactive UIs, games** |

**Our use case:** User registers, logs in, pastes URL, watches status. This is form-heavy. HTMX wins on simplicity.

**Acknowledged limitation:** HTMX can't do complex client interactions. If we needed a collaborative editor, we'd use React.

---

## Part II: Pre-Production

### 3. Architecture Deep Dive (What to Show)

#### System Diagram (ASCII for planning)

```text
┌─────────────────────────────────────────────────────────────────────┐
│                         CLIENT LAYER                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐               │
│  │   Browser   │  │    curl/    │  │   Swagger   │               │
│  │   (HTMX)    │  │   API client│  │      UI     │               │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘               │
│         │                 │                 │                      │
└─────────┼─────────────────┼─────────────────┼──────────────────────┘
          │                 │                 │
          ▼                 ▼                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      FASTAPI APPLICATION                            │
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐             │
│  │  Auth Layer  │  │  Job API     │  │  SSE Stream  │             │
│  │  JWT Cookies │  │  CRUD        │  │  (15s poll)  │             │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘             │
│         │                 │                  │                       │
│  ┌──────▼─────────────────▼─────────────────▼───────┐            │
│  │              DATABASE (PostgreSQL)                  │            │
│  │  ┌─────────┐  ┌────────────┐  ┌─────────────┐     │            │
│  │  │  User   │  │ DownloadJob│  │   Outbox    │     │            │
│  │  └─────────┘  └────────────┘  └──────┬──────┘     │            │
│  └───────────────────────────────────────┼─────────────┘            │
│                                          │                           │
└──────────────────────────────────────────┼──────────────────────────┘
                                           │
                    ┌──────────────────────┼──────────────────────┐
                    │         DUAL-WRITE   │                       │
                    │  ┌───────────────────▼───────────┐            │
                    │  │  1. Redis Queue (direct)     │            │
                    │  │     - Best effort             │            │
                    │  │     - If fails → outbox       │            │
                    │  └───────────────────────────────┘            │
                    │  ┌───────────────────────────────┐             │
                    │  │  2. Outbox Table (fallback)   │             │
                    │  │     - Guaranteed               │             │
                    │  │     - Syncs every 5 min       │             │
                    │  └───────────────────────────────┘             │
                    └────────────────────────────────────────────────┘
                                           │
                                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         WORKER PROCESS                              │
│                                                                      │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐     │
│  │  Job Claim      │  │  Media Extract  │  │  Retry Handler  │     │
│  │  (FOR UPDATE    │  │  (yt-dlp)       │  │  (Exponential   │     │
│  │   SKIP LOCKED)  │  │                 │  │   Backoff)      │     │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘     │
│           │                    │                    │               │
│           └────────────────────┴────────────────────┘               │
│                                  │                                   │
│  ┌───────────────────────────────▼───────────────────────────────┐   │
│  │              GRACEFUL SHUTDOWN HANDLER                        │   │
│  │  - SIGTERM/SIGINT caught                                     │   │
│  │  - In-flight job requeued atomically                         │   │
│  │  - Partial files cleaned up                                  │   │
│  └───────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                                           │
                                           ▼
                              ┌─────────────────────┐
                              │   FILE STORAGE      │
                              │   (expires 24h)      │
                              └─────────────────────┘
```

#### Key State Machines

**Download Job States:**

```text
                    ┌──────────────────────────────────────┐
                    │                                      │
                    ▼                                      │
┌─────────┐    ┌───────────┐    ┌───────────┐    ┌─────────┴───┐
│ pending │───▶│processing │───▶│ completed │    │    failed    │
└─────────┘    └───────────┘    └───────────┘    │   (terminal) │
                   │                │            └──────────────┘
                   │                │                 ▲
                   │    ┌────────────┘                 │
                   │    │                              │
                   │    │ max_retries exceeded         │
                   │    └──────────────────────────▶───┘
                   │
                   │ retry_count < max_retries
                   │ next_retry_at = now + 2^retry_count minutes
                   ▼
              ┌─────────┐
              │ pending │ (scheduled retry)
              └─────────┘
```

**Outbox States:**

```text
┌─────────┐    ┌──────────┐    ┌────────────┐
│ pending │───▶│ enqueued │───▶│ processed │
└─────────┘    └──────────┘    └────────────┘
```

---

### 4. Scene-by-Scene Production Plan

#### Scene 1: Cold Open (0:00-0:20)

**Purpose:** Hook viewers with "what happens when things go wrong"

**Content:**

```text
[Visual: Screen recording of a simulated crash during download]

Text overlay: "What happens when your download server crashes at 2 AM?"

[Show: Terminal output showing SIGTERM received]
[Show: Job requeued automatically]
[Show: Download completes after recovery]
```

**Narration:** "Most projects show you the happy path. This project shows you what happens when everything goes wrong—and how it automatically recovers."

**B-Roll Needed:**

- Terminal with colored output (red/green for errors/success)
- Animated diagram of crash → recovery sequence

---

#### Scene 2: The Problem (0:20-0:50)

**Purpose:** Establish why this project exists

**Content:**

```text
[Show: Clipboard with YouTube URL]
[Show: Various methods people use - browser extensions, websites, CLI tools]

Text: "YouTube downloads are unreliable by nature:
- Rate limiting blocks requests
- Videos are geo-restricted
- Formats change constantly
- Servers go down mid-download"
```

**Narration:** "Building a YouTube download service isn't hard. Making one that actually works when things fail—that's the engineering challenge."

**Visual Style:** Split screen - chaotic (browser extensions, different tools) vs. clean (our unified API)

---

#### Scene 3: System Architecture (0:50-2:00)

**Purpose:** Explain the distributed architecture at a level appropriate for senior devs

**Content:**

```text
[Animated diagram: Client → API → PostgreSQL + Redis → Worker → Storage]

Three focus areas (one at a time):
```

## Area 1: The Decoupled Architecture

```text
Narration: "The API server and worker are completely independent processes.
They communicate only through PostgreSQL and Redis—no direct function calls.
This means the worker can crash, restart, or scale horizontally
without affecting the API."
```

## Area 2: The Outbox Pattern (Highlight)

```text
Narration: "When you submit a download, two things happen in one database
transaction: the job is created AND a message is queued in the outbox table.
This dual-write ensures the job is never lost—even if the process crashes
between the transaction and the queue publish."
[Show: Transaction atomicity diagram]
```

## Area 3: Worker Reliability

```text
Narration: "Workers claim jobs atomically using SELECT FOR UPDATE SKIP LOCKED.
Only one worker processes a job. On shutdown, in-flight jobs are atomically
requeued. Failed jobs retry with exponential backoff: 2, 4, 8 minutes."
[Show: Retry timing diagram]
```

---

### Scene 4: Live Demo - The Happy Path (2:00-4:00)

**Purpose:** Show real functionality working

**CRITICAL: Pre-record this entire sequence as backup. Live demo only if confident.**

**Demo Steps:**

| Step | Action | What to Show |
|------|--------|--------------|
| 1 | Open browser | Clean browser, logged out state |
| 2 | Navigate to localhost | Landing page loads |
| 3 | Click Register | Registration form appears |
| 4 | Fill form | Email + password (use <testcred@vooglaadija.local>) |
| 5 | Submit | Success redirect to downloads |
| 6 | Paste YouTube URL | <https://www.youtube.com/watch?v=dQw4w9WgXcQ> |
| 7 | Submit | Job appears in list with "pending" |
| 8 | Watch SSE | Status changes: pending → processing → completed |
| 9 | Click download | File downloads |

**Error Handling Demos (Pre-recorded alternates):**

| Scenario | What Happens | How to Show |
|----------|--------------|-------------|
| Invalid URL | Validation error on form | Real-time error message |
| Expired token | 401 → auto-redirect to login | Cookie expiry demo |
| Failed download | Status shows error message | Geo-blocked or unavailable video |

**Pre-Recorded Fallback Videos Needed:**

1. `demo_registration_success.mp4` - Full registration flow
1. `demo_download_success.mp4` - Full download with status changes
1. `demo_invalid_url.mp4` - Form validation error
1. `demo_token_refresh.mp4` - Auth refresh flow
1. `demo_download_failure.mp4` - Failed job with error display

---

#### Scene 5: API Demo (4:00-5:00)

**Purpose:** Show the REST API for developers

**Content:**

**Terminal Recording:**

```bash
# Register
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "api-test@vooglaadija.local", "password": "testpass123"}'

# Login
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "api-test@vooglaadija.local", "password": "testpass123"}'

# Create download
curl -X POST http://localhost:8000/api/v1/downloads \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'

# Check status
curl http://localhost:8000/api/v1/downloads \
  -H "Authorization: Bearer $TOKEN"
```

**Swagger UI Recording:**

- Open <http://localhost:8000/docs>
- Show authentication endpoints
- Show download endpoints
- Show response schemas

**Narration:** "For developers, we provide a full REST API with OpenAPI documentation. JWT authentication uses access/refresh token rotation stored in HttpOnly cookies—secure against XSS, protected against CSRF with our double-submit pattern."

---

#### Scene 6: Code Architecture Deep Dive (5:00-6:30)

**Purpose:** Show the actual implementation patterns

**4 Code Snippets to Highlight:**

## 1. Atomic Job Claim (worker/processor.py)

```python
# Show: How workers claim jobs without race conditions
result = await db.execute(
    update(DownloadJob)
    .where(DownloadJob.id == job_id, DownloadJob.status == "pending")
    .values(status="processing", updated_at=datetime.now(UTC))
)
claimed = result.rowcount == 1
if not claimed:
    return False  # another worker got it
```

**Narration:** "Workers atomically claim jobs. The UPDATE returns only if the job was actually pending. No locks, no race conditions."

**2. Outbox Pattern (app/services/outbox_service.py)**

```python
# Show: Same transaction, both job and outbox
db.add(job)
outbox_entry = Outbox(job_id=job.id, event_type="job_created", ...)
db.add(outbox_entry)
await db.commit()  # Both succeed or both fail
```

**Narration:** "Job and outbox entry are written in one transaction. If the commit succeeds, the job is guaranteed to be processed eventually—even if Redis is down."

## 3. Graceful Shutdown (worker/main.py)

```python
# Show: Signal handler
for sig in (signal.SIGTERM, signal.SIGINT):
    loop.add_signal_handler(sig, _signal_handler)

# Show: In-flight job handling
if shutdown_event.is_set():
    await _requeue_job(job_id, db)  # Requeue atomically
    _cleanup_downloaded_file(file_path)  # No partial files left behind
```

**Narration:** "SIGTERM triggers graceful shutdown. The worker requeues its current job atomically and cleans up any partial downloads. Zero lost work on every deployment."

## 4. SSE Real-Time Updates (app/api/routes/sse.py)

```python
# Show: State change detection
if seen_jobs.get(job_id) != f"{job_id}:{job.status}":
    yield ServerSentEvent(event="job_update", data=json.dumps({...}))
```

**Narration:** "Server-Sent Events push status changes to the browser every 15 seconds. No WebSocket complexity—just simple server push that works through proxies."

---

### Scene 7: CI/CD Pipeline (6:30-7:15)

**Purpose:** Show production-grade DevOps

**Pipeline Visualization:**

```text
workflow_dispatch → lint → type-check → unit-tests → integration-tests → security-scan → docker-build
```

**What Each Stage Proves:**

| Stage | Tool | Evidence |
|-------|------|----------|
| Lint | ruff | Code style, import order, no dead code |
| Type Check | mypy | Type annotations correct, no implicit Any |
| Unit Tests | pytest + SQLite | Business logic isolated, mocked dependencies |
| Integration Tests | pytest + PostgreSQL + Redis | Real database, real queue |
| Security Scan | bandit + safety | No known vulnerabilities, no security anti-patterns |
| Docker Build | multi-stage build | Production image < 200MB |

**Narration:** "Every commit runs six validation stages. Unit tests use SQLite for speed, integration tests spin up real PostgreSQL and Redis with health checks. Security scanning catches vulnerabilities before they reach production."

**Show:**

- GitHub Actions workflow running
- Test coverage report
- Security scan output

---

#### Scene 8: Technical Trade-offs (7:15-8:00)

**Purpose:** Demonstrate engineering judgment

#### Trade-off 1: HTMX vs. SPA Frameworks

| Consideration | HTMX (Our Choice) | React |
|---------------|-------------------|-------|
| Bundle size | 50KB | 150KB+ |
| Complexity | Forms = HTML forms | Components, state, hooks |
| Real-time | SSE | WebSocket |
| Our use case | Form submit + status polling | Not needed |

**Conclusion:** "For a download dashboard, HTMX gives us 90% of the interactivity at 10% of the complexity."

#### Trade-off 2: Outbox vs. Direct Redis

| Consideration | Outbox (Our Choice) | Direct Redis |
|---------------|---------------------|--------------|
| Reliability | Survives crashes | Loses job if crash between DB and Redis |
| Complexity | Extra table, polling | Simpler |
| Latency | Polling delay (5 min max) | Immediate |

**Conclusion:** "We're a download service. A 5-minute polling delay is acceptable; lost jobs are not."

#### Trade-off 3: SQLite Tests vs. PostgreSQL Tests

| Consideration | SQLite (Our Choice) | PostgreSQL |
|---------------|---------------------|------------|
| Speed | In-memory, instant | Docker, 2-3 sec startup |
| Parallel | File per worker | Same DB, connection pooling |
| Coverage | Faster CI | More realistic |

**Conclusion:** "Unit tests run against SQLite in memory—100 tests in 3 seconds. Integration tests use real PostgreSQL. We get both speed and fidelity."

---

#### Scene 9: Closing (8:00-9:00)

**Purpose:** Recap tangible achievements without buzzwords

**What NOT to Say:**

- ❌ "Demonstrates key skills for a senior developer"
- ❌ "Showcases system design, async programming, security patterns, DevOps"
- ❌ "Thank you for watching!"

**What TO Say:**

```text
"This project shows what happens when you design for failure:

Tangible results:
- Jobs never disappear, even when servers crash
- Failed downloads retry automatically up to 3 times  
- Deployments never lose in-flight work
- The system recovers from Redis failures transparently

Technical evidence:
- Outbox pattern with crash recovery
- Atomic job claims with FOR UPDATE SKIP LOCKED
- Graceful shutdown with SIGTERM handling
- Exponential backoff: 2, 4, 8 minute retries

The code is in the repository. The tests are automated.
The architecture is documented. The system is production-ready."
```

**Final Shot:**

```text
[Show: Repository QR code or URL]
[Show: Project logo]
[Text: "vooglaadija - A reliability engineering project"]
```

---

## Part III: Production Planning

### 5. Visual Identity

#### Color Palette

| Role | Color | Hex | Usage |
|------|-------|-----|-------|
| Primary | Deep Blue | #1E3A5F | Title cards, headers |
| Secondary | Slate Gray | #64748B | Body text, diagrams |
| Accent Success | Emerald | #10B981 | Success states, completed |
| Accent Warning | Amber | #F59E0B | Processing, pending |
| Accent Error | Rose | #EF4444 | Failed, errors |
| Background | Off-White | #F8FAFC | Slide backgrounds |
| Code Background | Dark | #1E293B | Terminal, code blocks |

#### Typography

| Element | Font | Size | Weight |
|---------|------|------|--------|
| Title | Inter | 48px | 700 |
| Subtitle | Inter | 32px | 600 |
| Body | Inter | 24px | 400 |
| Code | JetBrains Mono | 20px | 400 |
| Caption | Inter | 18px | 400 |

#### Animation Style

- **Transitions:** Fade (300ms ease-out) between scenes
- **Diagram animations:** Sequential reveal, left-to-right flow
- **Code reveals:** Typewriter effect for key lines
- **No:** Bouncing, spinning, or distracting effects

### 6. B-Roll Requirements

#### Required B-Roll

| B-Roll Type | Duration | Purpose |
|-------------|----------|---------|
| Terminal with colored output | 30s | Architecture explanations |
| Docker containers running | 15s | Infrastructure setup |
| GitHub Actions workflow | 20s | CI/CD demo |
| Swagger UI browsing | 15s | API demo |
| Database query results | 10s | Outbox pattern visualization |

#### B-Roll Sources

1. **Self-recorded:** Terminal sessions, browser demos, code editor
1. **Generated:** Architecture diagrams (Mermaid or Excalidraw)
1. **Stock (if needed):** Server room, cable management (rarely needed)

#### Animation Specifications

**Architecture Diagram Animation (Scene 3):**

```text
Frame 1: [Client icon] appears (0.5s)
Frame 2: Arrow → API (0.5s)
Frame 3: API icon (0.5s)
Frame 4: Branch to PostgreSQL + Redis (0.5s)
Frame 5: Arrow → Worker (0.5s)
Frame 6: Arrow → Storage (0.5s)
Total: 3 seconds for main diagram
```

**State Machine Animation (Scene 3):**

```text
pending → processing: Arrow with "job claimed" label (0.3s)
processing → completed: Arrow with "success" label (green) (0.3s)
processing → pending: Arrow with "retry scheduled" label (amber) (0.3s)
processing → failed: Arrow with "max retries" label (red) (0.3s)
```

### 7. Music & Audio

#### Music Licensing

| Source | License | Attribution Required | Cost |
|--------|---------|---------------------|------|
| Uppbeat | Free with attribution | Yes | $0 |
| Pixabay | Royalty-free | No | $0 |
| Free Music Archive | Varies by track | Yes | $0 |
| Artlist | Commercial license | No | ~$15/month |

**Recommended:** Use Uppbeat (search: "technology", "uplifting", "corporate")

**Tracks to Evaluate:**

- "Digital Background" by Lesfw - Technology feel, not intrusive
- "Inspire" by Lesfw - Good for transitions
- "Coding Session" by John K. - Perfect for tech demos

#### Audio Recording Requirements

| Element | Equipment | Notes |
|---------|-----------|-------|
| Voiceover | Condenser mic (Blue Yeti or similar) | -20dB peak, quiet room |
| Screen audio | System audio (Blackhole 2ch) | Capture terminal beeps |
| Fallback | Phone recording | Only if mic fails |

#### Audio Levels

| Element | Target Level |
|---------|-------------|
| Voiceover | -20dB LUFS |
| Music (background) | -30dB LUFS (12dB below voice) |
| SFX | -25dB LUFS |
| Final mix | -16dB LUFS (YouTube standard) |

---

### 8. Demo Environment Setup

#### Pre-Production Checklist

```bash
# 1. Start all services
docker-compose up -d

# 2. Verify health
curl http://localhost:8000/api/v1/health
curl http://localhost:8081/health  # Worker health

# 3. Create demo user
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "demo@vooglaadija.local", "password": "d3m0p4ss!"}'

# 4. Login to get tokens
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "demo@vooglaadija.local", "password": "d3m0p4ss!"}'

# 5. Create sample downloads
# One pending, one completed, one failed
```

#### Demo Scenarios

## Scenario A: Success Path

```python
url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"  # Never fails
expected_duration = "2-5 minutes"
```

## Scenario B: Transient Failure (Retry Demo)

```python
# Use a video that occasionally fails
url = "https://www.youtube.com/watch?v= deliberately_invalid"
expected = "pending → processing → failed (after retries)"
```

## Scenario C: Format Error (Terminal Failure)

```python
# Video with unsupported format
url = "https://www.youtube.com/watch?v=ge62FatYN0Y"  # Age-restricted
expected = "Failed immediately, no retry"
```

### Contingency Plans

| Failure | Mitigation |
|---------|-----------|
| YouTube blocks request | Use local video URL or mock |
| Redis connection fails | Show outbox recovery |
| PostgreSQL connection fails | Show graceful error handling |
| SSE not updating | Refresh browser, show code |
| Download takes too long | Pre-record completed download |

---

### 9. Realistic Timeline

#### Phase Breakdown

| Phase | Initial | With Buffer | Buffer Reason |
|-------|---------|-------------|---------------|
| Script finalization | 2h | 3h | Multiple rewrites |
| Environment setup | 1h | 2h | Docker issues |
| B-Roll recording | 2h | 4h | Retakes, errors |
| Live demo recording | 2h | 4h | Demo failures |
| Pre-recorded fallback | 2h | 3h | Multiple scenarios |
| Voiceover recording | 1h | 2h | Mistakes, tone |
| Scene animation | 2h | 4h | Revisions |
| Video editing | 4h | 8h | Transitions, timing |
| Audio mixing | 1h | 2h | Level adjustments |
| Color grading | 1h | 2h | Consistency |
| Final review | 2h | 4h | Stakeholder feedback |
| **Total** | **20h** | **38h** | 90% buffer |

#### Daily Schedule (if 4h/day)

| Day | Focus | Deliverable |
|-----|-------|-------------|
| Day 1 | Script + storyboard | Final script approved |
| Day 2 | Environment + B-Roll | Raw B-Roll footage |
| Day 3 | Live demos | Demo recordings |
| Day 4 | Fallback recordings | All scenarios covered |
| Day 5 | Voiceover | Clean VO tracks |
| Day 6 | Animations | Scene animations ready |
| Day 7-8 | Editing | Rough cut |
| Day 9 | Editing + audio | Picture lock |
| Day 10 | Review + export | Final delivery |

## Total: 10 days at 4 hours/day = 40 hours

---

## Part IV: Deliverables

### 10. Final Video Specifications

| Property | Value |
|----------|-------|
| Duration | 8-10 minutes |
| Resolution | 1920x1080 (1080p) |
| Frame rate | 30fps |
| Format | MP4 (H.264) |
| File size | < 1GB (target 500MB) |
| Aspect ratio | 16:9 |
| Audio | AAC 48kHz stereo |
| Color space | sRGB |
| Subtitles | English (for accessibility) |

### 11. Supporting Materials

| Material | Purpose | Format |
|----------|---------|--------|
| Script | Naration guide | PDF + Google Docs |
| Storyboard | Visual plan | PDF or Figma |
| B-Roll library | Raw footage | Original recordings |
| Fallback videos | Error handling | MP4 collection |
| Architecture diagrams | Source files | Mermaid/Excalidraw |
| Music track | Audio file | MP3 (licensed) |

---

## Part V: Risk Mitigation

### 12. Demo Failure Protocols

#### Level 1: Minor Glitch

**Example:** Status doesn't update immediately  
**Protocol:** "Let me refresh... there we go. The SSE connection polls every 15 seconds, so there's a small delay."

#### Level 2: Significant Delay

**Example:** Download taking too long  
**Protocol:** Skip to pre-recorded completion, explain "This is what it looks like when it works"

#### Level 3: Complete Failure

**Example:** Service down  
**Protocol:** Switch immediately to pre-recorded fallback video. Show same flow from recording.

#### Level 4: Systematic Issue

**Example:** Docker compose not working  
**Protocol:** Show code, show architecture diagram, show test results instead of live demo.

### 13. Quality Gates

Before declaring video complete:

- [ ] All 5 demo scenarios filmed (success + 4 error cases)
- [ ] All B-Roll recorded and reviewed
- [ ] Voiceover recorded with clean audio
- [ ] All animations rendered
- [ ] Music licensed and mixed
- [ ] Color graded for consistency
- [ ] Subtitles synced
- [ ] Tested on target platform (YouTube/Vimeo)
- [ ] Playback verified at 100% and 50% quality
- [ ] File size verified < 1GB

---

## Appendix A: Code Snippet Locations

| Pattern | File | Lines |
|---------|------|-------|
| Atomic job claim | worker/processor.py | ~80-95 |
| Outbox write | app/services/outbox_service.py | ~15-40 |
| Graceful shutdown | worker/main.py | ~45-90 |
| SSE implementation | app/api/routes/sse.py | Full file |
| CSRF protection | app/api/routes/web.py | ~25-80 |
| JWT creation | app/auth.py | ~50-80 |
| Token refresh | app/api/routes/auth.py | ~60-90 |

---

## Appendix B: Key Architecture Decisions

| Decision | Alternative | Trade-off | Reference |
|----------|-------------|-----------|-----------|
| Outbox pattern | Direct Redis | Reliability vs. latency | Scene 3 |
| HTMX frontend | React/Vue SPA | Simplicity vs. interactivity | Scene 8 |
| SQLite for tests | PostgreSQL for all | Speed vs. realism | Scene 7 |
| SSE for updates | WebSocket | Simplicity vs. bidirectional | Scene 4 |
| Redis BRPOP | Redis PUB/SUB | Blocking vs. push | Scene 3 |

---

## Appendix C: Course Requirement Mapping

| Course Topic | Video Coverage |
|--------------|----------------|
| Practical software development | Live demo of working system |
| Teamwork | N/A (individual project) |
| Code quality | CI/CD pipeline, linting, type checking |
| Testing | Unit + integration test demonstration |
| CI/CD | Full GitHub Actions workflow shown |
| Documentation | README, API docs, code comments |
| Agile/SCRUM | N/A |
| Version control | GitHub repository |

---

## End of Plan v2.0
