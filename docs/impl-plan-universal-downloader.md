# Implementation Plan: Universal Non-YouTube Media Downloader

**Source:** [Deep Research: Universal Non-YouTube Media Downloader](../browser-downloader/deep-research-universal-video-downloader.md)
**Issue:** [#140](https://github.com/tomkabel/vooglaadija/issues/140)
**Date:** 2026-06-28
**Status:** Revised — Cycle 1 (architect critique applied)
**Replaces:** Original draft

---

## Overview

Replace/extend the current yt-dlp-based extraction with a hybrid architecture:
1. **yt-dlp** remains as primary path for well-supported platforms (YouTube, etc.)
2. **Playwright/Puppeteer Stealth** browser automation in **Docker containers with gVisor sandboxing** as universal fallback
3. Two-tier extraction: direct stream interception → DOM-based blob detection, with real-time screencast as optional last resort

The worker orchestrator tries yt-dlp first; if it fails on a non-YouTube platform, it falls through to the browser-based pipeline.

### Changes from v1 (Critique-Driven Revisions)

| Concern | Severity | Change |
|---|---|---|
| 100x virtual-time recording is physically infeasible (audio/video clock desync, CDP screencast I/O bottleneck) | HIGH | Dropped. Tier 3 replaced with real-time 1x screencast as optional, documented-as-lossy last resort |
| Alpine musl + Xvfb + LLVMPipe CPU rendering creates trivially detectable anti-bot signatures | HIGH | Replaced with glibc-based Docker images, Playwright's bundled Chromium, GPU acceleration where available |
| Firecracker double I/O (guest disk → host transfer → host storage) and 1GB rootfs limit cause silent failures on HD streams | HIGH | Docker containers with host bind-mount volumes; direct write to host storage, no guest-to-host copy |
| Obscura browser library (May 2026, 3 weeks old) is unproven — CDP gaps and maintenance risk | MEDIUM | Replaced with Playwright (Python) + puppeteer-extra-plugin-stealth (Node.js) — mature ecosystem with anti-detection plugins |
| musl static compilation of ffmpeg-next impossible without stripping decoders | MEDIUM | Dropped. Use system ffmpeg via Docker layer, no static bundling |
| Firecracker orchestrator (TAP devices, IP pools, crash recovery) is a standalone infra project | MEDIUM | Replaced with Docker Compose + gVisor (runc sandbox) — equivalent isolation, dramatically simpler orchestration |
| HLS/DASH parser effort severely underestimated | MEDIUM | Leverage streamlink + ffmpeg as extraction backends rather than building Rust parsers; allocate specialist time |
| 20–30 URL test suite is continuous maintenance, not a one-time phase | MEDIUM | Automated URL health monitoring, periodic refresh rotated into ongoing ops budget |

---

## Architecture (Revised)

```
┌──────────────────────────────────────────────────────────────┐
│                    Worker (Python)                            │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Hybrid Router                                       │   │
│  │  youtube.com → yt-dlp (existing path)                │   │
│  │  tiktok.com → Browser downloader microservice        │   │
│  │  instagram.com → Browser downloader microservice     │   │
│  │  * → yt-dlp first, fallback to Browser              │   │
│  └──────────────────────┬───────────────────────────────┘   │
│                         │ (subprocess + HTTP)                 │
│                         ▼                                    │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Browser Downloader (Node.js or Python)              │   │
│  │  - Playwright/Puppeteer Stealth                      │   │
│  │  - CDP Network interception (Tier 1)                 │   │
│  │  - DOM-based blob detection (Tier 2)                 │   │
│  │  - Real-time screencast fallback (Tier 3, optional)  │   │
│  │  - ffmpeg for HLS/DASH segment concatenation         │   │
│  └──────────────────────┬───────────────────────────────┘   │
│                         │                                    │
│  ┌──────────────────────▼───────────────────────────────┐   │
│  │  gVisor Sandbox (runsc)                              │   │
│  │  - Network egress: target domain only                │   │
│  │  - Filesystem: host bind-mount for output            │   │
│  │  - CPU/memory cgroup limits                          │   │
│  │  - Seccomp filter                                    │   │
│  └──────────────────────────────────────────────────────┘   │
│                         │ (host bind-mount)                   │
│                         ▼                                    │
│              ┌─────────────────────┐                        │
│              │   Host Storage      │                        │
│              │   (direct write)    │                        │
│              └─────────────────────┘                        │
└──────────────────────────────────────────────────────────────┘
```

**Key architectural properties:**
- No VM overhead — gVisor provides syscall-level isolation at near-native performance
- No double I/O — output writes directly to host storage via bind-mount
- No musl/Alpine identity leaks — standard glibc Debian/Ubuntu images with real fonts and GPU rendering
- No custom CDP bindings — Playwright/Puppeteer Stealth leverages the mature Node.js/Python automation ecosystem

---

## Phase 0 — Browser Downloader Microservice (Week 1–2)

### 0.1 Scaffold Playwright Stealth service (Node.js)

Create `packages/browser-downloader/`:
- `package.json` with `playwright`, `playwright-extra`, `puppeteer-extra-plugin-stealth`, `express`
- Dockerfile based on `mcr.microsoft.com/playwright:focal` (full Chromium + system ffmpeg, ~400MB)
- HTTP API on port 3000: `POST /download { "url": "...", "output_dir": "/output" }`
- Returns JSON: `{ "status": "success"|"failed", "file_path": "...", "error": "...", "tier_used": 1|2|3 }`

### 0.2 Implement CDP network interception (Tier 1 — Primary Path)

1. Launch Chromium via Playwright with stealth plugin (`playwright.chromium.launch({ headless: true })`)
2. Create a new page and enable request interception via CDP session
3. Register interceptors for:
   - `Content-Type: video/*` responses
   - URLs matching `*.m3u8` (HLS playlist), `*.mpd` (DASH manifest), `*.mp4`, `*.webm`, `*.ts`
4. On match: enforce per-kind size caps BEFORE materializing the body (manifest 8 MiB, key 64 KiB, segment/media 256 MiB) — reject candidates whose `Content-Length` or CDP `encodedDataLength` already exceeds the cap, so oversized responses never enter `Network.getResponseBody`
5. Fetch the response body via `Network.getResponseBody` only for responses within the cap (CDP buffers the whole body in the browser's memory — a post-read size check remains as a backstop for compressed responses)
6. For HLS: download all `.ts` segment URLs from the `.m3u8` manifest, then `ffmpeg concat`
7. For DASH: parse the `.mpd` manifest (XML), download video+audio segments, `ffmpeg` mux
8. For direct MP4/WebM: single download, no post-processing
9. Audio: capture audio streams directly via network interception (same as video)
10. Oversized media beyond the cap: do NOT buffer through CDP — stream the validated URL directly to disk in a controlled manner (bounded concurrency, per-segment caps, abortable), or fail with a clear `network_error` when the URL cannot be streamed directly

### 0.3 Implement DOM-based blob detection (Tier 2 — for TikTok/Instagram)

For platforms that construct media client-side:
1. Navigate to URL, wait for page load
2. Inject a script that hooks:
   - `URL.createObjectURL` — intercept blob URL creation
   - `MediaSource.addSourceBuffer` — intercept MSE buffer appends
3. Click the play button (find via `[aria-label="Play"]`, `[data-testid="play"]`, or generic video element)
4. Poll `page.evaluate(() => document.querySelector('video')?.src)` until a non-empty blob URL appears
5. Download the blob via `Network.getResponseBody`
6. If the blob URL is short-lived, capture via CDP Fetch domain (`Fetch.enable` with request patterns)

### 0.4 Real-time screencast fallback (Tier 3 — Optional, last resort)

**Design note:** Virtual-time acceleration (`Emulation.setVirtualTimePolicy`) is dropped because it is fundamentally incompatible with real-time audio/video system clocks. The screencast fallback runs at 1x speed only.

1. Navigate to page, click play
2. Start `Page.startScreencast` with `format: "jpeg"`, `quality: 80`, `maxWidth: 1920`
3. Pipe screencast frames to ffmpeg stdin:
   ```
   ffmpeg -f image2pipe -framerate 30 -i - \
     -c:v libx264 -preset ultrafast -crf 23 output.mp4
   ```
4. Audio: not captured (documented limitation)
5. This produces a **watchable but lossy recording** of whatever the browser plays
6. **Enabled only via explicit config flag** (`feature_recording_fallback=true`, default false)

### 0.5 Use streamlink as HLS/DASH backend (NOT custom Rust parsers)

Instead of building HLS/DASH parsers from scratch:
- Detect the media stream URL via CDP interception (Phase 0.2)
- Pass the URL to `streamlink` CLI: `streamlink <url> best -o output.mp4`
- streamlink handles: key decryption, adaptive bitrate selection, segment retry, live streams
- If streamlink fails, fall back to manual segment download + ffmpeg concat as backup

---

## Phase 1 — Sandbox Isolation Layer (Week 2)

### 1.1 Docker Compose sandbox setup

Create `docker/sandbox/docker-compose.yml`:
```yaml
services:
  browser-downloader:
    image: vooglaadija/browser-downloader:latest
    runtime: runsc  # gVisor sandbox
    cpus: 2
    memory: 512M
    volumes:
      - ${OUTPUT_DIR}:/output:rw  # host bind-mount, no double I/O
    networks:
      - sandbox-net
    security_opt:
      - no-new-privileges:true
    read_only: true
    tmpfs:
      - /tmp:size=256M

networks:
  sandbox-net:
    driver: bridge
```

> **Egress restriction:** Docker networks cannot filter by domain — `internal: true`
> would block ALL egress, which breaks downloading. Domain-restricted egress is
> enforced instead by (a) the microservice's SSRF validation, which limits every
> request to the permitted target media domain (plus the validated redirect
> targets), and (b) optionally a host-level egress firewall or egress proxy that
> allows only the configured media domains. The Compose network itself only
> isolates the sandbox from other containers.

### 1.2 gVisor (runsc) configuration

gVisor provides syscall-level isolation without the overhead of full virtualization:
- **Filesystem isolation:** The browser can only write to `/output` (host bind-mount) and `/tmp` (tmpfs)
- **Network isolation:** The sandbox is isolated from other containers; egress to the target media domain only is enforced in-app (SSRF validation against the permitted domain) plus optionally a host firewall/egress proxy — Docker network config alone cannot restrict by domain
- **Seccomp filter:** Built into gVisor — only ~200 syscalls allowed vs ~400 in standard Linux
- **No KVM required:** gVisor runs entirely in user space (works in Docker-in-Docker, GitHub Actions, any cloud VM)
- **Boot time:** <50ms (vs 125ms for Firecracker)
- **Memory overhead:** ~30MB (vs <5MB for Firecracker — acceptable trade-off for simplicity)

### 1.3 Container lifecycle management

Create `worker/container_executor.py`:
1. Start the browser-downloader container via `docker compose -p sandbox-<job_id> up -d` — the unique per-job Compose project name keeps concurrent downloads from stopping each other's containers
2. POST the download job to the container's HTTP API
3. Poll for completion (container returns JSON when done)
4. On completion: read output file from bind-mounted host directory
5. `docker compose -p sandbox-<job_id> down` to destroy the sandbox — the SAME project identity as the `up` step, so cleanup only ever targets this job's containers
6. Per-download container (fresh state, no session cross-contamination); the project name is derived from the job id (sanitized to `[a-z0-9-]`) so parallel downloads run isolated Compose projects

---

## Phase 2 — Worker Integration (Week 2–3)

### 2.1 Hybrid extraction strategy in `job_executor.py`

Modify `worker/job_executor.py` to add platform-aware dispatch:

```python
async def execute(db, job, *, start_time):
    platform = detect_platform(job.url)
    
    if platform in YT_DLP_PLATFORMS:
        return await execute_yt_dlp(db, job, start_time)  # existing path
    else:
        return await execute_browser_downloader(db, job, start_time)  # new path
```

Platform detection rules (from `worker/platform_routing.py`):
- `youtube.com`, `youtu.be` → yt-dlp (well-supported, existing circuit breaker)
- `tiktok.com` → Browser downloader (TikTok constructs blobs client-side, yt-dlp extractor frequently breaks)
- `instagram.com` → Browser downloader (same blob-based construction)
- `twitter.com`, `x.com` → Browser downloader
- Everything else → try yt-dlp first, fall back to Browser downloader on specific error classes

### 2.2 Browser downloader subprocess executor

Create `worker/browser_executor.py`:
1. Start the sandbox container (`docker compose -f docker/sandbox/docker-compose.yml up -d`)
2. POST `{ "url": "...", "output_dir": "/output" }` to `http://browser-downloader:3000/download`
3. Monitor with configurable timeout (default 120s)
4. Parse JSON response for success/failure/tier_used
5. On success: verify output file on host bind-mount path
6. On failure: classify error (DRM, anti-bot block, network error) for retry logic
7. Always: `docker compose down` to destroy the sandbox

### 2.3 Circuit breaker for browser downloader

Reuse the existing `CircuitBreaker` for the browser path:
- Separate instance per platform domain (e.g., `tiktok_api`, `instagram_api`)
- Same failure threshold (5 failures → OPEN), success threshold (3 successes → CLOSE)
- Distributed via Redis (same pattern as `youtube_api` breaker)

### 2.4 Error classification mapping

| Browser Error | Category | Retries |
|---|---|---|
| `drm_detected` | BLOCKED | 0 (permanent) |
| `anti_bot_block` | BLOCKED | 0 (permanent, mark for manual review) |
| `network_error` | TRANSIENT | 3 |
| `timeout` | TIMEOUT | 2 |
| `no_media_found` | NOT_FOUND | 0 |
| `tier3_recording_used` | (success, lower quality, log as info) | N/A |

---

## Phase 3 — Anti-Detection Infrastructure (Week 3–4)

### 3.1 puppeteer-extra-plugin-stealth configuration

The Stealth plugin (maintained by the community, ~4k GitHub stars, regularly updated) handles:
- `navigator.webdriver` → `undefined`
- Canvas fingerprint randomization
- WebGL vendor/renderer spoofing with consistent values
- Font enumeration with realistic OS font sets
- `chrome.runtime` presence (evades extension detection)
- `navigator.plugins` with standard plugin list
- `window.outerWidth/outerHeight` consistent with viewport

**Why this replaces custom Rust stealth code:** The plugin is battle-tested against Akamai, Cloudflare, PerimeterX, and DataDome. It updates within days of new Chromium releases. This is an ongoing maintenance dependency, not a one-time implementation — and it costs zero engineering time vs building and maintaining custom fingerprinting in Rust.

### 3.2 Consistent fingerprint profiles (NOT runtime randomization)

Runtime-randomized fingerprints create inconsistencies (e.g., Chrome 145 UA + old headless rendering behavior) that increase block rates. Instead:
- Curate 5–10 pre-tested, consistent fingerprint profiles
- Each profile: specific Chrome version, OS platform, screen resolution, font set, WebGL vendor → all internally consistent
- Rotate profiles per session (not per field)
- Validate profiles weekly against a test suite of anti-bot-protected URLs

### 3.3 Behavioral simulation

- Use Playwright's built-in `page.mouse.move(x, y, { steps: 10 })` for human-like mouse curves
- Add `page.waitForTimeout(random(1000, 3000))` between navigation and interaction
- Scroll `window.scrollBy(0, random(100, 300))` before clicking play
- These are handled by Playwright's API (no custom code needed)

### 3.4 Proxy rotation (deferred)

- Rotate exit IPs per session via SOCKS5 proxy pool
- Integrate with residential proxy providers if anti-bot blocking becomes a scale problem
- **Deferred until production traffic reveals actual block rates**

---

## Phase 4 — Operations & Deployment (Week 4–5)

### 4.1 Docker integration

Worker container needs:
- `docker` socket access (`-v /var/run/docker.sock:/var/run/docker.sock`)
- gVisor runtime installed (`runsc` binary + Docker runtime config)
- Browser downloader Docker image pre-built
- No KVM device required (gVisor runs in user space)

### 4.2 Configuration

Add to `Settings` (in `core/config.py`):
```python
browser_downloader_enabled: bool = True
browser_downloader_endpoint: str = "http://browser-downloader:3000/download"
browser_downloader_timeout: int = 120
browser_downloader_image: str = "vooglaadija/browser-downloader:latest"
browser_downloader_sandbox_runtime: str = "runsc"  # gVisor
feature_recording_fallback: bool = False  # Tier 3 disabled by default
```

### 4.3 Monitoring & metrics

Add Prometheus metrics:
- `browser_downloader_attempts_total{platform, tier}` — counter per platform + tier used
- `browser_downloader_duration_seconds{platform, tier}` — histogram
- `sandbox_launch_duration_seconds` — histogram for container startup time
- `sandbox_active_count` — gauge for concurrent sandboxed downloads

### 4.4 Health checks

- Verify Docker daemon is reachable on worker startup
- Verify `runsc` (gVisor) runtime is registered in Docker
- Verify browser downloader image exists locally
- Expose via `/health/ready` endpoint

---

## Phase 5 — Testing & Ongoing Validation (Week 4+, continuous)

### 5.1 Unit tests for browser downloader

- Test Tier 1 interception with local HTML pages serving `<video src="test.mp4">`
- Test blob URL detection with a page that creates blobs via JavaScript
- Test HLS segment download + ffmpeg concatenation
- Test DASH manifest parsing + muxing via streamlink
- Test DRM detection (EME API present → fails cleanly)
- Test anti-bot response (simulated block page → exits with `anti_bot_block`)
- Test container lifecycle: starts, writes output to bind-mount, stops cleanly

### 5.2 Integration tests

- Full pipeline: API → Redis queue → worker → browser downloader container
- Hybrid routing: yt-dlp for YouTube, browser for TikTok
- Concurrent execution (2 simultaneous sandboxed downloads)
- Sandbox cleanup after timeout/crash (no leaked containers)
- Circuit breaker triggers and recovers correctly

### 5.3 Real-world smoke test suite (ongoing)

Maintain an automated test suite of 20–30 live URLs with:
- **Automated URL health monitoring:** Weekly cron that verifies each URL is still reachable and has playable media
- **Periodic test refreshes:** URLs that break are replaced with new ones from a pool
- **Regression detection:** Download an expected URL, compare output size/format against baseline
- **Platform coverage:**
  - YouTube (standard, Shorts) → yt-dlp path (baseline)
  - TikTok (video, slideshow) → Browser path, Tier 2
  - Instagram (Reel, post) → Browser path, Tier 2
  - Twitter/X (video tweet) → Browser path, Tier 1
  - Vimeo, Dailymotion → yt-dlp, fallback to Browser
  - DRM-protected → expected failure with clear error code

**Ongoing maintenance budget:** 0.5 day/week for refreshing the test suite and updating anti-detection profiles.

---

## Risk Mitigation

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Anti-bot arms race makes puppeteer-stealth ineffective | Medium | High | Stealth plugin has established community maintenance; fall back to commercial anti-detect browser (Multilogin API) if needed |
| gVisor (runsc) not available in deployment environment | Low | High | gVisor runs entirely in user space, no KVM needed; if unavailable, fall back to standard Docker with seccomp profile |
| Mature Playwright ecosystem → higher resource footprint (400MB image vs 70MB Rust binary) | High | Low | Acceptable trade-off — reliability and anti-detection coverage are worth the disk space; image is pre-pulled |
| DMCA takedown of GitHub repo | Medium | High | Self-host repository; clear educational-purpose documentation; rate limiting and domain blocklists included |
| streamlink HLS/DASH parsing gaps for niche sites | Medium | Medium | Fall back to manual segment download + ffmpeg concat (Phase 0.5 backup path) |
| Recording quality (Tier 3, optional) too low for practical use | High | Low | Disabled by default; documented as experimental; Tier 1+2 cover the vast majority of use cases |
| Docker-in-Docker in CI environments adds latency | Medium | Low | gVisor containers start in <50ms; shared Docker socket avoids DinD nesting |

---

## Cost Estimate (Revised)

| Component | Effort | Notes |
|---|---|---|
| Phase 0 — Browser downloader microservice | 8–10 days | Playwright Stealth, CDP interception, DOM blob detection, streamlink integration |
| Phase 1 — Sandbox isolation (gVisor/Docker) | 3–4 days | Docker Compose, gVisor config, container lifecycle (simpler than Firecracker) |
| Phase 2 — Worker integration | 5–7 days | Hybrid routing, error mapping, circuit breaker |
| Phase 3 — Anti-detection | 3–4 days | puppeteer-extra-stealth config, fingerprint profiles, behavioral simulation |
| Phase 4 — Operations | 3–5 days | Docker, config, metrics, health checks |
| Phase 5 — Testing | 5–7 days | Unit + integration + smoke test suite bootstrap |
| **Initial implementation** | **~27–37 days** | Single senior engineer |
| **Ongoing maintenance** | **0.5 day/week** | Test suite refresh + anti-detection profile updates |

---

## Milestones

1. **M1 — Browser downloader works standalone** (end of Phase 0): Download a TikTok/Instagram video via HTTP API in a local container
2. **M2 — Sandbox isolation works** (end of Phase 1): Download happens inside a gVisor-sandboxed container with network egress limits
3. **M3 — Worker integration complete** (end of Phase 2): A user can submit a TikTok URL via the API and get a downloaded file
4. **M4 — Anti-detection validated** (end of Phase 3): Successfully downloads from TikTok, Instagram, Twitter/X in automated tests
5. **M5 — Production-ready** (end of Phase 4): Metrics, health checks, Docker deployment, CI pipeline
6. **M6 — Test suite operational** (end of Phase 5): Automated smoke tests with URL health monitoring

---

## Open Decisions (resolved)

1. ~~**Rust vs Node.js/Python browser automation:**~~ → **Resolved:** Node.js (Playwright + puppeteer-extra-stealth). Mature ecosystem beats novelty for reliability.
2. ~~**Firecracker vs Docker/gVisor isolation:**~~ → **Resolved:** gVisor (Docker with runsc runtime). Equivalent security at dramatically lower complexity.
3. ~~**100x recording vs real-time fallback:**~~ → **Resolved:** Real-time 1x screencast, disabled by default. 100x is physically impossible.
4. ~~**Alpine/musl vs glibc images:**~~ → **Resolved:** Standard glibc (Playwright Docker image). Anti-detection requires real OS fingerprints.
5. ~~**Custom Rust HLS/DASH parsers vs streamlink:**~~ → **Resolved:** streamlink as primary HLS/DASH backend. Building custom parsers is a separate project.
6. ~~**Static ffmpeg bundling vs system ffmpeg:**~~ → **Resolved:** System ffmpeg via Docker layer. Static musl compilation is infeasible.
