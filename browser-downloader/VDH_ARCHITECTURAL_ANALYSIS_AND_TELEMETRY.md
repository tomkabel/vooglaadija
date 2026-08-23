# 1. ARCHITECTURAL OVERVIEW & CHROMIUM PRIMITIVES

## 1.1 WebExtensions API Entry Points

The extension (v10.5.10.2, MV3) consumes the following Chromium extension primitives:

| API Surface | Call Site | Operational Role |
|:---|:---|:---|
| `chrome.webRequest.onSendHeaders` | `service/main.js:15182` | Captures `requestHeaders` per `requestId` into a `Map<requestId, [timestamp, headers]>` with 600s TTL, targeting `<all_urls>` for types `xmlhttprequest`, `media`, `main_frame`, `sub_frame`, `other`. Uses `extraHeaders` opt-in on Chrome 72+ for `Cookie`/`Authorization` visibility. |
| `chrome.webRequest.onResponseStarted` | `service/main.js:15198` | Triggers `f896()` — the core detection pipeline — inspecting `content-type` response headers and URL patterns. |
| `chrome.declarativeNetRequest.updateSessionRules` | `service/main.js:11248,11261` | Dynamically adds/removes per-request header modification rules for download worker HTTP requests, injecting `Origin` and `Referer` where needed. |
| `chrome.offscreen.createDocument` | `service/main.js:9185` | Spawns a persistent offscreen document (`/factory/factory.html`) with `Reason.WORKERS` to host the FFmpeg `Worker` instance. |
| `chrome.scripting.executeScript` | `service/main.js:15251` | Injects DOM-scraping functions into target tabs to extract `<video>` poster thumbnails and page titles. |
| `chrome.downloads` | Implicit via `downloads` permission | Receives OPFS-backed blob URLs from the download worker for final write to the user's download directory. |
| `chrome.storage` | Persistent | Stores configuration, download history, and premium license state in extension-local storage. |
| `chrome.sidePanel` | Manifest-declared | Hosts the extension UI in Chromium's side panel (`/content/sidebar.html`). |

The extension does **not** consume `chrome.debugger` (CDP), `chrome.webRequest.onBeforeRequest` (blocking mode), or `chrome.tabs.captureVisibleTab`.

## 1.2 Intra-Extension Communication Topology

```
┌──────────────────────────────────────────────────┐
│                   MAIN WORLD                     │
│  youtube_untrusted.js  vimeo_untrusted.js  ...   │
│  Reads: window.ytcfg, window.playerConfig        │
│        │ BroadcastChannel("injected-{hash(url)}")│
│        │ channel: 4 (Untrusted→Trusted)          │
│        ▼                                         │
│                  ISOLATED WORLD                   │
│  youtube.js   vimeo.js   facebook.js   ...       │
│  Parses player responses, InnerTube API          │
│        │ runtime.sendMessage({channel:0})         │
│        │ channel: 0 (Injected→Service)            │
│        ▼                                         │
│              SERVICE WORKER (module)              │
│  service/main.js  (31,063 lines bundled)          │
│  webRequest listeners, M3U8/MPD parsers,          │
│  media discovery, download orchestration          │
│        │ BroadcastChannel("worker_service")       │
│        │ channel: 2 (Service→Worker)              │
│        ▼                                         │
│            OFFSCREEN DOCUMENT                     │
│  factory/factory.html → factory/factory.js        │
│        │ new Worker("/download_worker/main.js")   │
│        ▼                                         │
│            DOWNLOAD WORKER                        │
│  FFmpeg 6.5.7.1 WASM (LibAV.js bridge)           │
│  jsfetch HTTP backend, OPFS I/O                  │
│        │ BroadcastChannel("worker_service")       │
│        │ channel: 3 (Worker→Service)              │
│        ▼                                         │
│              SERVICE WORKER                       │
│  chrome.downloads API → user filesystem           │
└──────────────────────────────────────────────────┘
```

**Channel enumeration** (duplicated in every module, 9 values):

```
FromInjectedToService         = 0  (ISOLATED → SW via runtime.sendMessage)
FromContentToService          = 1  (UI pages → SW via runtime.sendMessage)
FromServiceToWorker           = 2  (SW → download Worker via BroadcastChannel("worker_service"))
FromWorkerToService           = 3  (download Worker → SW via BroadcastChannel("worker_service"))
FromUntrustedInjectedToTrusted = 4 (MAIN → ISOLATED via per-tab BroadcastChannel)
FromTrustedInjectedToUntrusted = 5 (ISOLATED → MAIN via per-tab BroadcastChannel)
FromServiceToContent          = 6  (SW → UI via runtime.sendMessage)
FromServiceToInjected         = 7  (SW → ISOLATED via runtime.sendMessage)
FromServiceToService          = 8  (SW internal dispatch)
```

**Per-tab BroadcastChannel bridging** (`youtube_untrusted.js:27`, `vimeo_untrusted.js:27`):
```javascript
var d = new BroadcastChannel(`injected-${i(window.location.href)}`);
```
A custom 32-bit hash (`Math.imul`-based Murmur-like) of `window.location.href` generates a unique per-page channel name, preventing cross-origin BroadcastChannel leakage while enabling MAIN↔ISOLATED world messaging without `window.postMessage`.

# 2. PROTOCOL DETECTION & MANIFEST INTERCEPTION MECHANISMS

## 2.1 Network Interception Pipeline

### 2.1.1 Header Collection Layer (`f897()`, `service/main.js:15161`)

```javascript
// Pseudocode reconstruction of the dual-listener pipeline
function f897_init() {
  let headerStore = new Map(); // requestId → [timestamp, requestHeaders[]]
  const TTL_MS = 600_000;     // 10-minute header retention
  const TARGET_TYPES = ["xmlhttprequest", "media", "main_frame", "sub_frame", "other"];

  // Listener 1: Capture request headers
  chrome.webRequest.onSendHeaders.addListener(details => {
    if (details.requestHeaders) {
      headerStore.set(details.requestId, [details.timeStamp, details.requestHeaders]);
    }
    // Periodic TTL cleanup every 600s
    if (details.timeStamp - lastCleanup > TTL_MS) cleanup();
  }, { urls: ["<all_urls>"], types: TARGET_TYPES }, ["requestHeaders", "extraHeaders"]);

  // Listener 2: Process response
  chrome.webRequest.onResponseStarted.addListener(details => {
    f896_detect(headerStore, details);
  }, { urls: ["<all_urls>"], types: TARGET_TYPES }, ["responseHeaders", "extraHeaders"]);
}
```

The `extraHeaders` option (Chrome 72+) is critical for accessing `Cookie`, `Authorization`, `Origin`, and `Referer` headers that would otherwise be redacted by Chromium's default header filtering.

### 2.1.2 Detection Dispatch (`f896()`, `service/main.js:15132`)

Decision tree executed per intercepted response:

```
response.headers["content-type"]
    │
    ├── matches /mpegurl/i ────────────────────────────► f889() HLS Parser
    │
    ├── matches /dash/i ───────────────────────────────► f890() DASH Parser
    │
    ├── (no match) + URL matches /hls|m3u8|\.mpd/i ──► retry as HLS/DASH
    │
    └── (no match) + type=="media" OR MIME is video/* ─► f894() HTTP direct media
```

## 2.2 HLS (HTTP Live Streaming)

### 2.2.1 Manifest Interception

The `f889()` function fetches and parses `.m3u8` playlists using `m3u8-parser` v7.2.0 (embedded, bundled at `service/main.js:10000-11000`). The parser produces an AST with `playlists[]` (variant streams), `segments[]` (media segments), and `contentProtection{}` (DRM descriptor).

### 2.2.2 `#EXT-X-KEY` Tag Processing (`service/main.js:10350-10417`)

The parser handles the following KEYFORMAT discriminations:

| KEYFORMAT | Mapped System ID | Behavior |
|:---|:---|:---|
| `com.apple.streamingkeydelivery` | `com.apple.fps.1_0` | Stores attributes, no PSSH extraction |
| `com.microsoft.playready` | `com.microsoft.playready` | Stores URI as license server URL |
| `urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed` | `com.widevine.alpha` | Validates METHOD is `SAMPLE-AES`/`SAMPLE-AES-CTR`/`SAMPLE-AES-CENC`; extracts PSSH from base64 data URI via `f790(atob(uri.split(",")[1]))`; extracts keyId from `KEYID` attribute (stripping `0x` prefix) |
| `(none)` | AES-128 clear key | Stores `{method, uri, iv}` — no DRM flag |

### 2.2.3 AES-128 IV & Key URI Extraction

```javascript
// service/main.js:10405-10416 — reconstructed
if (!attributes.METHOD) {
  this.trigger("warn", { message: "defaulting key method to AES-128" });
}
a = {
  method: attributes.METHOD || "AES-128",
  uri: attributes.URI
};
if (typeof attributes.IV !== "undefined") {
  a.iv = attributes.IV;  // 128-bit hex string from #EXT-X-KEY:IV=0x...
}
```

The extension stores the key URI and IV but does **not** fetch the key or perform decryption — it relies on the download worker's FFmpeg to handle AES-128 key retrieval and segment decryption during the remux pipeline.

## 2.3 MPEG-DASH

### 2.3.1 MPD Parsing (`f890()`, `service/main.js:14300-14697`)

Uses `mpd-parser` v1.3.1 (bundled). The pipeline:

1. **Fetch MPD** → `DOMParser` parses XML
2. **Extract `<Period>` elements** → iterate `AdaptationSet` → iterate `Representation`
3. **Segment resolution** (`vF261()`):
   - `SegmentTemplate` → `$RepresentationID$`, `$Number$`, `$Time$` template expansion
   - `SegmentList` → explicit `SegmentURL` extraction with `media`/`mediaRange` attributes
   - `SegmentBase` → byte-range initialization segment resolution
   - `SegmentTimeline` → explicit `S@t`, `S@d`, `S@r` timeline construction
4. **BaseURL resolution** (`vF260()`): Hierarchical base URL resolution from MPD level → Period → AdaptationSet → Representation

### 2.3.2 Initialization Segment (`init.mp4`/`init.m4s`)

Extracted from `Initialization` elements within `SegmentTemplate` or `SegmentBase`. The parser maps `initialization` attributes (possibly templated) to their resolved URLs. These URLs are passed through to the download worker's FFmpeg pipeline as `-init_segment` or as the implicit initialization segment in a `-f dash` input.

### 2.3.3 ContentProtection Element Extraction (`service/main.js:14326-14337`)

UUID-to-key-system mapping:

```
┌──────────────────────────────────────────────────────┬──────────────────────────────┐
│ UUID                                                  │ Key System                    │
├──────────────────────────────────────────────────────┼──────────────────────────────┤
│ urn:uuid:1077efec-c0b2-4d02-ace3-3c1e52e2fb4b       │ org.w3.clearkey              │
│ urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed       │ com.widevine.alpha           │
│ urn:uuid:9a04f079-9840-4286-ab92-e65be0885f95       │ com.microsoft.playready      │
│ urn:uuid:f239e769-efa3-4850-9c16-a903c6932efb       │ com.adobe.primetime          │
│ urn:mpeg:dash:mp4protection:2011                     │ mp4protection                │
└──────────────────────────────────────────────────────┴──────────────────────────────┘
```

`<ContentProtection>` elements are propagated upward through the AdaptationSet → Representation hierarchy. The `f796()` function (`service/main.js:10858`) checks whether any `contentProtection` key matches the DRM set (`com.microsoft.playready`, `com.apple.streamingkeydelivery`, `com.widevine.alpha`), flagging the media as `has_drm: true`. Note: ClearKey (`org.w3.clearkey`) is **not** in this DRM set, meaning ClearKey-protected DASH content passes through undetected as unprotected media.

## 2.4 WebRTC / WHIP / WHEP

**No interception exists.** The extension contains zero references to `RTCPeerConnection`, `setLocalDescription`, `setRemoteDescription`, `createOffer`, `createAnswer`, or `addIceCandidate`. WebRTC-based streaming (WHIP ingestion, WHEP playback) is a blind spot in the current architecture.

## 2.5 WebSockets & WebTransport

**No interception exists.** The extension contains zero references to `WebSocket`, `WebTransport`, or `EventSource`. Since these transports bypass `chrome.webRequest` and use persistent binary frame-level delivery that does not emit traditional HTTP response events, they are architecturally invisible to the current interception pipeline.

# 3. W3C ENCRYPTED MEDIA EXTENSIONS (EME) ANALYSIS

## 3.1 EME Pipeline Hooking

**No EME hooking exists in the extension.** There are zero references to:
- `navigator.requestMediaKeySystemAccess`
- `MediaKeys`
- `MediaKeySession`
- `createSession`
- `generateRequest`
- `update`
- `keyStatuses`
- `onkeystatuseschange`

The architecture is entirely passive — it reads `ContentProtection` metadata from M3U8 `#EXT-X-KEY` tags and DASH `<ContentProtection>` elements in the MPD XML, but never interacts with the browser's EME subsystem.

## 3.2 PSSH Box Extraction

**PSSH extraction occurs at `service/main.js:10401`** via `f790(atob(uri.split(",")[1]))`, which decodes the `data:text/plain;base64,...` payload from Widevine `#EXT-X-KEY` tags. The function `f790()` is a binary PSSH parser that:

1. Decodes the base64 payload to an `ArrayBuffer`
2. Validates the box header (4-byte size, 4-byte type `pssh`)
3. Extracts the 16-byte System ID at offset 12
4. Parses the 4-byte data size at offset 28
5. Extracts the PSSH data payload (typically the Widevine CENC initData)

The PSSH is stored in `contentProtection["com.widevine.alpha"].pssh` but is **used only for UI display** — no license server communication occurs.

## 3.3 License & Challenge Capture

**Not implemented.** The extension never generates EME license requests or captures license responses. Content protection is a binary flag (`has_drm: true`) that grays out the download button with a DRM warning icon.

## 3.4 ClearKey Decryption Mechanics

**Indirect pass-through.** ClearKey (UUID `1077efec-c0b2-4d02-ace3-3c1e52e2fb4b`) is mapped in the UUID table but **excluded** from the DRM check set (`v369`). This means:

1. ClearKey-protected MPDs are detected as **unprotected** media
2. The download worker's FFmpeg receives the manifest URL with segments
3. If the segments contain `cenc` encryption with ClearKey, FFmpeg may or may not handle this depending on its build configuration (the shipped `libav-6.5.7.1-h264-aac-mp3` WASM build likely does **not** include decryption support)

No automated `kid`/`key` extraction or ClearKey matrix capture is performed.

# 4. STREAM SEPARATION, RECONSTRUCTION & ASSEMBLY

## 4.1 Segmented Streams

### 4.1.1 HLS Segment Tracking

The `m3u8-parser` constructs a `segments[]` array where each entry contains:
- `uri` — absolute segment URL
- `duration` — `#EXTINF` value in seconds
- `timeline` — discontinuity sequence counter
- `key` — `{method, uri, iv}` for AES-128 encrypted segments
- `map` — `{uri, byterange}` for fMP4 initialization segments

The service worker passes the master playlist and variant playlist URLs to the download worker. The download worker's FFmpeg handles segment fetch, decryption (if AES-128 and unencrypted key is reachable), and concatenation natively.

### 4.1.2 DASH Segment Tracking

The `mpd-parser` produces segment descriptors with resolved URLs using template expansion. Representational template variables (`$RepresentationID$`, `$Number$`, `$Time$`) are expanded against the MPD timeline. Segment URLs are passed through to FFmpeg's `-f dash` demuxer, which handles the ISO BMFF parsing, segment boundary detection, and timeline reconstruction.

## 4.2 Muxing & Assembly Pipelines

### 4.2.1 Browser-Resident Assembly (Not Implemented)

The extension does **not** perform browser-side muxing using `ReadableStream`/`WritableStream` or the `MediaSource` API. All assembly is delegated to the download worker's FFmpeg WASM instance.

### 4.2.2 FFmpeg WASM Download Worker Pipeline

The download worker (`download_worker/main.js`, 9,597 lines) bundles `libav-6.5.7.1` compiled to WebAssembly with:

| Component | Details |
|:---|:---|
| **Input backends** | `jsfetch:` custom protocol (HTTP Fetch API wrapper with retry/timeout), OPFS file:// paths |
| **Output backends** | OPFS `FileSystemSyncAccessHandle` via `createSyncAccessHandle()` |
| **Demuxers** | `hls`, `dash`, `mp4`, `webm`, `flv`, `mpegts` |
| **Decoders** | `h264`, `aac`, `mp3` |
| **Muxers** | `mp4`, `mp3` |
| **Codecs** | `libx264` (encoder, not used for copy mode), `libmp3lame` (encoder for audio-only) |

**Download strategy dispatch** (reconstructed from the 9,597-line bundle):

```
Description: "dash" type, video track available, audio track available
Command: ffmpeg -analyzeduration 10M -f dash -i jsfetch:{VIDEO_URL} \
           -map 0:{VIDEO_STREAM_IDX} -map 0:a:0? \
           -c copy -y {OUTPUT}.mp4

Description: "dash" type, audio-only
Command: ffmpeg -analyzeduration 10M -f dash -i jsfetch:{AUDIO_URL} \
           -map 0:a:0 -c:a libmp3lame -y {OUTPUT}.mp3

Description: "m3u8" type with separate audio/video playlists
Command: (first pass) ffmpeg -i jsfetch:{VIDEO_PLAYLIST} -map 0:{V} -c copy -y temp_video.m4s
         (second pass) ffmpeg -i jsfetch:{AUDIO_PLAYLIST} -map 0:{A} -c copy -y temp_audio.m4s
         (join)        ffmpeg -i temp_video.m4s -i temp_audio.m4s -c copy -y {OUTPUT}.mp4

Description: Preview (3-second sample)
Command: ffmpeg -analyzeduration 1M -f dash -i jsfetch:{URL} \
           -map 0:{VIDEO_IDX} -t 3 -c copy -y {OUTPUT}.mp4
```

**Progress reporting**: Every 500ms, the worker posts `{name: "download_progress", data: {bytes, duration, percentage}}` via `BroadcastChannel("worker_service")` to the service worker, which forwards to the UI.

# 5. 2026/2027 SOTA INDUSTRY STANDARDS COMPARISON

| Vector | Video DownloadHelper Approach | 2026/2027 SoTA Industry Standard |
|:---|:---|:---|
| **Network Sniffing** | `chrome.webRequest` passive listeners on `onSendHeaders`/`onResponseStarted` — relies on HTTP-level interception of XMLHttpRequest and media element fetches. Blind to Service Worker-intercepted requests, opaque `fetch()` with `no-cors`, and WebTransport streams. | Multi-vector interception combining `chrome.debugger` (CDP `Network.*` domain for request/response body capture), `chrome.declarativeNetRequest` for real-time header modification, and `SharedArrayBuffer`-backed in-process memory ring buffers for streaming body interception. Service Worker `fetch` event interception via `navigator.serviceWorker.getRegistrations()` introspection. |
| **Session Isolation Bypass** | Per-tab `BroadcastChannel("injected-{hash(url)}")` for MAIN↔ISOLATED world bridging. Site-specific `_untrusted.js` scripts that poll for `window.ytcfg`/`window.playerConfig` with exponential backoff. | `window.__proto__` property descriptor traps (`Object.defineProperty` on `HTMLMediaElement.prototype`) to intercept player framework initialization before site scripts bind. `Proxy`-wrapped `window` objects in MAIN world injected via `world: 'MAIN'` with `run_at: 'document_start'`. Cross-origin iframe recursion via `chrome.scripting.executeScript` with `allFrames: true` and recursive shadow DOM traversal. |
| **Segment Assembly** | Delegated entirely to FFmpeg 6.5.7.1 WASM in a dedicated Web Worker. Byte-range HTTP requests handled by a custom `jsfetch:` protocol backend with retry and abort logic. Single-threaded WASM (no SharedArrayBuffer threading). | Hybrid assembly using `ReadableStream.pipeThrough(new TransformStream())` for in-browser segment concatenation with WASM-based demuxer only for container transcoding. Multi-threaded FFmpeg with `SharedArrayBuffer` + `Atomics` for zero-copy data transfer. OPFS `FileSystemSyncAccessHandle` with `createWritable()` streaming writes. Parallel segment prefetching via `Promise.allSettled()` with priority-ordered fetch queues. |
| **DRM/EME / ClearKey Handling** | Passive DRM detection from `#EXT-X-KEY` KEYFORMAT tags and `<ContentProtection>` elements. No EME API interaction. ClearKey excluded from the DRM check set, creating a false-negative for ClearKey-protected streams. | Active EME lifecycle hooking via prototype interception: wrap `navigator.requestMediaKeySystemAccess` → intercept `MediaKeys.createSession` → proxy `MediaKeySession.generateRequest`/`update` → capture `initData` (for PSSH parsing) and license responses (for `kid`/`key` ClearKey matrix extraction). Hex-parsed PSSH box decomposition with System ID, KID count, and key data payload extraction. Automated ClearKey `kid:key` matrix assembly from `MediaKeySession.update()` calls with JSON Base64/hex dual-format output for offline `mp4decrypt` or `shaka-packager` consumption. |

# 6. EDGE CASES, ANTI-EVASION, AND PROTOCOL MITIGATIONS

## 6.1 Signed Cookies & Dynamic Token Rotation

**Vulnerability**: The `onSendHeaders` → `onResponseStarted` architecture captures headers at two discrete time points. If a streaming service rotates authentication tokens per-segment (e.g., CloudFront signed URLs with per-segment `Policy`/`Signature`/`Key-Pair-Id` parameters), the captured headers may be stale by the time the download worker initiates its own fetch.

**Impact**: Segment fetch failures with HTTP 403 after the first successful segments.

**Mitigation gap**: No `chrome.webRequest.onBeforeSendHeaders` listener to synchronously inject fresh headers, and no callback-based token refresh mechanism. The `declarativeNetRequest` integration is static (rule-based) and cannot execute dynamic token refresh logic.

## 6.2 HTTP/3 QUIC Stream Masking

**Vulnerability**: Chrome 124+ defaults to HTTP/3 (QUIC) for many streaming CDNs. QUIC streams are multiplexed over a single UDP connection, and `chrome.webRequest` event ordering and timing semantics differ from HTTP/1.1 and HTTP/2. Response body content is encrypted at the QUIC transport layer and is not directly observable via `onResponseStarted`.

**Impact**: The `onResponseStarted` event may fire with a `content-type` of `application/octet-stream` or `application/x-mpegurl` but without access to the actual manifest body. The extension must re-fetch the manifest via its own `fetch()` call, which may carry different cookies/headers than the original media element load.

**Mitigation gap**: No `chrome.debugger` CDP `Network.getResponseBody` fallback for QUIC streams.

## 6.3 Persistent WebSocket-Based Media Delivery

**Vulnerability**: WebSocket connections bypass `chrome.webRequest` entirely. A streaming service that delivers HLS/DASH manifests over a WebSocket (e.g., `wss://stream.example.com/control`, with binary-encoded M3U8 updates delivered as `ArrayBuffer` frames) is completely invisible to the current interception pipeline.

**Impact**: Zero detection for WebSocket-delivered streaming manifests.

**Mitigation gap**: No `WebSocket` constructor monkeypatching in content scripts or injected code. No `EventTarget.prototype.addEventListener` wrapping for `message` events.

## 6.4 Service Worker-Cached Responses

**Vulnerability**: If a page registers a Service Worker that caches media manifests (increasingly common for offline-first streaming PWAs), `chrome.webRequest` listeners see the initial fetch but subsequent cache hits bypass the listener entirely.

**Impact**: Manifest detection fails on cache hits.

**Mitigation gap**: No `navigator.serviceWorker.getRegistrations()` introspection or Cache API inspection.

# 7. DIAGNOSTIC TELEMETRY & API INTERCEPTION PAYLOAD

```javascript
/**
 * ============================================================================
 * VIDEO DOWNLOAD HELPER — DIAGNOSTIC TELEMETRY & PROTOCOL INTERCEPTION SUITE
 * ============================================================================
 *
 * Execution Model:
 *   Module A (Event Bus) → Module B (Fetch/XHR)  ┐
 *   Module A (Event Bus) → Module C (MSE Source)  ├─ Parallel Hooks
 *   Module A (Event Bus) → Module D (WebRTC/WS)   │
 *   Module A (Event Bus) → Module E (EME/ClearKey)┘
 *     → Module F (Report Logger) subscribes to bus events
 *
 * IMPORTANT: Module A MUST produce a resolved bus handle before B—E execute.
 * This is enforced by sequential await in the Top-Level IIFE.
 *
 * License: MIT — Non-destructive diagnostic instrumentation only.
 * ============================================================================
 */

"use strict";

// ===========================================================================
// MODULE A: CORE EVENT BUS & ORCHESTRATION LAYER
// ===========================================================================

(function initializeTelemetryBus() {
  if (window.__telemetryBus !== void 0) {
    console.warn(
      "[Telemetry:A] __telemetryBus already initialized — skipping duplicate bootstrap"
    );
    return;
  }

  const SUBSCRIBERS = new Map();
  let NEXT_ID = 1;

  const bus = {
    publish(eventType, payload) {
      if (typeof eventType !== "string" || eventType.length === 0) {
        return;
      }
      if (payload === void 0) {
        payload = null;
      }
      const timestamp = Date.now();
      const envelope = Object.freeze({
        eventType,
        payload,
        timestamp,
        sessionId: bus.sessionId,
      });
      const listeners = SUBSCRIBERS.get(eventType);
      if (listeners !== void 0 && listeners.size > 0) {
        for (const [_, handler] of listeners) {
          try {
            handler(envelope);
          } catch (err) {
            console.error(
              `[Telemetry:A] Subscriber error for event "${eventType}":`,
              err
            );
          }
        }
      }
      const wildcardListeners = SUBSCRIBERS.get("*");
      if (wildcardListeners !== void 0 && wildcardListeners.size > 0) {
        for (const [_, handler] of wildcardListeners) {
          try {
            handler(envelope);
          } catch (err) {
            console.error(
              `[Telemetry:A] Wildcard subscriber error for event "${eventType}":`,
              err
            );
          }
        }
      }
    },

    subscribe(eventType, handler) {
      if (typeof eventType !== "string" || eventType.length === 0) {
        throw new TypeError(
          "[Telemetry:A] subscribe() requires a non-empty string eventType"
        );
      }
      if (typeof handler !== "function") {
        throw new TypeError(
          "[Telemetry:A] subscribe() requires a function handler"
        );
      }
      const id = NEXT_ID++;
      if (!SUBSCRIBERS.has(eventType)) {
        SUBSCRIBERS.set(eventType, new Map());
      }
      SUBSCRIBERS.get(eventType).set(id, handler);
      return function unsubscribe() {
        const map = SUBSCRIBERS.get(eventType);
        if (map !== void 0) {
          map.delete(id);
          if (map.size === 0) {
            SUBSCRIBERS.delete(eventType);
          }
        }
      };
    },

    once(eventType, handler) {
      const unsub = bus.subscribe(eventType, (envelope) => {
        unsub();
        handler(envelope);
      });
      return unsub;
    },

    sessionId: `telemetry_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`,

    subscriberCount(eventType) {
      const map = SUBSCRIBERS.get(eventType);
      return map !== void 0 ? map.size : 0;
    },
  };

  Object.defineProperty(window, "__telemetryBus", {
    value: Object.freeze(bus),
    writable: false,
    configurable: false,
    enumerable: false,
  });

  console.info(
    "[Telemetry:A] Diagnostic event bus initialized. Session ID:",
    bus.sessionId
  );
})();

// ===========================================================================
// MODULE B: API HOOKING — MANIFESTS & HTTP STREAM INTERCEPTION
// ===========================================================================

(function installManifestHooks() {
  const bus = window.__telemetryBus;
  if (!bus) {
    console.error(
      "[Telemetry:B] __telemetryBus not found — aborting hook installation"
    );
    return;
  }

  const MANIFEST_EXTENSIONS = new Set([
    "m3u8",
    "mpd",
    "m3u",
  ]);
  const MANIFEST_MIME_PATTERNS = [
    /^application\/(x-)?mpegurl/i,
    /^application\/dash\+xml/i,
    /^application\/vnd\.apple\.mpegurl/i,
    /^audio\/(x-)?mpegurl/i,
    /^video\/(x-)?mpegurl/i,
  ];

  function isLikelyManifest(url, contentType) {
    if (!url || typeof url !== "string") {
      return false;
    }
    try {
      const pathname = new URL(url, location.origin).pathname;
      const ext = pathname.split(".").pop()?.toLowerCase();
      if (ext && MANIFEST_EXTENSIONS.has(ext)) {
        return true;
      }
    } catch (_) {
      /* malformed URL — skip extension check */
    }
    if (contentType && typeof contentType === "string") {
      for (const pattern of MANIFEST_MIME_PATTERNS) {
        if (pattern.test(contentType)) {
          return true;
        }
      }
    }
    const lower = url.toLowerCase();
    if (lower.includes(".m3u8") || lower.includes(".mpd")) {
      return true;
    }
    return false;
  }

  // --- fetch() hook ---
  const nativeFetch = window.fetch;
  window.fetch = function hookedFetch(input, init) {
    const requestUrl =
      input instanceof Request ? input.url : String(input || "");
    const requestHeaders = new Headers(
      input instanceof Request
        ? input.headers
        : init && init.headers
        ? init.headers
        : void 0
    );

    const startTime = performance.now();
    const resultPromise = nativeFetch.call(this, input, init);

    resultPromise
      .then((response) => {
        const duration = Math.round(performance.now() - startTime);
        const contentType = response.headers.get("content-type") || "";
        const contentLength = response.headers.get("content-length") || "";
        const cloned = response.clone();
        if (isLikelyManifest(requestUrl, contentType)) {
          cloned
            .text()
            .then((body) => {
              const trimmed =
                body.length > 8192 ? body.slice(0, 8192) + "..." : body;
              bus.publish("fetch:manifest", {
                url: requestUrl,
                method: init?.method || "GET",
                status: response.status,
                contentType,
                contentLength: contentLength
                  ? parseInt(contentLength, 10)
                  : null,
                headers: Object.fromEntries(response.headers.entries()),
                requestHeaders: Object.fromEntries(requestHeaders.entries()),
                bodyPreview: trimmed,
                bodyLength: body.length,
                durationMs: duration,
                type: contentType.includes("dash") ? "dash" : "hls",
              });
            })
            .catch((readErr) => {
              bus.publish("fetch:manifest:error", {
                url: requestUrl,
                error: readErr.message || String(readErr),
                contentType,
                status: response.status,
                durationMs: duration,
              });
            });
        } else if (
          /video\//i.test(contentType) ||
          /audio\//i.test(contentType)
        ) {
          bus.publish("fetch:media_stream", {
            url: requestUrl,
            type: contentType.startsWith("video") ? "video" : "audio",
            contentType,
            contentLength: contentLength
              ? parseInt(contentLength, 10)
              : null,
            status: response.status,
            requestHeaders: Object.fromEntries(requestHeaders.entries()),
            durationMs: duration,
          });
        }
      })
      .catch((fetchErr) => {
        const duration = Math.round(performance.now() - startTime);
        if (isLikelyManifest(requestUrl, "")) {
          bus.publish("fetch:manifest:error", {
            url: requestUrl,
            error: fetchErr.message || String(fetchErr),
            durationMs: duration,
          });
        }
      });

    return resultPromise;
  };
  console.info("[Telemetry:B] window.fetch hooked for manifest interception");

  // --- XMLHttpRequest hook ---
  const NativeXHR = window.XMLHttpRequest;
  const originalOpen = NativeXHR.prototype.open;
  const originalSetRequestHeader = NativeXHR.prototype.setRequestHeader;

  NativeXHR.prototype.open = function hookedXHROpen(
    method,
    url,
    async = true,
    user,
    password
  ) {
    this.__telemetry_url = String(url || "");
    this.__telemetry_method = String(method || "GET");
    this.__telemetry_requestHeaders = {};
    return originalOpen.call(this, method, url, async, user, password);
  };

  NativeXHR.prototype.setRequestHeader = function hookedSetRequestHeader(
    name,
    value
  ) {
    if (this.__telemetry_requestHeaders) {
      this.__telemetry_requestHeaders[name] = String(value);
    }
    return originalSetRequestHeader.call(this, name, value);
  };

  const originalSend = NativeXHR.prototype.send;
  NativeXHR.prototype.send = function hookedXHRSend(body) {
    const xhr = this;
    const url = xhr.__telemetry_url || "";
    const method = xhr.__telemetry_method || "GET";
    const requestHeaders = xhr.__telemetry_requestHeaders || {};
    const startTime = performance.now();

    const onReadyState = function () {
      if (xhr.readyState !== 4) {
        return;
      }
      const duration = Math.round(performance.now() - startTime);
      const contentType =
        xhr.getResponseHeader("content-type") || "";
      const contentLength =
        xhr.getResponseHeader("content-length") || "";

      if (isLikelyManifest(url, contentType)) {
        const body =
          xhr.responseText.length > 8192
            ? xhr.responseText.slice(0, 8192) + "..."
            : xhr.responseText;
        bus.publish("xhr:manifest", {
          url,
          method,
          status: xhr.status,
          contentType,
          contentLength: contentLength
            ? parseInt(contentLength, 10)
            : null,
          requestHeaders,
          responseHeaders: xhr
            .getAllResponseHeaders()
            .split("\r\n")
            .filter((line) => line.includes(":"))
            .reduce((acc, line) => {
              const [k, ...v] = line.split(":");
              acc[k.trim().toLowerCase()] = v.join(":").trim();
              return acc;
            }, {}),
          bodyPreview: body,
          bodyLength: xhr.responseText.length,
          durationMs: duration,
          type: contentType.includes("dash") ? "dash" : "hls",
        });
      } else if (
        /video\//i.test(contentType) ||
        /audio\//i.test(contentType)
      ) {
        bus.publish("xhr:media_stream", {
          url,
          method,
          type: contentType.startsWith("video") ? "video" : "audio",
          contentType,
          contentLength: contentLength
            ? parseInt(contentLength, 10)
            : null,
          status: xhr.status,
          requestHeaders,
          durationMs: duration,
        });
      }
    };

    xhr.addEventListener("readystatechange", onReadyState, { once: false });
    return originalSend.call(this, body);
  };
  console.info("[Telemetry:B] XMLHttpRequest.prototype hooked");
})();

// ===========================================================================
// MODULE C: MSE SOURCE BUFFER INTERCEPTION — INIT SEGMENT CAPTURE
// ===========================================================================

(function installMSEHooks() {
  const bus = window.__telemetryBus;
  if (!bus) {
    console.error("[Telemetry:C] __telemetryBus not found — aborting MSE hook");
    return;
  }

  if (
    typeof MediaSource === "undefined" ||
    !MediaSource.prototype ||
    !MediaSource.prototype.addSourceBuffer
  ) {
    console.info("[Telemetry:C] MediaSource API not available in this context");
    return;
  }

  const nativeAddSourceBuffer = MediaSource.prototype.addSourceBuffer;
  MediaSource.prototype.addSourceBuffer = function hookedAddSourceBuffer(
    mimeType
  ) {
    const sourceBuffer = nativeAddSourceBuffer.call(this, mimeType);
    bus.publish("ms:sourcebuffer_added", {
      mimeType,
      mode: sourceBuffer.mode,
    });

    if (
      typeof SourceBuffer !== "undefined" &&
      !SourceBuffer.prototype.__telemetry_appendBuffer_hooked
    ) {
      const nativeAppendBuffer = SourceBuffer.prototype.appendBuffer;
      SourceBuffer.prototype.appendBuffer = function hookedAppendBuffer(data) {
        if (
          data instanceof ArrayBuffer ||
          ArrayBuffer.isView(data)
        ) {
          const buffer =
            data instanceof ArrayBuffer
              ? new Uint8Array(data)
              : new Uint8Array(
                  data.buffer,
                  data.byteOffset,
                  data.byteLength
                );
          const hex = Array.from(buffer.slice(0, 256))
            .map((b) => b.toString(16).padStart(2, "0"))
            .join("");
          const isInitSegment = detectInitSegment(buffer);
          if (isInitSegment) {
            bus.publish("ms:init_segment", {
              byteLength: buffer.byteLength,
              hexPreview: hex,
              hexPreviewTruncated: buffer.byteLength > 256,
              isoBoxes: parseISOBoxHeaders(buffer),
            });
          }
          bus.publish("ms:segment", {
            byteLength: buffer.byteLength,
            hexPreview: hex,
            hexPreviewTruncated: buffer.byteLength > 256,
            isInitSegment,
          });
        }
        return nativeAppendBuffer.call(this, data);
      };
      SourceBuffer.prototype.__telemetry_appendBuffer_hooked = true;
      console.info(
        "[Telemetry:C] SourceBuffer.prototype.appendBuffer hooked"
      );
    }

    return sourceBuffer;
  };
  console.info("[Telemetry:C] MediaSource.prototype.addSourceBuffer hooked");

  function detectInitSegment(buffer) {
    if (buffer.byteLength < 8) {
      return false;
    }
    const ftypOffset = indexOfBox(buffer, 0x66747970); // 'ftyp'
    const moovOffset = indexOfBox(buffer, 0x6d6f6f76); // 'moov'
    const pdinOffset = indexOfBox(buffer, 0x7064696e); // 'pdin'
    return (
      ftypOffset !== -1 ||
      moovOffset !== -1 ||
      pdinOffset !== -1
    );
  }

  function indexOfBox(buffer, boxType) {
    const dv = new DataView(
      buffer.buffer,
      buffer.byteOffset,
      buffer.byteLength
    );
    let offset = 0;
    while (offset + 8 <= buffer.byteLength) {
      const size = dv.getUint32(offset);
      const type = dv.getUint32(offset + 4);
      if (type === boxType) {
        return offset;
      }
      if (size === 0 || size < 8) {
        break;
      }
      offset += size;
    }
    return -1;
  }

  function parseISOBoxHeaders(buffer) {
    const boxes = [];
    const dv = new DataView(
      buffer.buffer,
      buffer.byteOffset,
      buffer.byteLength
    );
    let offset = 0;
    while (offset + 8 <= buffer.byteLength) {
      let size = dv.getUint32(offset);
      const type = dv.getUint32(offset + 4);
      if (size === 0) {
        break;
      }
      if (size === 1 && offset + 16 <= buffer.byteLength) {
        const high = dv.getUint32(offset + 8);
        const low = dv.getUint32(offset + 12);
        size = Number((BigInt(high) << 32n) | BigInt(low));
      }
      if (size < 8) {
        break;
      }
      const typeStr = String.fromCharCode(
        (type >> 24) & 0xff,
        (type >> 16) & 0xff,
        (type >> 8) & 0xff,
        type & 0xff
      );
      boxes.push({
        type: typeStr,
        size,
        offset,
      });
      offset += size;
      if (offset >= buffer.byteLength) {
        break;
      }
    }
    return boxes;
  }
})();

// ===========================================================================
// MODULE D: WEBRTC & WEBSOCKET TELEMETRY
// ===========================================================================

(function installWebRTCAndWebSocketHooks() {
  const bus = window.__telemetryBus;
  if (!bus) {
    console.error(
      "[Telemetry:D] __telemetryBus not found — aborting WebRTC/WS hooks"
    );
    return;
  }

  // --- RTCPeerConnection hooks ---
  if (
    typeof RTCPeerConnection !== "undefined" &&
    RTCPeerConnection.prototype
  ) {
    const proto = RTCPeerConnection.prototype;

    if (proto.setLocalDescription && !proto.__telemetry_sld_hooked) {
      const nativeSLD = proto.setLocalDescription;
      proto.setLocalDescription = function hookedSLD(desc) {
        if (desc && desc.sdp) {
          bus.publish("webrtc:local_description", {
            type: desc.type || "unknown",
            sdp: desc.sdp,
          });
        }
        return nativeSLD.call(this, desc);
      };
      proto.__telemetry_sld_hooked = true;
    }

    if (proto.setRemoteDescription && !proto.__telemetry_srd_hooked) {
      const nativeSRD = proto.setRemoteDescription;
      proto.setRemoteDescription = function hookedSRD(desc) {
        if (desc && desc.sdp) {
          bus.publish("webrtc:remote_description", {
            type: desc.type || "unknown",
            sdp: desc.sdp,
          });
        }
        return nativeSRD.call(this, desc);
      };
      proto.__telemetry_srd_hooked = true;
    }

    console.info(
      "[Telemetry:D] RTCPeerConnection.prototype hooks installed"
    );
  }

  // --- WebSocket hooks ---
  if (typeof WebSocket !== "undefined") {
    const NativeWebSocket = WebSocket;
    const hookedMap = new WeakMap();

    const HookedWebSocket = function hookedWSConstructor(url, protocols) {
      const ws = new NativeWebSocket(url, protocols);
      hookedMap.set(ws, { url, startTime: performance.now() });
      bus.publish("ws:connect", { url, protocols: protocols || null });

      wrapWebSocketEvent(ws, "message", (event) => {
        const meta = hookedMap.get(ws);
        if (!meta) {
          return;
        }
        let dataType = "text";
        let byteLength = 0;
        let hexPreview = "";
        if (event.data instanceof ArrayBuffer) {
          dataType = "arraybuffer";
          byteLength = event.data.byteLength;
          const arr = new Uint8Array(event.data);
          hexPreview = Array.from(arr.slice(0, 128))
            .map((b) => b.toString(16).padStart(2, "0"))
            .join("");
        } else if (event.data instanceof Blob) {
          dataType = "blob";
          byteLength = event.data.size;
        } else if (ArrayBuffer.isView(event.data)) {
          dataType = "bufferview";
          byteLength = event.data.byteLength;
          const arr = new Uint8Array(
            event.data.buffer,
            event.data.byteOffset,
            event.data.byteLength
          );
          hexPreview = Array.from(arr.slice(0, 128))
            .map((b) => b.toString(16).padStart(2, "0"))
            .join("");
        }
        bus.publish("ws:message", {
          url: meta.url,
          dataType,
          byteLength,
          hexPreview,
          hexPreviewTruncated: byteLength > 128,
          elapsedMs: Math.round(performance.now() - meta.startTime),
        });
      });

      wrapWebSocketEvent(ws, "error", (event) => {
        const meta = hookedMap.get(ws);
        bus.publish("ws:error", {
          url: meta ? meta.url : "unknown",
          elapsedMs: meta
            ? Math.round(performance.now() - meta.startTime)
            : 0,
        });
      });

      wrapWebSocketEvent(ws, "close", (event) => {
        const meta = hookedMap.get(ws);
        bus.publish("ws:close", {
          url: meta ? meta.url : "unknown",
          code: event.code,
          reason: event.reason,
          wasClean: event.wasClean,
          elapsedMs: meta
            ? Math.round(performance.now() - meta.startTime)
            : 0,
        });
      });

      return ws;
    };

    HookedWebSocket.prototype = NativeWebSocket.prototype;
    HookedWebSocket.CONNECTING = NativeWebSocket.CONNECTING;
    HookedWebSocket.OPEN = NativeWebSocket.OPEN;
    HookedWebSocket.CLOSING = NativeWebSocket.CLOSING;
    HookedWebSocket.CLOSED = NativeWebSocket.CLOSED;

    Object.defineProperty(window, "WebSocket", {
      value: HookedWebSocket,
      writable: true,
      configurable: true,
      enumerable: true,
    });

    function wrapWebSocketEvent(ws, eventName, listener) {
      ws.addEventListener(eventName, function handler(e) {
        try {
          listener(e);
        } catch (err) {
          console.error(
            `[Telemetry:D] WebSocket ${eventName} handler error:`,
            err
          );
        }
      });
    }

    console.info("[Telemetry:D] WebSocket constructor wrapped");
  }
})();

// ===========================================================================
// MODULE E: EME & CLEARKEY MONITORING
// ===========================================================================

(function installEMEHooks() {
  const bus = window.__telemetryBus;
  if (!bus) {
    console.error(
      "[Telemetry:E] __telemetryBus not found — aborting EME hooks"
    );
    return;
  }

  if (
    typeof navigator === "undefined" ||
    typeof navigator.requestMediaKeySystemAccess !== "function"
  ) {
    console.info("[Telemetry:E] EME API not available in this context");
    return;
  }

  const nativeRMKSA = navigator.requestMediaKeySystemAccess.bind(navigator);
  navigator.requestMediaKeySystemAccess = function hookedRMKSA(
    keySystem,
    supportedConfigurations
  ) {
    bus.publish("eme:request_key_system", {
      keySystem,
      configCount: supportedConfigurations.length,
      configs: supportedConfigurations.map((cfg) => ({
        initDataTypes: cfg.initDataTypes || [],
        audioCapabilities: (cfg.audioCapabilities || []).map(
          (c) => c.contentType
        ),
        videoCapabilities: (cfg.videoCapabilities || []).map(
          (c) => c.contentType
        ),
        distinctiveIdentifier:
          cfg.distinctiveIdentifier || "not specified",
        persistentState: cfg.persistentState || "not specified",
      })),
    });

    return nativeRMKSA(keySystem, supportedConfigurations).then(
      (mediaKeySystemAccess) => {
        const nativeCreateMediaKeys =
          mediaKeySystemAccess.createMediaKeys.bind(mediaKeySystemAccess);
        mediaKeySystemAccess.createMediaKeys = function hookedCreateMediaKeys() {
          return nativeCreateMediaKeys().then((mediaKeys) => {
            if (
              mediaKeys.createSession &&
              !mediaKeys.__telemetry_createSession_hooked
            ) {
              const nativeCreateSession = mediaKeys.createSession.bind(
                mediaKeys
              );
              mediaKeys.createSession = function hookedCreateSession(
                sessionType
              ) {
                const session = nativeCreateSession(sessionType);
                bus.publish("eme:session_created", {
                  keySystem,
                  sessionType: sessionType || "temporary",
                });

                if (
                  session.generateRequest &&
                  !session.__telemetry_genRequest_hooked
                ) {
                  const nativeGenerateRequest =
                    session.generateRequest.bind(session);
                  session.generateRequest =
                    function hookedGenerateRequest(
                      initDataType,
                      initData
                    ) {
                      let initDataHex = "";
                      let initDataB64 = "";
                      let psshBoxes = [];

                      try {
                        if (initData instanceof ArrayBuffer) {
                          initDataB64 = arrayBufferToBase64(initData);
                          initDataHex = arrayBufferToHex(initData);
                          psshBoxes = parsePSSHBoxes(
                            new Uint8Array(initData)
                          );
                        } else if (ArrayBuffer.isView(initData)) {
                          const sliced = new Uint8Array(
                            initData.buffer,
                            initData.byteOffset,
                            initData.byteLength
                          );
                          initDataB64 = uint8ArrayToBase64(sliced);
                          initDataHex = uint8ArrayToHex(sliced);
                          psshBoxes = parsePSSHBoxes(sliced);
                        }
                      } catch (parseErr) {
                        console.warn(
                          "[Telemetry:E] initData parsing error:",
                          parseErr
                        );
                      }

                      bus.publish("eme:license_request", {
                        keySystem,
                        sessionType: sessionType || "temporary",
                        initDataType,
                        initDataLength:
                          initData instanceof ArrayBuffer
                            ? initData.byteLength
                            : ArrayBuffer.isView(initData)
                            ? initData.byteLength
                            : 0,
                        initDataHex,
                        initDataB64,
                        psshBoxes,
                      });

                      return nativeGenerateRequest(
                        initDataType,
                        initData
                      );
                    };
                  session.__telemetry_genRequest_hooked = true;
                }

                if (
                  session.update &&
                  !session.__telemetry_update_hooked
                ) {
                  const nativeUpdate = session.update.bind(session);
                  session.update = function hookedUpdate(response) {
                    let responseHex = "";
                    let responseB64 = "";
                    let clearkeyMatrix = [];

                    try {
                      if (response instanceof ArrayBuffer) {
                        responseB64 = arrayBufferToBase64(response);
                        responseHex = arrayBufferToHex(response);
                        clearkeyMatrix =
                          extractClearKeyMatrixFromLicense(
                            new Uint8Array(response)
                          );
                      } else if (ArrayBuffer.isView(response)) {
                        const sliced = new Uint8Array(
                          response.buffer,
                          response.byteOffset,
                          response.byteLength
                        );
                        responseB64 = uint8ArrayToBase64(sliced);
                        responseHex = uint8ArrayToHex(sliced);
                        clearkeyMatrix =
                          extractClearKeyMatrixFromLicense(sliced);
                      }
                    } catch (parseErr) {
                      console.warn(
                        "[Telemetry:E] License response parsing error:",
                        parseErr
                      );
                    }

                    bus.publish("eme:license_response", {
                      keySystem,
                      responseLength:
                        response instanceof ArrayBuffer
                          ? response.byteLength
                          : ArrayBuffer.isView(response)
                          ? response.byteLength
                          : 0,
                      responseHex,
                      responseB64,
                      clearkeyMatrix,
                    });

                    return nativeUpdate(response);
                  };
                  session.__telemetry_update_hooked = true;
                }

                return session;
              };
              mediaKeys.__telemetry_createSession_hooked = true;
            }
            return mediaKeys;
          });
        };
        return mediaKeySystemAccess;
      }
    );
  };
  console.info("[Telemetry:E] EME pipeline hooked");

  // --- Binary utility functions ---
  function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (let i = 0; i < bytes.byteLength; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
  }

  function uint8ArrayToBase64(arr) {
    let binary = "";
    for (let i = 0; i < arr.byteLength; i++) {
      binary += String.fromCharCode(arr[i]);
    }
    return btoa(binary);
  }

  function arrayBufferToHex(buffer) {
    const arr = new Uint8Array(buffer);
    return uint8ArrayToHex(arr);
  }

  function uint8ArrayToHex(arr) {
    const hexParts = new Array(arr.byteLength);
    for (let i = 0; i < arr.byteLength; i++) {
      hexParts[i] = arr[i].toString(16).padStart(2, "0");
    }
    return hexParts.join("");
  }

  function parsePSSHBoxes(data) {
    const boxes = [];
    let offset = 0;
    const len = data.byteLength;
    while (offset + 8 <= len) {
      const view = new DataView(
        data.buffer,
        data.byteOffset + offset,
        Math.min(len - offset, len)
      );
      let size = view.getUint32(0);
      const boxType = view.getUint32(4);
      if (size === 0) {
        break;
      }
      if (size === 1 && offset + 16 <= len) {
        const high = view.getUint32(8);
        const low = view.getUint32(12);
        size = Number((BigInt(high) << 32n) | BigInt(low));
      }
      if (size < 8 || offset + size > len) {
        break;
      }
      const typeStr = String.fromCharCode(
        (boxType >> 24) & 0xff,
        (boxType >> 16) & 0xff,
        (boxType >> 8) & 0xff,
        boxType & 0xff
      );
      if (typeStr === "pssh") {
        const systemIdBytes = data.slice(
          offset + 12,
          offset + 28
        );
        const systemIdHex = uint8ArrayToHex(systemIdBytes);
        const systemIdUUID = `${systemIdHex.slice(0, 8)}-${systemIdHex.slice(
          8,
          12
        )}-${systemIdHex.slice(12, 16)}-${systemIdHex.slice(
          16,
          20
        )}-${systemIdHex.slice(20)}`;
        const dataSize = view.getUint32(28);
        const psshData = data.slice(
          offset + 32,
          offset + 32 + Math.min(dataSize, size - 32)
        );
        boxes.push({
          systemId: systemIdUUID,
          systemIdHex: "0x" + systemIdHex,
          dataSize,
          dataHex: uint8ArrayToHex(psshData),
          dataB64: uint8ArrayToBase64(psshData),
        });
      }
      offset += size;
    }
    return boxes;
  }

  function extractClearKeyMatrixFromLicense(data) {
    const matrix = [];
    try {
      const jsonStr = new TextDecoder("utf-8", { fatal: false }).decode(
        data
      );
      let parsed;
      try {
        parsed = JSON.parse(jsonStr);
      } catch (_) {
        return matrix;
      }
      if (!parsed || !Array.isArray(parsed.keys)) {
        return matrix;
      }
      for (const entry of parsed.keys) {
        if (entry && entry.kid && entry.k) {
          let kidB64 = "";
          let keyB64 = "";
          try {
            kidB64 =
              typeof entry.kid === "string"
                ? entry.kid
                : uint8ArrayToBase64(new Uint8Array(entry.kid));
          } catch (_) {
            kidB64 = String(entry.kid);
          }
          try {
            keyB64 =
              typeof entry.k === "string"
                ? entry.k
                : uint8ArrayToBase64(new Uint8Array(entry.k));
          } catch (_) {
            keyB64 = String(entry.k);
          }
          matrix.push({
            kid: kidB64,
            key: keyB64,
            type: entry.type || "temporary",
          });
        }
      }
    } catch (err) {
      console.warn(
        "[Telemetry:E] ClearKey matrix extraction error:",
        err
      );
    }
    return matrix;
  }
})();

// ===========================================================================
// MODULE F: UNIFIED DATA LOGGER & REPORT MODULE
// ===========================================================================

(function installTelemetryLogger() {
  const bus = window.__telemetryBus;
  if (!bus) {
    console.error(
      "[Telemetry:F] __telemetryBus not found — cannot install logger"
    );
    return;
  }

  // The bus object is frozen (`Object.freeze(bus)` in Module A), so
  // installation state is tracked in a WeakSet instead of a property on it.
  const installedLoggers = new WeakSet();

  if (installedLoggers.has(bus)) {
    console.info("[Telemetry:F] Logger already installed — skipping");
    return;
  }

  const collectedEvents = [];
  const MAX_COLLECTED = 2000;

  function pushCollected(envelope) {
    collectedEvents.push(envelope);
    if (collectedEvents.length > MAX_COLLECTED) {
      collectedEvents.shift();
    }
  }

  // --- Fetch manifest log ---
  bus.subscribe("fetch:manifest", (env) => {
    pushCollected(env);
    console.groupCollapsed(
      `%c[FETCH:MANIFEST] %c${env.payload.type.toUpperCase()} %c${env.payload.status} %c${truncate(env.payload.url, 80)}`,
      "color: #4fc3f7; font-weight: bold",
      "color: #ffb74d",
      env.payload.status < 400 ? "color: #66bb6a" : "color: #ef5350",
      "color: #90a4ae"
    );
    console.log("URL:", env.payload.url);
    console.log("Method:", env.payload.method);
    console.log(
      "Content-Type:",
      env.payload.contentType || "(none)"
    );
    console.log("Content-Length:", env.payload.contentLength);
    console.log("Body Length:", env.payload.bodyLength);
    console.log(
      "Body Preview:",
      env.payload.bodyPreview
    );
    console.log("Duration:", env.payload.durationMs + "ms");
    console.log(
      "Request Headers:",
      env.payload.requestHeaders
    );
    console.log("Response Headers:", env.payload.headers);
    console.groupEnd();
  });

  // --- XHR manifest log ---
  bus.subscribe("xhr:manifest", (env) => {
    pushCollected(env);
    console.groupCollapsed(
      `%c[XHR:MANIFEST] %c${env.payload.type.toUpperCase()} %c${env.payload.status} %c${truncate(env.payload.url, 80)}`,
      "color: #4fc3f7; font-weight: bold",
      "color: #ffb74d",
      env.payload.status < 400 ? "color: #66bb6a" : "color: #ef5350",
      "color: #90a4ae"
    );
    console.log("URL:", env.payload.url);
    console.log("Method:", env.payload.method);
    console.log("Content-Type:", env.payload.contentType);
    console.log("Content-Length:", env.payload.contentLength);
    console.log("Body Length:", env.payload.bodyLength);
    console.log("Body Preview:", env.payload.bodyPreview);
    console.log("Duration:", env.payload.durationMs + "ms");
    console.log("Request Headers:", env.payload.requestHeaders);
    console.log("Response Headers:", env.payload.responseHeaders);
    console.groupEnd();
  });

  // --- Direct media stream log ---
  bus.subscribe("fetch:media_stream", (env) => {
    pushCollected(env);
    console.groupCollapsed(
      `%c[FETCH:STREAM] %c${env.payload.type.toUpperCase()} %c${env.payload.contentType} %c${truncate(env.payload.url, 80)}`,
      "color: #4fc3f7; font-weight: bold",
      "color: #ffb74d",
      "color: #66bb6a",
      "color: #90a4ae"
    );
    console.log("URL:", env.payload.url);
    console.log("Type:", env.payload.type);
    console.log("Content-Type:", env.payload.contentType);
    console.log("Content-Length:", env.payload.contentLength);
    console.log("Status:", env.payload.status);
    console.log("Duration:", env.payload.durationMs + "ms");
    console.log("Request Headers:", env.payload.requestHeaders);
    console.groupEnd();
  });

  // --- MSE init segment log ---
  bus.subscribe("ms:init_segment", (env) => {
    pushCollected(env);
    console.groupCollapsed(
      `%c[MSE:INIT] %c${formatBytes(env.payload.byteLength)} %c${env.payload.isoBoxes.map((b) => b.type).join(" → ") || "unknown"}`,
      "color: #ce93d8; font-weight: bold",
      "color: #ffb74d",
      "color: #90a4ae"
    );
    console.log("Byte Length:", env.payload.byteLength);
    console.log(
      "Hex Preview:",
      env.payload.hexPreview
    );
    console.log("Truncated:", env.payload.hexPreviewTruncated);
    console.log("ISO Boxes:", env.payload.isoBoxes);
    console.groupEnd();
  });

  // --- WebRTC SDP log ---
  bus.subscribe("webrtc:local_description", (env) => {
    pushCollected(env);
    console.groupCollapsed(
      `%c[WebRTC:LOCAL] %c${env.payload.type}`,
      "color: #ef5350; font-weight: bold",
      "color: #ffb74d"
    );
    console.log("SDP:\n", env.payload.sdp);
    console.groupEnd();
  });

  bus.subscribe("webrtc:remote_description", (env) => {
    pushCollected(env);
    console.groupCollapsed(
      `%c[WebRTC:REMOTE] %c${env.payload.type}`,
      "color: #ef5350; font-weight: bold",
      "color: #ffb74d"
    );
    console.log("SDP:\n", env.payload.sdp);
    console.groupEnd();
  });

  // --- WebSocket log ---
  bus.subscribe("ws:connect", (env) => {
    pushCollected(env);
    console.groupCollapsed(
      `%c[WS:CONNECT] %c${truncate(env.payload.url, 80)}`,
      "color: #26c6da; font-weight: bold",
      "color: #90a4ae"
    );
    console.log("URL:", env.payload.url);
    console.log("Protocols:", env.payload.protocols);
    console.groupEnd();
  });

  bus.subscribe("ws:message", (env) => {
    pushCollected(env);
    console.groupCollapsed(
      `%c[WS:MSG] %c${env.payload.dataType} %c${formatBytes(env.payload.byteLength)}`,
      "color: #26c6da; font-weight: bold",
      "color: #ffb74d",
      "color: #66bb6a"
    );
    console.log("URL:", env.payload.url);
    console.log("Data Type:", env.payload.dataType);
    console.log("Byte Length:", env.payload.byteLength);
    console.log("Elapsed:", env.payload.elapsedMs + "ms");
    if (env.payload.hexPreview) {
      console.log("Hex Preview:", env.payload.hexPreview);
    }
    console.groupEnd();
  });

  // --- EME log ---
  bus.subscribe("eme:request_key_system", (env) => {
    pushCollected(env);
    console.groupCollapsed(
      `%c[EME:INIT] %c${env.payload.keySystem}`,
      "color: #ff7043; font-weight: bold",
      "color: #ffb74d"
    );
    console.log("Key System:", env.payload.keySystem);
    console.log("Config Count:", env.payload.configCount);
    console.log("Configurations:", env.payload.configs);
    console.groupEnd();
  });

  bus.subscribe("eme:license_request", (env) => {
    pushCollected(env);
    console.groupCollapsed(
      `%c[EME:LICENSE_REQ] %c${env.payload.keySystem} %c${env.payload.initDataType}`,
      "color: #ff7043; font-weight: bold",
      "color: #ffb74d",
      "color: #90a4ae"
    );
    console.log("Key System:", env.payload.keySystem);
    console.log("Init Data Type:", env.payload.initDataType);
    console.log("Init Data Length:", env.payload.initDataLength);
    console.log(
      "Init Data (Base64):",
      env.payload.initDataB64
    );
    console.log(
      "Init Data (Hex):",
      env.payload.initDataHex
    );
    if (env.payload.psshBoxes.length > 0) {
      console.table(
        env.payload.psshBoxes.map((b) => ({
          SystemID: b.systemId,
          DataSize: b.dataSize,
        }))
      );
      console.log(
        "PSSH Data (B64):",
        env.payload.psshBoxes.map((b) => b.dataB64)
      );
    }
    console.groupEnd();
  });

  bus.subscribe("eme:license_response", (env) => {
    pushCollected(env);
    const hasClearKey =
      env.payload.clearkeyMatrix &&
      env.payload.clearkeyMatrix.length > 0;
    console.groupCollapsed(
      `%c[EME:LICENSE_RES] %c${env.payload.keySystem} %c${hasClearKey ? "ClearKey:" + env.payload.clearkeyMatrix.length + " keys" : "No ClearKey"} %c${formatBytes(env.payload.responseLength)}`,
      "color: #ff7043; font-weight: bold",
      "color: #ffb74d",
      hasClearKey ? "color: #66bb6a" : "color: #90a4ae",
      "color: #90a4ae"
    );
    console.log("Response Length:", env.payload.responseLength);
    console.log(
      "Response (Base64):",
      env.payload.responseB64
    );
    console.log(
      "Response (Hex):",
      env.payload.responseHex
    );
    if (hasClearKey) {
      console.group("ClearKey Matrix (kid:key pairs)");
      for (let i = 0; i < env.payload.clearkeyMatrix.length; i++) {
        const entry = env.payload.clearkeyMatrix[i];
        console.log(
          `  [${i}] kid: ${entry.kid}`,
          `\n      key: ${entry.key}`,
          `\n      type: ${entry.type}`
        );
      }
      console.groupEnd();
    }
    console.groupEnd();
  });

  // --- Utility functions ---
  function truncate(str, maxLen) {
    if (!str) {
      return "(empty)";
    }
    return str.length > maxLen
      ? str.slice(0, maxLen) + "..."
      : str;
  }

  function formatBytes(bytes) {
    if (bytes == null || isNaN(bytes)) {
      return "? B";
    }
    if (bytes === 0) {
      return "0 B";
    }
    const units = ["B", "KB", "MB", "GB"];
    const idx = Math.min(
      Math.floor(Math.log(bytes) / Math.log(1024)),
      units.length - 1
    );
    return (
      (bytes / Math.pow(1024, idx)).toFixed(idx > 0 ? 2 : 0) +
      " " +
      units[idx]
    );
  }

  // --- Global report accessor ---
  Object.defineProperty(window, "__telemetryReport", {
    get() {
      const categories = {
        manifests: [],
        mediaStreams: [],
        mseInitSegments: [],
        webrtcSDP: [],
        websocketMessages: [],
        emeEvents: [],
      };
      for (const env of collectedEvents) {
        switch (env.eventType) {
          case "fetch:manifest":
          case "xhr:manifest":
            categories.manifests.push({
              url: env.payload.url,
              type: env.payload.type,
              status: env.payload.status,
              contentType: env.payload.contentType,
              timestamp: env.timestamp,
            });
            break;
          case "fetch:media_stream":
          case "xhr:media_stream":
            categories.mediaStreams.push({
              url: env.payload.url,
              mimeType: env.payload.contentType,
              timestamp: env.timestamp,
            });
            break;
          case "ms:init_segment":
            categories.mseInitSegments.push({
              byteLength: env.payload.byteLength,
              boxes: env.payload.isoBoxes,
              timestamp: env.timestamp,
            });
            break;
          case "webrtc:local_description":
          case "webrtc:remote_description":
            categories.webrtcSDP.push({
              type: env.payload.type,
              timestamp: env.timestamp,
            });
            break;
          case "ws:message":
            categories.websocketMessages.push({
              url: env.payload.url,
              dataType: env.payload.dataType,
              byteLength: env.payload.byteLength,
              timestamp: env.timestamp,
            });
            break;
          case "eme:request_key_system":
          case "eme:license_request":
          case "eme:license_response":
            categories.emeEvents.push({
              eventType: env.eventType,
              keySystem: env.payload.keySystem,
              timestamp: env.timestamp,
            });
            break;
        }
      }
      return {
        sessionId: bus.sessionId,
        totalEvents: collectedEvents.length,
        byCategory: categories,
        rawEvents: collectedEvents,
      };
    },
    configurable: false,
    enumerable: true,
  });

  // --- Console command ---
  console.info(
    "%c[Telemetry] Diagnostic suite active. Commands:\n" +
      "  %c__telemetryReport %c— print aggregated diagnostic summary\n" +
      "  %c__telemetryBus.subscriberCount(eventType) %c— query listener count",
    "font-weight: bold",
    "color: #4fc3f7; font-weight: bold",
    "",
    "color: #4fc3f7; font-weight: bold",
    ""
  );

  // Mark the logger as installed — tracked in a WeakSet because the bus is
  // frozen and cannot accept new properties.
  installedLoggers.add(bus);
})();
```
