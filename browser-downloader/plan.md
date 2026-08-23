## Summary

Phase 2 of issue #140. Wires the existing Phase 0 browser-downloader microservice (`packages/browser-downloader/`) into the worker so `/api/v1/downloads` can serve TikTok/Instagram/Twitter-X URLs end-to-end.

**Before:** every job goes to yt-dlp; TikTok/Instagram/X fail (yt-dlp cannot construct `blob:` media).
**After:** platform-aware dispatch — TikTok/Instagram/X go to the microservice, YouTube stays on yt-dlp.

## Behavior

- **Safe default**: `browser_downloader_enabled=False`. The worker behaves byte-for-byte identical to main until an operator sets `BROWSER_DOWNLOADER_ENABLED=true`. Default `BROWSER_DOWNLOADER_ENDPOINT=http://browser-downloader:3000` resolves in-cluster.
- Platform detection: hostname suffix match on `tiktok.com` / `tiktokv.com` / `instagram.com` / `instagr.am` / `twitter.com` / `x.com` / `t.co` (with `www.`-stripping and FQDN-trailing-dot handling). YouTube and unknown hosts continue to yt-dlp.
- Throttle predictor and progress callback are skipped for browser-routed jobs (the microservice is single-shot HTTP; no progress stream).
- Errors map to existing `ErrorCategory` codes so the existing retry/DLQ pipeline handles them:
  - `drm_detected` / `anti_bot_block` → BLOCKED
  - `network_error` / ConnectError / 5xx / non-JSON → TRANSIENT
  - `timeout` → TIMEOUT
  - `no_media_found` / 404 → NOT_FOUND
  - HTTP 4xx → BLOCKED (except 429 rate-limit → TRANSIENT)
- Dedicated `browser_downloader` circuit breaker (Redis-distributed opt-in via `BROWSER_DOWNLOADER_CB_USE_REDIS`).

## Files

| File | Change |
|------|--------|
| `worker/browser_executor.py` | NEW — httpx client + breaker + error mapping |
| `worker/job_executor.py` | routing helper + dispatch branch in `execute()` |
| `core/config.py` | 4 new settings + validator |
| `pyproject.toml` | `httpx>=0.27` promoted from test extras to runtime |
| `tests/test_worker/test_browser_executor.py` | NEW — error-code matrix, transport failures, breaker integration |
| `tests/test_worker/test_job_executor_routing.py` | NEW — per-URL dispatch + end-to-end through `execute()` |
| `tests/test_browser_downloader_config.py` | NEW — settings defaults + validator failure modes |

## Verification

- `hatch run lint:check` → exit 0
- `hatch run type:check` → no issues
- `hatch run test:unit` → 875 passed, 6 skipped, 0 regressions

## Deferred to P4/P5 (documented in `_bmad-output/implementation-artifacts/deferred-work.md`)

- Prometheus metrics for the browser executor
- `aclose()` on the httpx client at worker shutdown
- `t.co` redirect resolution (currently routes to browser even for YouTube targets)
- Browser→yt-dlp fallback when the breaker is open
- Per-hostname / percentage rollout flag
- Node.js contract test against the actual microservice

## Out of scope (P1, P3, P4)

- No gVisor sandbox (P1)
- No fingerprint profiles or behavioral simulation (P3)
- No live smoke tests (P5)
- No docker-compose service definition (P4)
- `packages/browser-downloader/` unchanged — Phase 0 contract frozen

## Spec

Full design + I/O matrix + review order: `_bmad-output/implementation-artifacts/spec-gh-140-p2-worker-integration.md`
