# TOP1 Technical Demo Guide — Vooglaadija

**Author:** Senior AI & Security Architecture Review **Date:** 2026-05-06 **Context:** Comprehensive
run-of-show for the TOP1 nomination demo — covering chaos injection, Grafana observability,
cinematography, and risk-mitigated live execution.

---

## Table of Contents

- [1. Philosophy: Engineering Theater](#1-philosophy-engineering-theater)
- [2. Pre-Flight Verification Checklist](#2-pre-flight-verification-checklist)
- [3. Demo Stack Architecture](#3-demo-stack-architecture)
- [4. Chaos Injection API — Deep Dive](#4-chaos-injection-api--deep-dive)
- [5. Grafana Dashboard — UI/UX & Configuration](#5-grafana-dashboard--uix--configuration)
- [6. Cinematography & Framing Guide](#6-cinematography--framing-guide)
- [7. Run of Show — Timed Sequence](#7-run-of-show--timed-sequence)
- [8. Risk Matrix & Backup Plans](#8-risk-matrix--backup-plans)
- [9. Rehearsal Protocol](#9-rehearsal-protocol)
- [10. Post-Demo Teardown](#10-post-demo-teardown)

---

## 1. Philosophy: Engineering Theater

The demo is not a product pitch. It is an **architectural autopsy performed live**. The audience —
instructors evaluating senior-level engineering — has seen 20+ teams demo happy-path applications.
None demonstrated a system _surviving its own destruction_.

**Core principle:** Make the invisible visible. The circuit breaker, zombie sweeper, transactional
outbox, and retry chain are real production code paths that operate silently. The Chaos Injection
API surfaces them into a 4-panel Grafana dashboard that tells one story in three colors: **GREEN →
RED → GREEN**.

**What we never do:**

- Fake a failure with a video. In a senior course, faking = failing.
- Touch the Docker daemon during the live demo. Unpredictable restart times kill timing.
- Show more than 4 Grafana panels. Cognitive load is the enemy of comprehension.
- Wing the timing. The script is verbatim, rehearsed 8 times.

**What we always do:**

- Trigger real production code paths through the Chaos Injection API.
- Keep the narrative simple: crash → recover gracefully.
- Use massive, color-coded Grafana panels readable from the back row.
- Pre-seed demo data so no live download is required for the happy path.

---

## 2. Pre-Flight Verification Checklist

Execute this checklist **in order** before every rehearsal and before the final demo. No exceptions.

### 2.1 Environment Sanity

```bash
# 1. Verify Docker daemon is healthy
docker info > /dev/null 2>&1 || { echo "DOCKER DOWN"; exit 1; }

# 2. Clean any stale containers from previous runs
docker compose -f docker-compose.yml -f docker-compose.demo.yml down --remove-orphans

# 3. Bring up full demo stack
docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d

# 4. Wait for all 9 services to be healthy (30s grace)
sleep 30
docker compose -f docker-compose.yml -f docker-compose.demo.yml ps
```

### 2.2 Service Health Verification

```bash
# API health (liveness)
curl -s http://localhost:8000/health | jq .status

# API readiness (DB + Redis)
curl -s http://localhost:8000/health/ready | jq .

# Worker health
curl -s http://localhost:8082/health

# Prometheus — verify ytprocessor target is UP
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | select(.labels.job=="ytprocessor") | .health'

# Grafana — verify datasource connectivity
# Browse to http://localhost:3000 → should auto-redirect to resilience dashboard
```

### 2.3 Demo-Specific Verification

```bash
# 5. Reset all chaos flags (MUST BE FIRST ACTION IN DEMO)
curl -s http://localhost:8000/api/v1/chaos/status | jq .

# 6. Verify demo login works
curl -v http://localhost:8000/web/demo-login 2>&1 | grep -E "Set-Cookie|Location"

# 7. Verify pre-seeded demo account has 8 pending jobs
#    (Log in via browser at /web/demo-login — jobs are primed live on login)
#    (Check /web/downloads shows 8 entries from multiple platforms)
#    Jobs transition pending→processing→completed in real-time via SSE

# 8. Test each chaos scenario individually with 5s duration:
for scenario in circuit_breaker_open worker_crash db_failover throttle_spike; do
  echo "Testing: $scenario"
  curl -s -X POST http://localhost:8000/api/v1/chaos/inject \
    -H 'Content-Type: application/json' \
    -H 'X-CSRF-Token: <CSRF_TOKEN>' \
    -b 'csrf_token=<VALUE>' \
    -d "{\"scenario\":\"$scenario\",\"duration_seconds\":5}"
  sleep 6
  curl -s http://localhost:8000/api/v1/chaos/status | jq .
done

# 9. Reset everything after testing
curl -s -X POST http://localhost:8000/api/v1/chaos/reset \
  -H 'X-CSRF-Token: <CSRF_TOKEN>' \
  -b 'csrf_token=<VALUE>'
```

### 2.4 Projector & Visual Verification

```text
□ Grafana dashboard loaded on external monitor at 1920×1080
□ All 4 panel titles readable from 5 meters (48pt+ font size)
□ Red (#E02F44) / Green (#73BF69) / Yellow (#FADE2A) distinguishable
□ No panel shows "No data" — all Prometheus queries returning values
□ Browser zoom at 100% (not 90%, not 110%)
□ Dark theme enabled (GF_USERS_DEFAULT_THEME: dark)
□ Grafana time range set to "Last 5 minutes" with 10s auto-refresh
```

---

## 3. Demo Stack Architecture

### 3.1 Services in Play (9 containers)

```text
┌─────────────────────────────────────────────────────────┐
│                    DEMO STACK                            │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐             │
│  │  nginx   │  │  Swagger │  │  Grafana   │  :3000      │
│  │  :80     │  │  :8081   │  │  :3000     │             │
│  └────┬─────┘  └──────────┘  └─────┬──────┘             │
│       │                            │                    │
│  ┌────▼─────┐                 ┌────▼──────┐             │
│  │  FastAPI  │◄─── metrics ───│ Prometheus │ :9090      │
│  │  :8000    │                │ :9090      │             │
│  └────┬─────┘                 └────────────┘             │
│       │                                                 │
│  ┌────▼─────┐  ┌──────────┐                             │
│  │PostgreSQL│  │  Redis   │                             │
│  │ :5432    │  │  :6379   │                             │
│  └──────────┘  └────┬─────┘                             │
│                      │                                  │
│               ┌──────▼──────┐                           │
│               │   Worker    │                           │
│               │   :8082     │                           │
│               └─────────────┘                           │
│                                                         │
│  ┌──────────────────┐  ┌──────────────────┐             │
│  │ seed-demo-data   │  │ OTel Collector   │             │
│  │ (run once, exit) │  │ :4317            │             │
│  └──────────────────┘  └──────────────────┘             │
└─────────────────────────────────────────────────────────┘
```

### 3.2 Key Configuration Differences from Production

| Setting                     | Production | Demo                          |
| --------------------------- | ---------- | ----------------------------- |
| `FEATURE_CHAOS_API_ENABLED` | `false`    | `true`                        |
| Grafana anonymous auth      | Disabled   | Enabled (Viewer role)         |
| Grafana home dashboard      | Default    | `vooglaadija-chaos-demo.json` |
| Prometheus retention        | 15d        | 2h                            |
| CSRF enforcement            | Enforced   | Enforced (same code path)     |

### 3.3 Feature Flag Gating

The chaos API router is physically unreachable in production. The gate is at the framework level,
not at the auth level:

```python
# app/api/routes/chaos.py
def _require_feature_flag():
    if not settings.feature_chaos_api_enabled:
        raise HTTPException(status_code=404, detail="Not Found")
```

Production `docker-compose.production.yml` never sets `FEATURE_CHAOS_API_ENABLED=true`. The route
returns 404 for all HTTP verbs — it doesn't exist as far as an attacker is concerned.

---

## 4. Chaos Injection API — Deep Dive

### 4.1 Architecture

The Chaos Injection API sets Redis keys with TTLs. The existing service layer checks these keys
before executing real operations. **No code paths are mocked.** The circuit breaker state machine,
zombie sweeper, and retry chain are the same code that runs in production.

```text
POST /api/v1/chaos/inject  ──►  Redis SET chaos:circuit_breaker_open "1" EX 30
                                      │
                                      ▼
Worker claims job ──► circuit_breaker.py checks Redis ──► FORCES OPEN state
                                      │
                                      ▼
                              Grafana gauge updates ──► Panel turns RED
```

### 4.2 Scenario Reference

| Scenario Key           | Redis Key                    | Effect                                                                                                                                | Grafana Signal                                  |
| ---------------------- | ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| `circuit_breaker_open` | `chaos:circuit_breaker_open` | Forces circuit breaker to OPEN. All extraction requests fail immediately with 503.                                                    | CB State panel: GREEN→RED                       |
| `worker_crash`         | `chaos:worker_crash`         | Sets the current in-flight job to "orphaned" (stuck in `processing`). Zombie sweeper detects and reclaims it on next tick.            | Queue Depth spikes, Recovery counter increments |
| `db_failover`          | `chaos:db_failover`          | Raises `OperationalError` after worker claims a job but before processing. Retry chain fires with exponential backoff + jitter.       | Error Rate spikes, Recovery counter increments  |
| `throttle_spike`       | `chaos:throttle_spike`       | Pre-populates Redis sorted set with 15 synthetic 429 events in a 30s sliding window. AI predictor scores ≥0.7 → pre-throttle warning. | Risk Score gauge: 0→1.0                         |

### 4.3 Chaos Lab UI (`/web/chaos-lab`)

The chaos-lab page is a hidden HTMX-powered control panel. It is **not linked from any navigation
element** — accessible only by direct URL. This prevents accidental discovery during the demo while
keeping it one `Ctrl+T` away for the presenter.

**Layout:** 2×2 grid of scenario buttons + reset button + auto-polling status panel (5s interval).

**Button behavior:** Each button fires an HTMX POST to `/api/v1/chaos/inject` with the CSRF token
embedded in the page. The response renders inline in the `#inject-result` div. No page reload. No
visual disruption.

**Demo workflow:**

1. Before demo: Open `http://localhost:8000/web/chaos-lab` in a background tab (logged in as demo
   user).
1. During demo: `Alt+Tab` → click scenario button → `Alt+Tab` back to Grafana.
1. Reset step: Click "RESET ALL FLAGS" at the start of every demo run.

### 4.4 CSRF Handling for Chaos Endpoints

The chaos endpoints require CSRF validation even in the demo. The chaos-lab page embeds the CSRF
token via Jinja2 template:

```html
hx-headers='{"Content-Type": "application/json", "X-CSRF-Token": "{{ csrf_token }}"}'
```

**Backup:** If CSRF fails during the demo, the fallback is to use `curl` from a terminal
(pre-authenticated cookie jar). This is a 5-second recovery.

---

## 5. Grafana Dashboard — UI/UX & Configuration

### 5.1 The 4-Panel Layout

```text
┌────────────────────────────────────────────────────────────┐
│  PANEL 1: CIRCUIT BREAKER STATE          │  PANEL 2: QUEUE DEPTH          │
│  Stat panel — GREEN/RED/YELLOW           │  Time series — line chart      │
│  48pt value, 48pt title                  │  Spikes when worker is "down"  │
│  "CLOSED" / "OPEN" / "HALF-OPEN"        │  ytprocessor_queue_depth        │
├────────────────────────────────────────────────────────────┤
│  PANEL 3: ERROR RATE (5xx + 429)          │  PANEL 4: RECOVERY EVENTS       │
│  Time series — bar chart                 │  Stat panel — counter           │
│  rate(ytprocessor_http_requests_total...) │  ytprocessor_recoveries_total   │
│  Spikes on 429 / DB fail injection       │  Background turns GREEN on inc  │
└────────────────────────────────────────────────────────────┘
```

### 5.2 Design Decisions

**Why 4 panels, not 10:** The human eye can track ~4 discrete visual elements simultaneously during
a live presentation. More panels = fragmented attention = lost narrative.

**Why massive fonts (48pt titles, 48pt values):** Projectors in conference rooms typically resolve
at 1920×1080. At 5 meters viewing distance, anything below 24pt is illegible. 48pt ensures the back
row sees the state change.

**Why only 3 colors:**

- GREEN (`#73BF69`) = healthy / recovered
- RED (`#E02F44`) = failure / degraded
- YELLOW (`#FADE2A`) = transitional / HALF-OPEN

No blue, no purple, no orange. The story is binary at heart: broken → fixed. Yellow exists only to
show the system _in the process of healing itself_.

**Why dark theme:**

- Red/green contrast is maximized on dark backgrounds.
- Dark backgrounds reduce projector bloom (white backgrounds wash out on older projectors).
- Consistent with the "engineering at 3 AM" aesthetic.

### 5.3 Panel 1: Circuit Breaker State (Stat Panel)

```text
Panel type:        Stat
Datasource:        Prometheus
Query:             ytprocessor_circuit_breaker_state{service="youtube_api"}
Value mappings:    0 → CLOSED (GREEN), 1 → OPEN (RED), 2 → HALF-OPEN (YELLOW)
Thresholds:        0 (green), 1 (red), 2 (yellow)
Color mode:        Background (entire panel changes color)
Text mode:         Value and name
Value font:        48pt
Title font:        48pt
```

**Why background color mode:** A stat value changing from "CLOSED" to "OPEN" is subtle. The entire
panel turning from green to red is visible from across a lecture hall. Peripheral vision catches
color faster than text.

### 5.4 Panel 2: Queue Depth (Time Series)

```text
Panel type:        Time series (line)
Datasource:        Prometheus
Query:             ytprocessor_queue_depth
Legend:            Hidden (single line, no legend needed)
Line width:        3px
Fill opacity:      15%
Y-axis:            Auto
```

**Why line chart over stat:** Queue depth is meaningful as a _trajectory_, not a point value. The
audience needs to see it spike and then drain back to baseline. A stat panel would obscure the
recovery arc.

### 5.5 Panel 3: Error Rate (Time Series — Bar)

```text
Panel type:        Time series (bars)
Datasource:        Prometheus
Query:             rate(ytprocessor_http_requests_total{status_code=~"5..|429"}[1m])
Legend:            Hidden
Bar alignment:     Center
Fill opacity:      80%
Color:             Red (#E02F44)
```

**Why bars over line:** Errors are discrete events. Bars communicate "something broke HERE" better
than a continuous line which implies a gradual degradation.

### 5.6 Panel 4: Recovery Events (Stat)

```text
Panel type:        Stat
Datasource:        Prometheus
Query:             ytprocessor_recoveries_total
Value font:        48pt
Title font:        48pt
Color:             Green (#73BF69)
Thresholds:        0 (base green)
Color mode:        Background (entire panel turns green on change)
```

**Why a counter:** Recovery is the payoff. The number going up while the other panels return to
green is the visual climax of the demo. A simple incrementing counter is immediately understandable
— no interpretation required.

### 5.7 Dashboard Configuration in `docker-compose.demo.yml`

```yaml
grafana:
  environment:
    GF_AUTH_ANONYMOUS_ENABLED: "true"
    GF_AUTH_ANONYMOUS_ORG_ROLE: Viewer
    GF_USERS_DEFAULT_THEME: dark
    GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH: /etc/grafana/provisioning/dashboards/vooglaadija-chaos-demo.json
```

The dashboard is the Grafana **home dashboard**. Navigating to `http://localhost:3000` loads it
immediately — no clicking through folder trees during the demo.

---

## 6. Cinematography & Framing Guide

### 6.1 Screen Real Estate Map

For the video recording, divide the 1920×1080 canvas into three zones:

```text
┌─────────────────────┬──────────────────────────┐
│                     │                          │
│   ZONE A            │   ZONE B                 │
│   TERMINAL /        │   GRAFANA                │
│   BROWSER           │   DASHBOARD              │
│   (happy path)      │   (chaos response)       │
│   40% width         │   60% width              │
│                     │                          │
├─────────────────────┴──────────────────────────┤
│   ZONE C: PRESENTER (picture-in-picture)       │
│   Bottom-right, 25% scale, no background       │
└────────────────────────────────────────────────┘
```

**During the happy path (0:00–0:50):** Zone A dominates. The browser fills the screen showing the
Guest Demo login → paste URL → download flow. Grafana is visible but secondary (minimized or
behind).

**During chaos injection (0:50–1:30):** Zone B takes over. Grafana goes full-width. The presenter's
cursor moves deliberately between panels. No frantic mouse movement — each motion has intent.

**During recovery (1:30–2:00):** Zone B remains primary. The camera holds on the Grafana panels as
they transition RED→GREEN. This is the money shot. No cuts, no zooms, no commentary overlay — let
the dashboard speak.

**During the flex (2:00–2:30):** Split between browser (job list scroll) and Grafana (steady green).

**During close (2:30–3:00):** Full presenter frame or slide. Grafana fades to background.

### 6.2 Camera & Recording Setup

| Parameter        | Recommendation                                               |
| ---------------- | ------------------------------------------------------------ |
| Screen recording | OBS Studio, 1920×1080 @ 30fps, NVENC H.264                   |
| Presenter camera | Logitech C920 or better, 1080p, eye-level                    |
| Microphone       | Condenser mic (Blue Yeti / Rode NT-USB), cardioid pattern    |
| Room             | Treated for echo — curtains, rugs, soft furniture            |
| Lighting         | Key light at 45° left, fill light at 30° right, no backlight |
| Audio levels     | Peak -3dB, average -18dB, noise gate at -50dB                |

### 6.3 Cursor Presence & Movement

- **Cursor must be visible at all times.** The audience needs to follow what the presenter is
  clicking.
- **Enlarge cursor to 150%** in OS accessibility settings. Standard cursor size is invisible on
  projectors.
- **No circular mouse movements while speaking.** Parkinson's-style cursor circling is the #1
  amateur tell in demo videos. Move the cursor to the target, stop, click.
- **Use a mouse, not a trackpad.** Trackpad precision on a projector is unreliable.

### 6.4 Browser Configuration

```text
□ Browser: Ungoogled Chromium or Firefox (clean profile, no extensions visible)
□ Bookmark bar: Hidden
□ Default zoom: 100%
□ Theme: Dark (matches Grafana)
□ Tab strip: Hidden (F11 fullscreen mode for Grafana)
□ Password manager prompts: DISABLED (auto-fill popups ruin recordings)
□ Notifications: DO NOT DISTURB mode
```

### 6.5 Terminal Configuration (if used for backup)

```text
□ Font: JetBrains Mono, 16pt (minimum — readable on projector)
□ Color scheme: Solarized Dark or Nord
□ Window opacity: 90% (slight transparency looks professional, not gimmicky)
□ Prompt: Minimal — `❯` or `$` only (no full path, no git branch)
□ Scrollback: Clean terminal before recording starts
```

---

## 7. Run of Show — Timed Sequence

**Total slot:** 3 minutes (180 seconds) **Script duration:** 2:45 (15-second buffer) **Presenter:**
Tom Kristian Abel

### 7.1 Pre-Show (5 minutes before)

1. Terminal 1: `docker compose -f docker-compose.yml -f docker-compose.demo.yml ps` — verify all
   services `Up (healthy)`.
1. Terminal 2: `curl -s -X POST http://localhost:8000/api/v1/chaos/reset` — clear all stale chaos
   flags.
1. Browser Tab 1: Grafana at `http://localhost:3000` — verify all 4 panels showing data, all GREEN.
1. Browser Tab 2: Chaos Lab at `http://localhost:8000/web/chaos-lab` — logged in as demo user,
   scrolled so all 4 buttons are visible.
1. Browser Tab 3: Login page at `http://localhost:8000/web/login` — Guest Demo button visible.
1. OBS: Verify scene is capturing correct window. Verify audio levels.
1. Water nearby. Deep breath.

### 7.2 Timed Sequence

| Time      | Action                                                                        | Visual                                                                            | Audio                                                                         |
| --------- | ----------------------------------------------------------------------------- | --------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| **-0:05** | OBS recording started. Presenter centered.                                    | Presenter frame.                                                                  | Silence.                                                                      |
| **0:00**  | **HOOK.** Eye contact with camera.                                            | Presenter full-frame or split with Grafana.                                       | "Meie esimene prototüüp jooksis testi ajal kokku..."                          |
| **0:10**  | **PROBLEM.** Gesture toward screen.                                           | Browser showing YouTube 429 error or generic downloader crash.                    | "YouTube rate-limits aggressively..."                                         |
| **0:20**  | **HAPPY PATH.** Click Guest Demo button.                                      | Browser — login page → redirect to /web/downloads. Paste URL. Download completes. | "Vooglaadija. Sisesta URL, saad faili..."                                     |
| **0:35**  | Scroll job list showing 6 platforms.                                          | Browser — /web/downloads page with pre-seeded jobs.                               | "Kuus platvormi. Üks süsteem."                                                |
| **0:50**  | **CHAOS TRANSITION.** Alt+Tab to Chaos Lab tab.                               | Split screen: Chaos Lab buttons visible.                                          | "Aga tegelik küsimus: mis juhtub kui asjad lagunevad?"                        |
| **0:55**  | Click "SIMULATE YOUTUBE 429"                                                  | Button click visible. Result appears inline.                                      | "Ma just simuleerisin YouTube rate-limiti..."                                 |
| **1:00**  | Alt+Tab to Grafana. Point to Panel 1.                                         | Grafana fullscreen. Panel 1 turns GREEN→RED.                                      | "Circuit breaker avaneb..."                                                   |
| **1:10**  | Alt+Tab back to Chaos Lab. Click "SIMULATE WORKER CRASH". Alt+Tab to Grafana. | Panel 2 (Queue Depth) spikes.                                                     | "Nüüd tappisin workeri keset tööd..."                                         |
| **1:25**  | Point to Panel 4 (Recovery Events) — counter increments.                      | Panel 4 counter increases.                                                        | "Zombie sweeper tuvastab orvuks jäänud jobi sekunditega."                     |
| **1:30**  | **RECOVERY.** Hold on Grafana. Let panels return to GREEN.                    | Panels 1 and 2 return GREEN. Panel 4 higher than before.                          | "Ja nüüd — automaatne taastumine..."                                          |
| **1:50**  | Point to Panel 1 (GREEN). Then Panel 4 (higher counter).                      | Full Grafana. All green. Counter visibly higher.                                  | "Circuit breaker sulgub. Job on tagasi nõutud. Kasutaja ei näe mitte midagi." |
| **2:00**  | **FLEX.** Switch to browser job list. Scroll through completed jobs.          | Browser — job list with 6 platforms, all "completed".                             | "Kuus platvormi. 12 000 rida teste..."                                        |
| **2:15**  | Point to Grafana (still visible, all green).                                  | Split browser + Grafana.                                                          | "AI-põhine throttle prediction, mis ennetavalt rotab endpoint'e."             |
| **2:30**  | **CLOSE.** Return to presenter frame. Hold.                                   | Presenter full-frame. Grafana in background, all green.                           | "Üks `docker compose up` käsk. Seitse teenust. Null konfiguratsiooni."        |
| **2:40**  | Pause. Eye contact.                                                           | Presenter.                                                                        | "Me ehitasime selle, et ta jääks ellu seal, kus teised surevad."              |
| **2:48**  | Final beat.                                                                   | Presenter. Grafana behind — panels steady GREEN.                                  | "Vooglaadija."                                                                |
| **2:50**  | Hold 3 seconds. OBS fade to black.                                            | Fade to black.                                                                    | Silence.                                                                      |

### 7.3 Post-Show (immediate)

```bash
# Reset chaos flags so the system is clean for any Q&A demo
curl -s -X POST http://localhost:8000/api/v1/chaos/reset
```

---

## 8. Risk Matrix & Backup Plans

### 8.1 Risk Matrix

| #   | Risk                                         | Likelihood | Impact   | Mitigation                                                                                                 | Backup                                                                                                       |
| --- | -------------------------------------------- | ---------- | -------- | ---------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| R1  | Chaos API doesn't trigger                    | Low        | High     | Test all 3 scenarios in isolation <1h before demo                                                          | Use `curl` from terminal with pre-saved cookie jar                                                           |
| R2  | Grafana shows "No data"                      | Medium     | Critical | Pre-load dashboard, verify Prometheus targets during pre-flight                                            | Screenshot of dashboard from rehearsal (last resort — breaks "live" authenticity but preserves message)      |
| R3  | Circuit breaker already OPEN from prior test | Medium     | Medium   | **ALWAYS run `/api/v1/chaos/reset` as first demo action**                                                  | Wait for TTL expiry (30s) — script accounts for this in timing buffer                                        |
| R4  | Demo button auth fails (cookie issue)        | Low        | High     | Pre-verify with `curl -v` during pre-flight                                                                | Type demo credentials manually: `demo@vooglaadija.io` / `demo123`. 5-second recovery.                        |
| R5  | Docker daemon hangs                          | Low        | Critical | **No Docker commands in the live demo.** All chaos is API-driven.                                          | If Docker is needed: `docker compose restart <service>` — but this should never happen                       |
| R6  | yt-dlp fails on non-YouTube URL live         | Low        | Medium   | **Never do a live download during the demo.** All jobs are pre-seeded. Happy path shows completed jobs.    | If forced: only use YouTube URLs verified <1h before demo                                                    |
| R7  | Projector makes Grafana unreadable           | Medium     | High     | 48pt fonts, 3 colors max, tested on actual projector during rehearsal                                      | Verbal narration of state changes ("Circuit breaker just opened — that red panel means...")                  |
| R8  | 3-minute time limit enforced strictly        | High       | Medium   | Script timed at 2:45 with 15s buffer. Rehearse 8x with stopwatch.                                          | If running long: cut the Flex section (2:00–2:30) — the recovery is the story                                |
| R9  | Internet fails                               | Low        | Critical | No live downloads. Grafana and Prometheus are local. Chaos API is local. The demo is fully self-contained. | If internet is needed for some reason: mobile hotspot pre-configured                                         |
| R10 | Presenter loses place in script              | Medium     | Low      | Script printed on paper, placed below camera at eye-level. Key phrases memorized.                          | Improvise around the 3 beats: crash → recover → close. The Grafana panels tell the story even without words. |
| R11 | CSRF token expired between chaos clicks      | Low        | Medium   | Chaos-lab page auto-refreshes CSRF token on each poll (5s interval)                                        | Fall back to curl with fresh cookie jar                                                                      |

### 8.2 The "Break Glass" Backup

If **everything fails** — Grafana is blank, chaos API returns 500, the demo login redirects to a
404:

1. **Do not panic.** The audience doesn't know what success looks like. You define the frame.
1. **Pivot to architectural narration.** Talk through the circuit breaker diagram (Slide 2 —
   Architecture). Explain the state machine transitions. Walk through the zombie sweeper logic
   verbally.
1. **Show the code.** Open `services/circuit_breaker.py` and walk through the `_transition_to`
   method. Code is truth. Code cannot fail to render.
1. **Acknowledge the failure.** "See on täpselt see, millest ma räägin — süsteemid lagunevad. Aga
   vaadake, kuidas me sellega toime tuleme." (This is exactly what I'm talking about — systems
   break. But watch how we handle it.)

This meta-recovery is actually _more impressive_ than the planned demo — it demonstrates the very
resilience philosophy the project embodies. The senior move is to treat a demo failure as an
unplanned chaos injection and narrate through it.

---

## 9. Rehearsal Protocol

### 9.1 Schedule

| Rehearsal # | Focus                                                                          | Duration |
| ----------- | ------------------------------------------------------------------------------ | -------- |
| 1           | Tech check: verify all services, chaos scenarios, Grafana queries              | 30 min   |
| 2           | Script read-through (no tech, just words + timing)                             | 15 min   |
| 3           | Half-speed run with all tech (double-click deliberately, narrate every action) | 10 min   |
| 4           | Full-speed run, stopwatch, note timing drift                                   | 5 min    |
| 5           | Full-speed run, fix major timing issues                                        | 5 min    |
| 6           | Full-speed run, polish cursor movements and transitions                        | 5 min    |
| 7           | Dress rehearsal — same clothes, same room, same lighting as final              | 5 min    |
| 8           | Cold run — simulate "morning of" conditions (just woke up, no warm-up)         | 5 min    |

**Total rehearsal time:** ~1.5 hours over 2 days.

### 9.2 Rehearsal Recording Protocol

Record rehearsals 4–8. Review with these questions:

1. **Timing:** Did any section run over? Under? Where?
1. **Cursor:** Was the cursor ever circling aimlessly? Were clicks deliberate and visible?
1. **Grafana:** Were all panel transitions visible on the recording? Did any panel show "No data"?
1. **Transitions:** Were Alt+Tabs clean or did they flash the wrong tab?
1. **Voice:** Any filler words? Uptalk? Rushed delivery?
1. **Eye contact:** Did the presenter look at the camera during the hook and close?

### 9.3 Venue-Specific Rehearsal

If possible, do rehearsal #7 in the actual presentation room with the actual projector. Test:

- Wi-Fi vs. Ethernet (prefer Ethernet for OBS streaming if applicable)
- Projector color reproduction (red/green colorblindness affects ~8% of males — ensure luminance
  contrast, not just hue contrast)
- Audio echo in the room
- Seating distance from screen for the back row
- Power outlets — bring an extension cord

---

## 10. Post-Demo Teardown

```bash
# 1. Reset chaos flags
curl -s -X POST http://localhost:8000/api/v1/chaos/reset

# 2. Tear down demo stack
docker compose -f docker-compose.yml -f docker-compose.demo.yml down

# 3. Verify production config is clean (no demo overrides)
grep -r "FEATURE_CHAOS_API_ENABLED" docker-compose.production.yml
# Should return NOTHING or "false"

# 4. If demo was recorded, archive the recording
mv ~/Videos/vooglaadija-demo-*.mkv ./presentation/recordings/
```

---

## Appendix A: CSRF Token Extraction for curl

```bash
# Step 1: Log in via demo-login (sets JWT + CSRF cookies)
curl -c /tmp/demo-cookies.txt -v http://localhost:8000/web/demo-login 2>&1 | grep -E "Set-Cookie|Location"

# Step 2: Visit chaos-lab to seed a CSRF token in cookies
curl -c /tmp/demo-cookies.txt -b /tmp/demo-cookies.txt -L http://localhost:8000/web/chaos-lab > /dev/null 2>&1

# Step 3: Extract CSRF token value from cookies
CSRF=$(grep csrf_token /tmp/demo-cookies.txt | awk '{print $NF}')

# Step 4: Inject chaos (CSRF token from cookie, JWT from cookie for auth)
curl -b /tmp/demo-cookies.txt -X POST http://localhost:8000/api/v1/chaos/inject \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF" \
  -d '{"scenario":"circuit_breaker_open","duration_seconds":30}'

# Step 5: Reset all chaos flags
curl -b /tmp/demo-cookies.txt -X POST http://localhost:8000/api/v1/chaos/reset \
  -H "X-CSRF-Token: $CSRF"

# Step 6: Check status
curl -b /tmp/demo-cookies.txt http://localhost:8000/api/v1/chaos/status | jq .
```

> **Note:** The demo login uses GET `/web/demo-login` (not the REST API) because it sets both JWT
> and CSRF cookies in a single redirect flow. The chaos-lab page visit ensures the CSRF cookie is
> set before extraction.

## Appendix B: Quick Prometheus Queries for Manual Verification

```promql
# Circuit breaker state (0=CLOSED, 1=OPEN, 2=HALF-OPEN)
ytprocessor_circuit_breaker_state{service="youtube_api"}

# Queue depth
ytprocessor_queue_depth

# Error rate (5xx + 429 per-second over 1m window)
rate(ytprocessor_http_requests_total{status_code=~"5..|429"}[1m])

# Recovery count (cumulative)
ytprocessor_recoveries_total

# Recovery rate (per-minute)
increase(ytprocessor_recoveries_total[1m])

# Throttle risk score (0.0–1.0)
ytprocessor_throttle_risk_score{service="youtube", provider="youtube"}

# Worker health status (1=HEALTHY, 0=DOWN)
ytprocessor_worker_status

# All metrics in Prometheus format:
# curl -s http://localhost:9090/api/v1/label/__name__/values | jq '.data[] | select(startswith("ytprocessor"))'
```

---

_This guide is a living document. Update it after every rehearsal with timing adjustments, new risks
discovered, and refined backup procedures. The demo is not the product — the demo is the proof that
the product was built by someone who has seen systems die and decided theirs wouldn't._
