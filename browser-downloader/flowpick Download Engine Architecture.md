---
title: "Download Engine Architecture"
source: "https://flowpick.net/docs/advanced/download-engine#strategy-comparison-summary"
author:
published:
created: 2026-07-13
description: "Technical architecture details of FlowPick's download engine — segment concurrent download, AES-128 decryption, three-tier write strategy, memory safety management, and progress tracking."
tags:
  - "clippings"
---
Technical architecture details of FlowPick's download engine — segment concurrent download, AES-128 decryption, three-tier write strategy, memory safety management, and progress tracking.

FlowPick's download engine is the core of the entire system, responsible for downloading streaming segments, decrypting, merging them, and writing to disk. This document is for developers and advanced users who want to deeply understand the internal mechanisms.

![Download Engine Architecture Overview](https://flowpick.net/_ipx/_/screenshots/download-engine-architecture.png)

---

## Architecture Overview

Download engine consists of three core modules, with data flowing through each module in pipeline fashion:

```
┌─────────────────────────────────────────────────┐
│                   Download Engine                 │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ Segment   │→│ Segment   │→│ File Writing    │  │
│  │ Download  │  │ Process  │  │ Module          │  │
│  │ Module    │  │ Module   │  │                 │  │
│  │          │  │          │  │                 │  │
│  │ · Concur- │  │ · Decrypt│  │ · FSA Streaming│  │
│  │   rency  │  │ · Concat │  │ · StreamSaver   │  │
│  │ · Retry  │  │ · Remux  │  │ · Blob Fallback │  │
│  └──────────┘  └──────────┘  └───────────────┘  │
│                                                  │
│  ┌─────────────────────────────────────────────┐ │
│  │           Memory Safety Manager              │ │
│  │  · Size Estimation · Threshold Check        │ │
│  │  · Strategy Selection                       │ │
│  └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

### Full Data Flow

Complete chain from user clicking download to file written to disk:

```
User clicks download
    │
    ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ 1. Manifest   │ ──→ │ 2. Segment    │ ──→ │ 3. Segment    │
│    Parsing    │     │    Download   │     │    Processing │
│              │     │               │     │               │
│ · Download   │     │ · Worker Pool │     │ · AES Decrypt │
│   M3U8       │     │ · Concurrency │     │ · TS Concat   │
│ · Parse seg- │     │ · Exponential │     │ · FFmpeg Remux│
│   ment list  │     │   backoff     │     │ · A/V Merge   │
│ · Extract    │     │ · Progress    │     │               │
│   key        │     │   reporting   │     │               │
│ · Estimate   │     │               │     │               │
│   size       │     │               │     │               │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                  │
                                                  ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ 6. Completion │ ←── │ 5. Cleanup   │ ←── │ 4. File Write │
│ Notification │     │ & Recycling   │     │               │
│              │     │               │     │               │
│ · Desktop    │     │ · Release     │     │ · FSA Stream  │
│   notifi-    │     │   memory      │     │ · StreamSaver │
│   cation    │     │ · Clean temp  │     │ · Blob Fall-  │
│ · Copy path  │     │   files       │     │   back        │
│ · Queue adv- │     │ · Reset state │     │               │
│   ance       │     │               │     │               │
└──────────────┘     └──────────────┘     └──────────────┘
```

For specific implementation of manifest parsing phase (Master Playlist vs Media Playlist, encryption detection), see [Video Sniffing — HLS Streams](https://flowpick.net/docs/features/video-sniffing#hlsm3u8-streams). For FFmpeg remuxing details in segment processing, see [Format Conversion](https://flowpick.net/docs/features/format-conversion). For engine's position in overall system, see [Project Architecture](https://flowpick.net/docs/developer/architecture).

### Engine Lifecycle

A complete run of download engine goes through following state transitions:

```
[Idle]
  │ User triggers download
  ▼
[Initializing] ─── Load WASM, check API availability, select write strategy
  │
  ▼
[Manifest Parsing] ── Download and parse M3U8/MPD, extract segment list and keys
  │
  ▼
[Segment Download] ── Worker Pool concurrent download, real-time progress reporting
  │
  ▼
[Segment Processing] ── Decrypt → Concat/Remux → Write to disk
  │
  ├── Success → [Cleanup] → [Complete] → [Idle]
  │
  ├── Cancel → [Cleanup] → [Idle]
  │
  └── Failure → [Cleanup] → [Error] → [Idle]
```

Each state transition triggers corresponding lifecycle hooks; UI layer updates interface state by listening to these hooks. For UI-engine interaction details, see [Project Architecture — Data Flow](https://flowpick.net/docs/developer/architecture#data-flow).

---

## Segment Download Module

### Concurrent Downloader

Segment download uses Worker Pool pattern to implement concurrency control:

```typescript
const downloadSegmentsConcurrently = async (
  segments: SegmentInfo[],
  onSegmentDownloaded: (buffer: ArrayBuffer, index: number) => void
) => {
  const concurrency = Math.min(config.concurrency, 8)
  let nextIndex = 0

  const worker = async () => {
    while (nextIndex < segments.length) {
      const index = nextIndex++
      const buffer = await downloadWithRetry(segments[index]!)
      onSegmentDownloaded(buffer, index)
    }
  }

  const workers = Array.from(
    { length: Math.min(concurrency, segments.length) },
    () => worker()
  )
  await Promise.all(workers)
}
```

**Design Points**:

- Use shared `nextIndex` counter for task assignment, avoiding load imbalance from pre-splitting segments
- Worker count doesn't exceed total segment count, avoiding idle workers
- Each worker runs independently; single segment failure doesn't affect other workers

#### Worker Pool Deep Dive

Core advantage of Worker Pool pattern is **dynamic load balancing**. Unlike pre-splitting segment array into N equal parts, shared counter ensures:

```
Pre-splitting (Not recommended):
Worker 1: [Segment 0-49]   ← If these segments are larger, Worker 1 becomes bottleneck
Worker 2: [Segment 50-99]  ← May finish early, then wait idle

Shared Counter (FlowPick's approach):
Worker 1: Segment 0 → Segment 3 → Segment 5 → ...
Worker 2: Segment 1 → Segment 4 → Segment 7 → ...
Worker 3: Segment 2 → Segment 6 → Segment 8 → ...
```

Each worker immediately grabs next unassigned segment after completing current one, until all segments processed. This pattern naturally adapts to scenarios with uneven segment sizes (e.g., HLS streams' first/last segments usually smaller, middle segments larger).

Concurrency controlled by `config.concurrency`, capped at 8. Users can adjust default value in [Configuration Reference](https://flowpick.net/docs/getting-started/configuration) or modify in real-time on download interface. For queue scheduling in batch download scenarios, see [Batch Download — Concurrency Selection Guide](https://flowpick.net/docs/features/batch-download#concurrency-selection-guide).

### Async Generator Pattern

For very large files (segment count > 500), engine uses AsyncGenerator pattern to avoid loading all segment data into memory at once:

```typescript
async function* segmentGenerator(
  segments: SegmentInfo[],
  signal?: AbortSignal
): AsyncGenerator<ArrayBuffer> {
  for (const segment of segments) {
    if (signal?.aborted) break
    const buffer = await downloadWithRetry(segment)
    yield buffer
  }
}
```

**Difference from Array Pattern**:

| Dimension | Array Pattern | Generator Pattern |
| --- | --- | --- |
| Memory peak | All segments in memory simultaneously | Only currently processing segment in memory |
| Applicable scenario | Segment count < 500 | Segment count > 500 |
| Progress tracking | Known total, precise percentage | Known total, precise percentage |
| Cancel response | Must wait for current batch to complete | Immediate response |

Engine automatically selects pattern based on segment count without user intervention. For actual scenarios of very large file downloads, see [Live Replay Saving](https://flowpick.net/docs/usecases/live-replays).

### Retry Mechanism

When segment download fails, uses exponential backoff retry:

```typescript
const downloadWithRetry = async (
  segment: SegmentInfo,
  maxRetries = 3
): Promise<ArrayBuffer> => {
  let lastError: unknown
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      return await downloadAndDecryptSegment(segment)
    } catch (e) {
      lastError = e
      if (attempt < maxRetries - 1) {
        // Exponential backoff: 400ms, 800ms, 1600ms
        await new Promise(r => setTimeout(r, Math.pow(2, attempt) * 400))
      }
    }
  }
  throw lastError
}
```

**Retry Strategy**:

| Attempt # | Delay | Cumulative Wait |
| --- | --- | --- |
| 1st failure | 400ms | 400ms |
| 2nd failure | 800ms | 1200ms |
| 3rd failure | 1600ms | 2800ms |

**Cases that don't trigger retry**:

- HTTP 4xx client errors (403, 404, etc.) — retry meaningless
- CORS errors — policy issue, retry won't change result
- Unsupported encryption error — cannot be resolved via retry

Retry mechanism works together with error classification table in [Online Tools — Error Handling](https://flowpick.net/docs/advanced/online-tools#error-handling). HTTP 4xx and CORS errors thrown directly, displayed as corresponding user prompts by upper UI layer. For troubleshooting mid-download failures, see [Common Issues Troubleshooting — Mid-Download Failure](https://flowpick.net/docs/troubleshooting/common-issues#mid-download-failure).

### Error Classification

```typescript
class FetchError extends Error {
  constructor(
    message: string,
    public status: number,
    public url: string
  ) {
    super(message)
    this.name = 'FetchError'
  }
}
```

Errors classified and handled by HTTP status code:

| Status Code | Error Type | User Prompt |
| --- | --- | --- |
| 403 | Auth/authorization failure | "Access denied, please check if URL is valid" |
| 404 | Resource not found | "Segment not found, stream may have expired" |
| 502/503/504 | Server error | "Server temporarily unavailable, please try again later" |
| CORS | Cross-origin restriction | "Cross-origin request blocked, recommend using extension version" |
| Network | Network interruption | "Network connection failed, please check network" |

When encountering CORS errors, recommend installing [browser extension](https://flowpick.net/docs/getting-started/installation), which can bypass same-origin policy via `webRequest` permissions. See [Common Issues Troubleshooting](https://flowpick.net/docs/troubleshooting/common-issues) for more troubleshooting methods. For CORS technical principles and limitations, see [Known Limitations — Browser Limitations](https://flowpick.net/docs/troubleshooting/known-issues#browser-limitations).

## Segment Processing Module

### AES-128 Decryption

For encrypted HLS streams, FlowPick uses Web Crypto API to decrypt in browser:

```typescript
const decryptAES128 = async (
  encryptedData: ArrayBuffer,
  key: ArrayBuffer,
  iv?: Uint8Array
): Promise<ArrayBuffer> => {
  const keyBytes = await crypto.subtle.importKey(
    'raw', key,
    { name: 'AES-CBC' },
    false,
    ['decrypt']
  )

  const ivBuffer = iv ? new Uint8Array(iv) : new Uint8Array(16)

  const decrypted = await crypto.subtle.decrypt(
    { name: 'AES-CBC', iv: ivBuffer },
    keyBytes,
    encryptedData
  )

  return decrypted
}
```

**Decryption Flow**:

1. Extract key URI and IV from M3U8's `#EXT-X-KEY` tag
2. Download key file (usually 16-byte binary file)
3. Decrypt each segment using AES-128-CBC mode
4. If IV not specified, use segment sequence number as IV (HLS spec default behavior)

**Performance Considerations**:

- Web Crypto API uses hardware acceleration, decryption speed usually not bottleneck
- Each segment decrypted independently, can parallelize with download
- Key only needs to be downloaded once, cached in memory

All decryption operations completed locally in browser; keys and segment data not uploaded to any server. For more details on encryption detection, see [Video Sniffing — Encrypted Streams](https://flowpick.net/docs/features/video-sniffing#encrypted-streams-aes-128). For privacy protection strategy, see [Privacy & Security](https://flowpick.net/docs/features/privacy-security). For unsupported encryption types (Widevine, PlayReady etc.), see [Known Limitations — DRM Protected Content](https://flowpick.net/docs/troubleshooting/known-issues#drm-protected-content).

### TS Segment Concatenation

For TS output format, segments directly concatenated in binary:

```typescript
// Pseudocode
const merged = new Uint8Array(totalSize)
let offset = 0
for (const segment of segments) {
  merged.set(new Uint8Array(segment), offset)
  offset += segment.byteLength
}
```

This approach has zero CPU overhead, speed limited only by memory copy speed.

### FFmpeg WASM Remuxing

For MP4 output format, use FFmpeg WASM for container conversion. See [Format Conversion](https://flowpick.net/docs/features/format-conversion) document for details.

If you only need TS format, selecting TS output can completely bypass FFmpeg, significantly improving merge speed. TS files can be played directly with VLC, PotPlayer etc. For output format selection recommendations, see [Format Conversion — Output Format Selection](https://flowpick.net/docs/features/format-conversion#output-format-selection-guide).

### DASH Stream Special Processing

DASH streams differ significantly from HLS streams in segment processing:

```
HLS stream processing:
  Segment 0 → Segment 1 → Segment 2 → ... → Concat → Output

DASH stream processing (audio-video separated):
  Init segment → Video Seg 0 → Video Seg 1 → ... ─┐
                                              ├→ FFmpeg Merge → Output
  Init segment → Audio Seg 0 → Audio Seg 1 → ... ─┘
```

DASH's FMP4 (Fragmented MP4) format requires special handling:

1. **Init segment** (`ftyp` + `moov` box) must be placed at beginning of file
2. **Media segments** (`moof` + `mdat` box) appended sequentially
3. When audio-video separated, need to download video and audio tracks separately, finally merge via FFmpeg

For DASH stream manifest parsing details, see [Video Sniffing — DASH Streams](https://flowpick.net/docs/features/video-sniffing#dashmpd-streams). For specific FMP4 reassembly implementation, see [Format Conversion — FMP4 Reassembly](https://flowpick.net/docs/features/format-conversion). For known issues with DASH audio-video separation, see [Known Limitations — Separated Audio-Video Streams](https://flowpick.net/docs/troubleshooting/known-issues#separated-audio-video-streams).

## File Writing Module

Writing module selects strategy by priority to ensure working across as many browsers as possible.

![Three-Tier Write Strategy Comparison](https://flowpick.net/_ipx/_/screenshots/write-strategies.png)

### Strategy 1: File System Access API (Optimal)

```typescript
async function createFSAStream(
  filename: string,
  dirHandle?: FileSystemDirectoryHandle | null
): Promise<WritableStream<Uint8Array>> {
  let handle: FileSystemFileHandle

  if (dirHandle) {
    // Already have directory permission, directly create file
    handle = await dirHandle.getFileHandle(filename, { create: true })
  } else {
    // Popup for user to choose save location
    handle = await window.showSaveFilePicker!({
      suggestedName: filename,
      types: [{
        description: 'Video',
        accept: { 'video/mp4': ['.mp4'] }
      }]
    })
  }

  const writable = await handle.createWritable()

  return new WritableStream<Uint8Array>({
    async write(chunk) { await writable.write(chunk) },
    async close() { await writable.close() },
    async abort(reason) { await writable.abort(reason) }
  }, {
    highWaterMark: 16 * 1024 * 1024 // 16MB buffer
  })
}
```

**Advantages**:

- Streaming write, constant memory usage (only 16MB buffer)
- Supports arbitrary file sizes
- After directory persistence, no repeated popups needed

**Limitations**:

- Only supported on Chrome 86+ and Edge 86+
- Requires user gesture trigger (`showDirectoryPicker` must be called within user click event)

### Strategy 2: StreamSaver.js (Alternative)

```typescript
async function createStreamSaverStream(
  filename: string,
  fileSize?: number
): Promise<WritableStream<Uint8Array>> {
  const ss = await getStreamSaver()
  const fileStream = ss.createWriteStream(filename, { size: fileSize })
  return fileStream
}
```

**Advantages**:

- Streaming write, low memory usage
- Better compatibility than FSA API

**Limitations**:

- Requires Service Worker support
- Requires `mitm.html` and `streamsaver-sw.js` properly deployed
- Some enterprise network environments may block Service Worker

### Strategy 3: Blob Fallback

```typescript
// Pseudocode
const blob = new Blob([mergedData], { type: 'video/mp4' })
const url = URL.createObjectURL(blob)
const a = document.createElement('a')
a.href = url
a.download = filename
a.click()
URL.revokeObjectURL(url)
```

**Limitations**:

- Entire file loaded into memory
- Hard limit 1.5GB (`MEMORY_CONFIG.maxBlobSize`)
- Console warning when exceeding 800MB (`MEMORY_CONFIG.warnBlobSize`)

### Strategy Comparison Summary

| Dimension | FSA API | StreamSaver.js | Blob |
| --- | --- | --- | --- |
| Memory usage | 16MB (constant) | Low (streaming) | Equal to file size |
| File size limit | Unlimited | Unlimited | 1.5GB |
| Browser requirement | Chrome/Edge 86+ | Service Worker support | All browsers |
| Directory persistence | Supported | Not supported | Not supported |
| Download experience | Best | Good | Average |

For browser support status of these three strategies, see [Browser Compatibility — Feature Fallback Strategies](https://flowpick.net/docs/advanced/browser-compatibility#feature-fallback-strategies). For write strategy usage in online tools, see [Online Tools — Write Strategy Priority](https://flowpick.net/docs/advanced/online-tools#write-strategy-priority). For Blob mode size limits and solutions, see [Known Limitations — File Size Limits](https://flowpick.net/docs/troubleshooting/known-issues#file-size-limits).

## Memory Safety Management

![Memory Management Decision Flow](https://flowpick.net/_ipx/_/screenshots/memory-management.png)

### Size Estimation

Before starting download, engine samples to estimate total file size:

```typescript
async function estimateSegmentsSize(
  segments: ArrayBuffer[] | AsyncGenerator<ArrayBuffer>,
  totalCount: number
): Promise<{ estimatedSize: number; isEstimate: boolean }> {
  // Sample count: min(5, max(1, totalCount * 0.1))
  const sampleCount = Math.min(5, Math.max(1, Math.floor(totalCount * 0.1)))

  // Download first few segments and calculate average size
  let sampledTotal = 0
  for (let i = 0; i < sampleCount; i++) {
    sampledTotal += sampleSegment.byteLength
  }

  const avgSample = sampledTotal / sampleCount
  return {
    estimatedSize: Math.round(avgSample * totalCount),
    isEstimate: true
  }
}
```

**Sampling Strategy Explanation**:

- Sample count takes `min(5, max(1, total segments × 10%))`, ensuring small streams sample at least 1, large streams at most 5
- Sampling result marked `isEstimate: true`, UI layer displays "~XX MB" instead of exact value based on this
- For HLS streams, if Master Playlist declares `BANDWIDTH` attribute, engine prioritizes using that value as estimation reference

### Strategy Selection Logic

```
Estimated size < 800MB  → Any strategy acceptable
Estimated size 800MB-1.5GB → Prioritize streaming write, Blob mode shows warning
Estimated size > 1.5GB → Force streaming write, Blob mode refused
```

### Memory Configuration Constants

```typescript
const MEMORY_CONFIG = {
  maxBlobSize: 1500 * 1024 * 1024,    // 1.5GB hard limit
  warnBlobSize: 800 * 1024 * 1024,    // 800MB warning threshold
}
```

### Runtime Memory Monitoring

In addition to pre-download estimation, engine continuously monitors memory pressure during runtime:

```typescript
const checkMemoryPressure = (): 'normal' | 'warning' | 'critical' => {
  // Check current total allocated segment buffer size
  const allocatedMB = allocatedBuffers.reduce((sum, buf) => sum + buf.byteLength, 0) / 1048576

  if (allocatedMB > 1200) return 'critical'
  if (allocatedMB > 600) return 'warning'
  return 'normal'
}
```

When memory pressure reaches `critical` level, engine will:

- Pause new segment download requests
- Prioritize writing already downloaded segments to disk
- Release buffers for segments already written

Memory safety is one of core design considerations for FlowPick's download engine. For very large files (e.g., 20GB+ live replays), engine forces streaming write strategy to ensure browser doesn't crash due to insufficient memory. For actual scenarios of large file downloads, see [Live Replay Saving](https://flowpick.net/docs/usecases/live-replays). For troubleshooting large file download failures, see [Common Issues Troubleshooting — Large File Download Failure](https://flowpick.net/docs/troubleshooting/common-issues#large-file-download-failure).

## Progress Tracking

### Speed Calculation

Engine uses sliding window algorithm to calculate real-time download speed:

```typescript
class SpeedCalculator {
  private samples: Array<{ bytes: number; timestamp: number }> = []
  private readonly WINDOW_SIZE = 5000 // 5 second window

  record(bytesDownloaded: number): void {
    this.samples.push({ bytes: bytesDownloaded, timestamp: Date.now() })
    // Remove samples older than window
    const cutoff = Date.now() - this.WINDOW_SIZE
    this.samples = this.samples.filter(s => s.timestamp >= cutoff)
  }

  getSpeed(): number {
    if (this.samples.length < 2) return 0
    
    const oldest = this.samples[0]!
    const newest = this.samples[this.samples.length - 1]!
    
    const timeDiff = (newest.timestamp - oldest.timestamp) / 1000 // seconds
    const byteDiff = newest.bytes - oldest.bytes
    
    return timeDiff > 0 ? byteDiff / timeDiff : 0
  }
}
```

**Algorithm Characteristics**:

- **Sliding window**: Only considers data from recent 5 seconds, avoids historical data interference
- **Responsive**: Speed update reflects current network conditions in near real-time
- **Smooth**: Averages out momentary fluctuations, providing stable display value

**Display Format**:

| Speed Range | Display Unit | Example |
| --- | --- | --- |
| < 1 MB/s | KB/s | 512 KB/s |
| 1-1024 MB/s | MB/s | 5.2 MB/s |
| \> 1024 MB/s | GB/s | 1.3 GB/s |

### ETA Calculation

Estimated Time of Arrival (ETA) calculated based on current speed and remaining work:

```typescript
function calculateETA(
  downloadedBytes: number,
  totalBytes: number,
  currentSpeed: number
): string {
  if (currentSpeed <= 0 || downloadedBytes >= totalBytes) return '--'
  
  const remainingBytes = totalBytes - downloadedBytes
  const remainingSeconds = remainingBytes / currentSpeed
  
  return formatDuration(remainingSeconds)
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return \`${Math.round(seconds)}s\`
  if (seconds < 3600) return \`${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s\`
  return \`${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m\`
}
```

**Calculation Logic**:

- Based on **sliding window average speed**, not instantaneous speed
- Dynamically adjusts to network condition changes
- Shows "--" when speed is zero or download complete

### Progress Event Structure

Engine emits structured progress events for UI layer consumption:

```typescript
interface DownloadProgress {
  // Basic info
  stage: 'parsing' | 'downloading' | 'processing' | 'writing' | 'completed'
  
  // Overall progress
  percent: number                    // 0-100
  downloadedBytes: number            // Total downloaded bytes
  totalBytes: number                // Estimated or exact total
  
  // Speed info
  speed: number                     // Bytes per second
  eta: number                       // Estimated remaining seconds
  
  // Segment-level detail
  completedSegments: number         // Completed segment count
  totalSegments: number             // Total segment count
  
  // Error info (if any)
  error?: {
    type: string
    message: string
    segmentIndex?: number
    recoverable: boolean
  }
}
```

**Event Throttling**:

To avoid excessive UI updates, progress events throttled to maximum 10 times per second:

```typescript
const THROTTLE_INTERVAL = 100 // 100ms minimum interval

let lastEmitTime = 0
function emitProgress(progress: DownloadProgress) {
  const now = Date.now()
  if (now - lastEmitTime < THROTTLE_INTERVAL) return
  
  lastEmitTime = now
  eventEmitter.emit('progress', progress)
}
```

---

## Cancellation Mechanism

### AbortController Integration

Download engine fully supports cancellation via standard `AbortController` API:

```typescript
async function startDownload(
  url: string,
  options: DownloadOptions,
  signal: AbortSignal
): Promise<void> {
  // Pass signal to each module
  await parseManifest(url, { signal })
  await downloadSegments(segments, { signal })
  await processSegments(segments, { signal })
  await writeFile(outputFile, { signal })
}
```

**Cancellation Propagation Chain**:

```
User clicks cancel button
    │
    ▼
UI layer calls controller.abort()
    │
    ▼
1. Set signal.aborted = true
    │
    ├─→ 2. Stop accepting new download tasks
    │      └── Worker Pool stops grabbing new segments from queue
    │
    ├─→ 3. Interrupt ongoing fetch requests
    │      └── fetch() throws AbortError, caught by retry mechanism
    │
    ├─→ 4. Stop Worker Pool
    │      └── All Workers detect signal.aborted, exit loop
    │
    ├─→ 5. Clean up FFmpeg virtual filesystem
    │      └── Delete temp files: filelist.txt, segment_*.ts, output.*
    │
    ├─→ 6. Release allocated memory buffers
    │      └── Set all ArrayBuffer references to null, wait for GC
    │
    ├─→ 7. Discard incomplete file
    │      └── FSA mode: call writable.abort()
    │      └── StreamSaver mode: call writable.abort()
    │      └── Blob mode: Don't trigger download, release Blob directly
    │
    └─→ 8. Reset engine state
           └── Clear progress data, speed stats, error messages
```

### Cancellation Propagation Mechanism

`AbortSignal` propagates layer-by-layer through entire download chain:

```
UI Layer AbortController
    │
    ├──→ Segment Download Worker Pool (interrupt fetch)
    ├──→ Segment Processing Pipeline (skip remaining segments)
    ├──→ FFmpeg WASM (terminate remuxing)
    └──→ File Write Stream (abort write)
```

Each layer independently checks `signal.aborted`, ensuring cancel operation responds quickly. Even when cancelling during FFmpeg remuxing, engine waits for current FFmpeg operation to complete one atomic step before terminating, avoiding virtual filesystem corruption.

Cancel operation especially important in [Batch Download](https://flowpick.net/docs/features/batch-download) scenarios — users can cancel a specific download in queue without affecting other ongoing tasks. For queue behavior after cancellation, see [Batch Download — Queue Management](https://flowpick.net/docs/features/batch-download#queue-management).

## Performance Benchmarks

Following data tested under Chrome 126 + 100Mbps network environment, for reference only:

| Scenario | File Size | Concurrency | Download Time | Merge Time | Total Time |
| --- | --- | --- | --- | --- | --- |
| Short video (TS output) | ~50MB (30 segments) | 4 | ~8s | <1s | ~9s |
| Short video (MP4 output) | ~50MB (30 segments) | 4 | ~8s | ~3s | ~11s |
| Long video (TS output) | ~500MB (200 segments) | 6 | ~45s | ~2s | ~47s |
| Long video (MP4 output) | ~500MB (200 segments) | 6 | ~45s | ~15s | ~60s |
| Very large file (TS output) | ~2GB (800 segments) | 8 | ~3min | ~8s | ~3min |
| DASH audio-video separate | ~300MB video + ~30MB audio | 4 | ~30s | ~12s | ~42s |

**Influencing Factors**:

| Factor | Impact Level | Description |
| --- | --- | --- |
| CDN speed | High | Upper limit of segment download speed |
| Concurrent threads | Medium | 4-6 threads usually optimal range |
| Output format | Medium | TS output skips FFmerge, faster merging |
| SharedArrayBuffer | Medium | Multi-threaded FFmpeg 40-60% faster than single-threaded |
| Encrypted streams | Low | Web Crypto API hardware accelerated, minimal decryption overhead |

### Relationship Between Concurrency and Performance

```
Concurrency 1: ████████████████████████████ Slow, low bandwidth utilization
Concurrency 2: ████████████████ Faster, basically usable
Concurrency 4: ██████████ Fast, recommended default
Concurrency 6: ████████ Very fast, near optimal
Concurrency 8: ████████ About same as 6, diminishing returns
Concurrency 12+: ████████ May trigger CDN throttling,反而 slower
```

Selecting TS output format can skip FFmpeg merge phase, saving 10-20% total time for very large files. TS files can be played directly with VLC, PotPlayer, IINA etc. For more troubleshooting methods on slow download speeds, see [Common Issues Troubleshooting — Slow Download Speed](https://flowpick.net/docs/troubleshooting/common-issues#slow-download-speed).

## Error Boundaries & Exception Propagation

Download engine adopts layered error handling architecture, ensuring exceptions at each level are properly caught and converted to user-friendly prompts:

```
┌─────────────────────────────────────────────┐
│                   UI Layer                  │
│  · Display user prompts (Toast/popup)       │
│  · Update download status to "failed"       │
│  · Provide retry/feedback entry points      │
└──────────────────┬──────────────────────────┘
                   │ Catch all exceptions
┌──────────────────▼──────────────────────────┐
│              Engine Facade Layer             │
│  · Unified exception format (StreamMergeError) │
│  · Attach context info (URL, segment index, stage) │
│  · Decide whether retryable                 │
└──────────────────┬──────────────────────────┘
                   │
    ┌──────────────┼──────────────┐
    ▼              ▼              ▼
┌────────┐  ┌──────────┐  ┌──────────┐
│Download│  │Process   │  │Write     │
│ Module │  │Module    │  │Module    │
│        │  │          │  │          │
│FetchErr│  │CryptoErr │  │WriteErr  │
└────────┘  └──────────┘  └──────────┘
```

**Error Type Mapping**:

| Underlying Error | Engine Error Type | User Prompt | Retryable |
| --- | --- | --- | --- |
| `FetchError(403)` | `AuthError` | "Access denied" | No |
| `FetchError(404)` | `NotFoundError` | "Resource expired" | No |
| `FetchError(5xx)` | `ServerError` | "Server error" | Yes |
| `TypeError: Failed to fetch` | `NetworkError` | "Network connection failed" | Yes |
| `DOMException: AbortError` | `CancelledError` | No prompt (silent) | — |
| `CryptoError` | `DecryptError` | "Decryption failed" | No |
| `FFmpegError` | `MergeError` | "Merge failed" | No |
| `QuotaExceededError` | `StorageError` | "Insufficient storage space" | No |

For detailed troubleshooting steps for various errors, see [Common Issues Troubleshooting](https://flowpick.net/docs/troubleshooting/common-issues). For engine's known limitations and edge cases, see [Known Issues](https://flowpick.net/docs/troubleshooting/known-issues). For how to contribute error handling improvements to engine, see [Contributing Guide](https://flowpick.net/docs/developer/contributing).

## Related Documentation

- [Video Sniffing](https://flowpick.net/docs/features/video-sniffing) — HLS/DASH manifest parsing and encryption detection
- [Format Conversion](https://flowpick.net/docs/features/format-conversion) — FFmpeg WASM engine and TSToMP4Muxer
- [Batch Download](https://flowpick.net/docs/features/batch-download) — Queue scheduling and concurrency control
- [Online Tools](https://flowpick.net/docs/advanced/online-tools) — Practical application of write strategies in online tools
- [Browser Compatibility](https://flowpick.net/docs/advanced/browser-compatibility) — API support across browsers and fallback strategies
- [Privacy & Security](https://flowpick.net/docs/features/privacy-security) — Local processing, zero data upload
- [Configuration Reference](https://flowpick.net/docs/getting-started/configuration) — Configuration items like concurrency, output format
- [Project Architecture](https://flowpick.net/docs/developer/architecture) — Engine's position in overall system and module interaction
- [Contributing Guide](https://flowpick.net/docs/developer/contributing) — Contributing code to download engine
- [Live Replay Saving](https://flowpick.net/docs/usecases/live-replays) — Actual scenarios for very large file downloads
- [Online Courses Download](https://flowpick.net/docs/usecases/online-courses) — Course video download scenarios
- [Video Platform Downloads](https://flowpick.net/docs/usecases/video-platforms) — Mainstream platform download practices
- [Common Issues Troubleshooting](https://flowpick.net/docs/troubleshooting/common-issues) — Diagnosis of download failures
- [Known Issues](https://flowpick.net/docs/troubleshooting/known-issues) — Known limitations of download engine
- [FAQ](https://flowpick.net/docs/troubleshooting/faq) — High-frequency download-related questions[Browser Compatibility](https://flowpick.net/docs/advanced/browser-compatibility)

[

FlowPick feature support across different browsers, including API compatibility matrix, fallback strategies, and browser selection recommendations.

](https://flowpick.net/docs/advanced/browser-compatibility)[

Online Courses & Educational Platforms

Use FlowPick to save online course videos for offline learning and content backup.

](https://flowpick.net/docs/usecases/online-courses)
