# TikTok and Instagram Download Reliability: Deep Research Report

_Generated: 2026-07-13 | Sources reviewed: 24 | Confidence: High for the immediate
Instagram/TikTok diagnosis; Medium for long-term platform reliability_

## Executive Summary

The two failures in `browser-downloader/problem.md` do not currently have the same cause. The
repository locks `yt-dlp 2026.3.17`; the current `2026.07.04` release specifically reworked the
Instagram extractor and added invalid-cookie detection. In a metadata-only reproduction from this
workspace, the reported Instagram Reel failed with the locked version and succeeded with
`2026.07.04`, exposing downloadable video and audio formats without cookies. The first corrective
action is therefore an extractor/dependency update, not a browser rewrite.

The TikTok fixture returned `Your IP address is blocked from accessing this post` under both
versions. A browser launched on the same worker or VM keeps the same egress IP, so routing that
failure to the existing browser service is unlikely to help. TikTok and Instagram should remain
**experimental** until non-local smoke results establish platform-specific reliability. The product
can nevertheless satisfy the issue immediately by exposing precise capability and failure states.

The best technical strategy is a staged, evidence-driven pipeline: current `yt-dlp` plus its
recommended impersonation dependency; platform-aware session handling; precise error
classification; and health-based capability gating. Cobalt or gallery-dl can run as shadow canaries
to measure independent extractor value. The Chromium service should only receive failures where a
browser can plausibly add information—never IP blocks, DRM, missing content, or expired operator
credentials.

## 1. Reproduced Root Causes

### 1.1 Repository state

The dependency declaration permits any `yt-dlp >=2025.3.0`, but `uv.lock` resolves it to
`2026.3.17`. The upstream project describes stable releases as potentially stale when sites change,
recommends the nightly channel to regular users, and advises testing nightly before filing extractor
bugs. It also recommends `curl_cffi` for sites requiring browser-request impersonation.
([yt-dlp update channels and dependencies](https://github.com/yt-dlp/yt-dlp/blob/master/README.md#update))

The current project environment does not have `curl_cffi` installed. Upstream documents the
`yt-dlp[default,curl-cffi]` extra for Chrome/Edge/Safari impersonation targets.
([yt-dlp impersonation dependency](https://github.com/yt-dlp/yt-dlp/blob/master/README.md#impersonation))

### 1.2 Controlled metadata-only reproduction

The reproduction did not download media. It ran extraction simulation for the exact two URLs in
`problem.md`:

| Fixture | Locked `2026.3.17` | Current stable `2026.07.04` | Diagnosis |
| --- | --- | --- | --- |
| Instagram Reel `DGcoPAktJAT` | Empty media response; suggested cookies or upgrade | Extracted title plus progressive and separate DASH video/audio formats without cookies | Fixed upstream extractor drift |
| TikTok video `7008477449723292934` | Egress IP explicitly blocked | Same explicit IP block | Access/egress condition, not fixed by extractor update |

The latest release notes independently corroborate the Instagram result: `2026.07.04` includes an
Instagram extractor rework and explicit invalid-cookie detection.
([yt-dlp 2026.07.04 release](https://github.com/yt-dlp/yt-dlp/releases/tag/2026.07.04))

These results are environment-specific. They establish the immediate failure mechanisms but do not
meet the issue's non-local happy-path acceptance criterion by themselves.

### 1.3 Two local implementation details would still reduce reliability after upgrading

First, `_GENERIC_FORMAT_CHAIN` forces `format: best` for Instagram. Current Instagram extraction can
return separate DASH audio and video tracks. Upstream warns that `-f best` selects only the best
pre-merged format and is often not the intended choice; omitting a forced format lets yt-dlp apply
its default merge-aware selection.
([yt-dlp format-selection guidance](https://github.com/yt-dlp/yt-dlp#format-selection))

Second, `no_warnings: True` suppresses useful extractor diagnostics. The worker eventually retains
an exception string, but it loses structured distinctions such as invalid cookies, anonymous rate
limits, login redirects, and an extractor regression. The current Instagram extractor explicitly
emits different messages for invalidated cookies, login redirects, anonymous rate limits, and empty
media responses.
([Instagram extractor source](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/instagram.py))

## 2. Candidate Ranking

Scores are relative to this repository and issue, not general product ratings. “Reliability” means
the probability of improving authorised public TikTok/Instagram URL handling without creating a
larger maintenance burden.

| Rank | Candidate | Reliability potential | Effort | Main limitation | Decision |
| ---: | --- | ---: | ---: | --- | --- |
| 1 | Current yt-dlp + controlled promotion pipeline | High | Low | Extractors still drift | Implement first |
| 2 | Capability registry + precise failure contract | High product value | Medium | Does not itself extract media | Implement regardless |
| 3 | Platform-scoped session manager | Medium-high | Medium | Credential expiry/account risk | Implement only after cookie A/B evidence |
| 4 | Cobalt shadow adapter | Medium | Medium | AGPL service; similar reverse-engineered endpoints | Benchmark, do not immediately depend on it |
| 5 | gallery-dl shadow adapter | Medium-low | Low-medium | Failure modes correlate with yt-dlp | Canary/diagnostic role |
| 6 | Chromium/CDP discovery fallback | Medium for specific failures | High | Same IP/session; costly and brittle | Narrow conditional fallback only |
| 7 | User-run browser companion | Medium-high for authorised sessions | High product effort | Separate client experience | Strong later strategy |
| 8 | Official connected-account APIs | High within narrow scope | High | Owned/professional content only | Separate product mode |
| 9 | Instaloader fallback | Low-medium | Medium | Instagram-only; Reel/session regressions | Do not prioritise |
| 10 | Rust browser / accelerated recording | Low | Very high | Immature media stacks; cannot solve access controls | Reject |

## 3. Improve the Existing Candidates

### 3.1 Make yt-dlp an actively managed extractor, not a static library

Use two release lanes:

1. **Production:** exact promoted stable or nightly version in the lockfile.
2. **Canary:** scheduled test against the latest upstream nightly using the same fixture matrix.

Promote only when canaries pass. Do not let a production container self-update at runtime; that
would make jobs non-reproducible. Store `extractor_name`, `extractor_version`, selected format IDs,
and a sanitised failure signal with each attempt.

Upstream lists TikTok and Instagram extractors but explicitly says that listing does not guarantee a
site currently works; testing is the reliable check.
([yt-dlp supported-sites caveat](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md))

Recommended profile changes:

- Add `yt-dlp[default,curl-cffi]` and verify available impersonation targets during worker startup.
- Stop forcing `best` for Instagram; use the upstream default or an explicit
  `bestvideo*+bestaudio/best` fallback.
- Keep TikTok and Instagram profiles separate. TikTok's current extractor can solve a short-lived
  webpage challenge, distinguish login-required content, propagate the `sid_tt` cookie to media
  hosts, and report an explicit IP block.
  ([TikTok extractor source](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/tiktok.py))
- Make warnings observable rather than globally suppressing them.
- Retry only transient transport and 429 failures. Do not retry an unchanged IP block, invalid
  credentials, DRM, private/deleted media, or unsupported format.

### 3.2 Replace the binary browser feature flag with a capability registry

The existing boolean sends TikTok/Instagram directly to the browser service when enabled. Replace
it with runtime state per platform:

```text
supported | experimental | degraded | blocked
```

Each state should include the promoted extractor version, required authentication mode, last
successful smoke timestamp, rolling success rate, current reason, and allowed executor chain. URL
validation should answer “is this a recognised platform?”; the capability registry should answer
“what promise can the product make right now?”

Suggested public failure codes:

| Code | Retry? | Browser fallback? | User meaning |
| --- | --- | --- | --- |
| `login_required` | No | Only with an already-authorised managed session | This media requires login |
| `session_expired` | No; operator action | No | The service session needs renewal |
| `challenge_required` | No automatic loop | Maybe, experimental | Platform verification interrupted access |
| `ip_blocked` | No | No on the same egress | Platform blocked this service location |
| `rate_limited` | Delayed, bounded | No | Try after the recorded retry window |
| `geo_restricted` | No | No | Unavailable from this region |
| `not_found_or_private` | No | No | Removed, private, or inaccessible |
| `extractor_regression` | No user retry | Shadow-engine/canary only | Recognised public media could not be parsed |
| `no_media_detected` | No repeated yt-dlp retry | Yes, experimental | Browser discovery may add information |
| `drm_detected` | No | No | Protected media is outside scope |

This is more precise than mapping every login, restriction, 403, copyright, and geo condition into
one generic `BLOCKED` category.

### 3.3 Treat cookies as a managed platform secret

Cookie support should not mean reading a developer's personal browser profile in production.
Upstream warns that exported browser cookies can include credentials for every site, and Playwright
warns that saved browser state can impersonate its account.
([yt-dlp cookie FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp))
([Playwright authentication state](https://playwright.dev/docs/auth))

If cookie A/B smoke tests prove a material improvement:

- use a dedicated operator account and isolated profile per platform;
- mount the minimum platform-scoped cookie file read-only from a secret store;
- never log cookies, signed media URLs, CSRF tokens, or browser storage state;
- record an expiry/health state and stop jobs when the session is invalid;
- document renewal, revocation, ownership, and account-loss consequences;
- never share one mutable browser context between users or concurrent jobs.

Cookies will not fix the reproduced TikTok IP block, so this work should follow—not precede—the
yt-dlp upgrade and non-local egress tests.

### 3.4 Restrict the browser service to browser-solvable failures

Playwright can monitor HTTP(S), XHR, fetch, and WebSocket traffic. It also documents that Service
Workers can hide events from routing APIs, which must be considered during testing.
([Playwright network documentation](https://playwright.dev/docs/network))

The browser fallback should therefore:

- run only after `no_media_detected` or a confirmed extractor parsing regression;
- inject selected PoC observation hooks before navigation, not after the player has already created
  its MediaSource or requests;
- prefer URL/header discovery over copying large bodies through CDP;
- preserve only the minimum required request context when handing a manifest to a downloader;
- keep the download on the same sandbox egress and never expose captured credentials to the worker;
- stop at DRM, login challenge, private media, IP block, geo restriction, or missing content.

Chrome DevTools Protocol exposes response-body and cookie methods, but those primitives do not make
protected or inaccessible content downloadable.
([CDP Network domain](https://chromedevtools.github.io/devtools-protocol/tot/Network/))

The current browser code loses request context when it passes an HLS/DASH URL to Streamlink.
Streamlink and N_m3u8DL-RE are good **download backends once a valid manifest and permitted request
context already exist**; neither discovers Instagram/TikTok media or cures an access block.
([Streamlink CLI](https://streamlink.github.io/cli.html))
([N_m3u8DL-RE](https://github.com/nilaoda/N_m3u8DL-RE))

## 4. Additional Candidates and Strategies

### 4.1 Self-hosted Cobalt as a shadow extractor

Cobalt declares direct TikTok and Instagram support, including Instagram Reels and TikTok videos,
slideshows, audio, and watermark choices. Its API returns structured status/error objects and has no
public pre-hosted API intended for application use, so evaluation requires a self-hosted instance.
([Cobalt API README](https://github.com/imputnet/cobalt/blob/main/api/README.md))
([Cobalt API contract](https://github.com/imputnet/cobalt/blob/main/docs/api.md))

This is the strongest newly identified engine candidate, but not because it avoids platform drift.
Its TikTok implementation also parses `__UNIVERSAL_DATA_FOR_REHYDRATION__`; its Instagram
implementation uses embed/mobile/GraphQL endpoints and cookies. Those are independent code paths
but the same underlying platform surfaces and egress conditions.
([Cobalt TikTok implementation](https://github.com/imputnet/cobalt/blob/main/api/src/processing/services/tiktok.js))
([Cobalt Instagram implementation](https://github.com/imputnet/cobalt/blob/main/api/src/processing/services/instagram.js))

Run it in shadow mode first: submit the URL only after yt-dlp failure, retain its classified outcome
without returning it to users, and compare results for several weeks. Adoption requires an AGPL-3.0
licensing/deployment review, resource and SSRF hardening, and explicit source-compliance handling.

### 4.2 gallery-dl as a cheap independent canary

gallery-dl is actively maintained and supports cookies, TikTok, and Instagram. Its TikTok extractor
parses the same rehydration data and can optionally delegate media downloading to yt-dlp; its
Instagram extractor uses REST/GraphQL paths and browser cookies.
([gallery-dl](https://github.com/mikf/gallery-dl))
([gallery-dl TikTok extractor](https://github.com/mikf/gallery-dl/blob/master/gallery_dl/extractor/tiktok.py))
([gallery-dl Instagram extractor](https://github.com/mikf/gallery-dl/blob/master/gallery_dl/extractor/instagram.py))

That correlation makes it less valuable as a production fallback than Cobalt, but it is inexpensive
as a diagnostic: if current yt-dlp and gallery-dl both fail with an access/session signal, a browser
parser is unlikely to be the missing piece.

### 4.3 A user-run browser companion

For content a user is already authorised to view, a browser extension or local companion can observe
the request in the user's own session and download locally. This avoids concentrating all playback
and session traffic behind the worker's server IP. Chrome's `webRequest` API can observe and analyse
requests with the required host permissions, although Manifest V3 limits blocking modification and
does not expose established WebSocket messages.
([Chrome webRequest API](https://developer.chrome.com/docs/extensions/reference/api/webRequest))

This is a separate client-mode product, not a transparent backend fallback. It needs explicit user
consent, local-only token handling, narrow host permissions, and a clear non-DRM boundary. It is
nevertheless structurally better than server-side browser automation for user-session-dependent
media.

### 4.4 Official connected-account imports

Official APIs can create a reliable, compliant mode for user-owned content, but cannot replace
arbitrary public-URL downloading:

- TikTok's Display API operates on an authorised user's videos and returns share/embed metadata,
  not a general direct-download URL. Its query endpoint verifies that requested IDs belong to the
  authorised user.
  ([TikTok Query Videos](https://developers.tiktok.com/doc/tiktok-api-v2-video-query/))
  ([TikTok Video Object](https://developers.tiktok.com/doc/tiktok-api-v2-video-object/))
- TikTok oEmbed returns embed HTML and metadata for public video URLs, making it useful as a
  lightweight availability/preflight signal, not a download engine.
  ([TikTok Embedded Videos](https://developers.tiktok.com/doc/embed-videos/))
- Meta's Instagram API is centred on professional accounts and media those accounts manage; its
  official collection states that the Facebook Login variant cannot access consumer accounts.
  ([Meta Instagram API collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api))

This candidate makes sense if Vooglaadija adds “import my own media” alongside—but distinct from—its
current paste-any-public-link workflow.

### 4.5 Preflight and adaptive product gating

Before creating a full download job, run a short platform preflight that can return:

- recognised and anonymously accessible;
- recognised but authentication required;
- currently degraded/blocked by capability health;
- unavailable/private;
- unknown and best-effort.

Preflight should reduce obviously doomed queue work, but it must not promise a later download:
short-lived URLs, token rotation, and rate limits can change between metadata and transfer. TikTok
oEmbed can supplement—but not replace—extractor preflight.

The platform capability should automatically degrade after a minimum sample threshold of real and
synthetic failures, then recover only after consecutive non-local canary successes. Keep fixture
health separate from extractor health so a deleted test post does not falsely mark an engine broken.

## 5. Strategies Not Recommended

| Strategy | Why it does not solve this issue well |
| --- | --- |
| Route every TikTok/Instagram job to Chromium | Same egress/session constraints; higher cost; bypasses the now-fixed Instagram extractor |
| Rotate residential proxies or spoof TLS/browser identities | Creates an evasion arms race, policy risk, cost, and unstable support semantics |
| Persist personal browser profiles on the worker | High-value credential store and cross-user contamination risk |
| Retry all 403/login failures | Repeats terminal conditions and can worsen account/IP restrictions |
| Rust browser replacement | Current candidates do not demonstrate mature social-media playback/MSE compatibility |
| 100× screencast and FFmpeg slowdown | Media, audio, wall, and capture clocks do not generally accelerate together; lossy and cannot cross DRM |
| Capture EME licence responses or keys | DRM circumvention is outside the stated product scope and the technical/legal risk is unacceptable |
| Depend on a public Cobalt instance | Cobalt explicitly says there is no public pre-hosted API intended for project integration |

## 6. Recommended Delivery Plan

### Phase 0 — Close the immediate correctness gap

1. Promote and lock `yt-dlp 2026.07.04` or a tested newer release.
2. Add the recommended `curl_cffi` extra and startup diagnostics.
3. Stop forcing `best` for Instagram; test actual audio/video mux output.
4. Add failure codes for `ip_blocked`, `session_expired`, `login_required`, and
   `extractor_regression`.
5. Mark TikTok/Instagram experimental in API and UI; show the specific reason on failure.

### Phase 1 — Establish evidence outside the developer machine

Use at least two authorised non-local environments and stable fixtures:

| Platform | Anonymous public | Managed cookie | Current stable | Latest nightly | Expected signal |
| --- | ---: | ---: | ---: | ---: | --- |
| Instagram Reel | Yes | Optional A/B | Yes | Canary | Successful mux with audio |
| TikTok video | Yes | Optional A/B only if not IP-blocked | Yes | Canary | Success or explicit `ip_blocked` |
| Deleted/private fixture | No | No | Yes | Yes | Terminal classified failure |
| Expired session fixture | N/A | Yes | Yes | Yes | `session_expired`, no retries |

Record extractor version, environment/egress label, authentication mode, selected formats, duration,
container probe, and sanitised outcome. Never store signed URLs or cookies in metrics.

### Phase 2 — Measure alternative engines

Run Cobalt and gallery-dl as non-user-visible shadow adapters on a bounded cohort. Adopt an adapter
only if it succeeds on a meaningful class of failures where current yt-dlp does not, and if its
operational/licensing burden is accepted.

### Phase 3 — Add the browser only where evidence supports it

Repair request-context handoff and early instrumentation, then enable Chromium only for
`no_media_detected`/confirmed parsing regressions. Track incremental success attributable to the
browser. If the incremental rate is negligible, remove it rather than maintaining a universal
fallback in name only.

### Phase 4 — Decide the supported promise

Promote a platform from experimental only after a defined observation window meets explicit SLOs,
session ownership is documented, and somebody owns break/fix work. Otherwise, retain recognition of
the URL but reject it immediately with a current capability reason.

## 7. Acceptance-Criteria Mapping

| Existing criterion | Research-backed completion test |
| --- | --- |
| Decide supported/experimental/blocked | Experimental now; capability registry controls future promotion/degradation |
| Reproducible happy path per platform | Instagram extraction is locally reproduced on current stable; both still need non-local full-download/mux validation |
| Document session/cookie approach | Dedicated platform account/profile, read-only secret, health/expiry, renewal and revocation runbook |
| Expose platform-specific failures | Store stable public error code separately from sanitised operator detail |
| Regression coverage | Stable + nightly, anonymous + cookie A/B, at least two egress environments, fixture-health checks |

## Key Takeaways

1. Upgrade before redesigning: the exact Instagram failure is already fixed upstream.
2. Treat TikTok's current result as `ip_blocked`; a same-VM browser is not a meaningful fallback for
   that signal.
3. Make support a measured runtime capability, not a hostname accepted by validation.
4. Use Cobalt/gallery-dl as shadow evidence generators before accepting their maintenance and
   licensing costs.
5. Reserve browser automation for the small failure class where executing the page can reveal an
   otherwise undiscovered unencrypted stream.

## Sources

1. [yt-dlp 2026.07.04 release](https://github.com/yt-dlp/yt-dlp/releases/tag/2026.07.04) — current extractor changes; primary project source.
2. [yt-dlp README](https://github.com/yt-dlp/yt-dlp/blob/master/README.md) — release channels, formats, dependencies, and impersonation; primary project source.
3. [yt-dlp FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ) — official cookie/session guidance and risks; primary project source.
4. [yt-dlp supported sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md) — extractor list and reliability caveat; primary project source.
5. [yt-dlp TikTok extractor](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/tiktok.py) — current challenge/login/IP-block behaviour; source code.
6. [yt-dlp Instagram extractor](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/instagram.py) — current session and failure behaviour; source code.
7. [Playwright network documentation](https://playwright.dev/docs/network) — network and WebSocket observation plus Service Worker caveat; official docs.
8. [Playwright authentication documentation](https://playwright.dev/docs/auth) — browser-state isolation and credential warning; official docs.
9. [Chrome DevTools Protocol Network domain](https://chromedevtools.github.io/devtools-protocol/tot/Network/) — response and cookie primitives; official protocol docs.
10. [Chrome webRequest API](https://developer.chrome.com/docs/extensions/reference/api/webRequest) — extension observation and Manifest V3 limitations; official docs.
11. [Streamlink CLI](https://streamlink.github.io/cli.html) — stream extraction/download backend; official docs.
12. [N_m3u8DL-RE](https://github.com/nilaoda/N_m3u8DL-RE) — MPD/M3U8/ISM download backend; primary project source.
13. [Cobalt API README](https://github.com/imputnet/cobalt/blob/main/api/README.md) — supported platforms, self-hosting, and licence; primary project source.
14. [Cobalt API contract](https://github.com/imputnet/cobalt/blob/main/docs/api.md) — structured responses and public-instance restriction; primary project source.
15. [Cobalt TikTok implementation](https://github.com/imputnet/cobalt/blob/main/api/src/processing/services/tiktok.js) — extraction mechanism; source code.
16. [Cobalt Instagram implementation](https://github.com/imputnet/cobalt/blob/main/api/src/processing/services/instagram.js) — extraction mechanism; source code.
17. [gallery-dl](https://github.com/mikf/gallery-dl) — project status, authentication, and capabilities; primary project source.
18. [gallery-dl TikTok extractor](https://github.com/mikf/gallery-dl/blob/master/gallery_dl/extractor/tiktok.py) — rehydration and optional yt-dlp delegation; source code.
19. [gallery-dl Instagram extractor](https://github.com/mikf/gallery-dl/blob/master/gallery_dl/extractor/instagram.py) — REST/GraphQL and cookie paths; source code.
20. [Instaloader](https://github.com/instaloader/instaloader) — Instagram-specific alternative and session-file model; primary project source.
21. [TikTok Query Videos](https://developers.tiktok.com/doc/tiktok-api-v2-video-query/) — authorised-user ownership and returned fields; official platform docs.
22. [TikTok Video Object](https://developers.tiktok.com/doc/tiktok-api-v2-video-object/) — official share/embed metadata fields; official platform docs.
23. [TikTok Embedded Videos](https://developers.tiktok.com/doc/embed-videos/) — official oEmbed behaviour; official platform docs.
24. [Meta Instagram API collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api) — professional-account scope and limitations; official Meta collection.

## Methodology

- Reproduced both issue fixtures using the locked project version and current stable yt-dlp in
  simulation mode; no media was downloaded.
- Ran 21 Firecrawl searches across extractor drift, authentication, official APIs, browser capture,
  stream backends, and alternative engines. Several official-domain searches returned no indexed
  results, so known primary URLs were scraped directly.
- Attempted 21 Firecrawl page scrapes and deep-read the primary sources most relevant to the final
  ranking. GitHub source files were additionally verified through the GitHub API.
- Firecrawl search feedback was attempted immediately, but the configured deployment returned
  `DB_DISABLED`; no retry loop was used. Jina MCP was not available, so built-in search was used only
  to recover official TikTok/Meta documentation.
- Sub-questions: current failure causes; extractor/session improvements; independent engines;
  browser and protocol boundaries; official API alternatives; capability/error product design; and
  regression operations.
