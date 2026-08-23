---
title: "Online Tools"
source: "https://flowpick.net/docs/advanced/online-tools/"
author:
published:
created: 2026-07-13
description: "M3U8/HLS and MPD/DASH streaming downloaders that work without installing extensions — get stream URL, paste it, select quality, download with one click."
tags:
  - "clippings"
---
M3U8/HLS and MPD/DASH streaming downloaders that work without installing extensions — get stream URL, paste it, select quality, download with one click.

In addition to the browser extension, FlowPick provides two independent online tool pages that allow downloading streaming media without installing any software. These tools share the same download engine as the extension but run in a web environment.

---

## When to Use Online Tools

Online tools are a supplementary solution to the extension, suitable for following scenarios:

| Scenario | Recommended Method | Reason |
| --- | --- | --- |
| Download anytime during daily browsing | [Browser Extension](https://flowpick.net/docs/getting-started/installation) | Auto-detection, one-click download |
| Already have stream URL, just want quick download | Online tools | No installation needed, open and use |
| Environments where extensions cannot be installed (company computers, public devices) | Online tools | Pure web page, no installation required |
| Need to batch download multiple known URLs | Online tools + [Batch Download](https://flowpick.net/docs/features/batch-download) | Paste one by one, queue management |
| Encounter CORS restrictions | [Browser Extension](https://flowpick.net/docs/getting-started/installation) | Extension permissions can bypass same-origin policy |

If unsure which method to use, prioritize installing the extension. Online tools are suitable for temporary scenarios where "you have a stream URL and don't want to install an extension". See [Installation Guide](https://flowpick.net/docs/getting-started/installation) for details.

---

## Differences from Extension

| Feature | Browser Extension | Online Tools |
| --- | --- | --- |
| Auto-detect media | Supported, auto-sniffs on page load | Not supported, requires manual paste of URL |
| Installation requirement | Requires extension installation | No installation needed, just open webpage |
| Cross-origin restrictions | Extension permissions can bypass some restrictions | Subject to browser same-origin policy |
| Download engine | Same | Same |
| Save directory selection | Supported | Supported |
| Use case | Anytime download during daily browsing | Quick download when stream URL already available |

Online tools and extension use exactly the same download engine (`useStreamMerge`), with identical download speed, merge logic, and encryption/decryption capabilities. The only difference is in media detection method.

## M3U8/HLS Stream Downloader

Access path: `/m3u8-downloader`

![M3U8 Downloader Interface](https://flowpick.net/_ipx/_/screenshots/m3u8-downloader.png)

### Overview

M3U8 downloader specifically handles HLS (HTTP Live Streaming) streaming protocol. Supports both Master Playlist and Media Playlist manifest formats.

For detailed principles of HLS protocol (Master Playlist vs Media Playlist, segment structure, encryption mechanism), see [Video Sniffing — HLS Streams](https://flowpick.net/docs/features/video-sniffing#hlsm3u8-streams).

### Usage Flow

1. **Get M3U8 URL**: Obtain `.m3u8` address from browser developer tools (Network panel) or third-party tools
2. **Paste URL**: Paste address into input field, tool will automatically parse manifest
3. **Select quality**: If Master Playlist, select target resolution from quality list
4. **Configure parameters**: Set concurrent thread count, output format, and filename
5. **Start download**: Click download button, tool will automatically download all segments and merge

Don't know how to get M3U8 address? Refer to F12 developer tool guide in [Video Platforms — Manual URL Retrieval Mode](https://flowpick.net/docs/usecases/video-platforms#manual-url-retrieval-mode).

### Supported Manifest Types

**Master Playlist (Multi-bitrate)**

Contains multiple sub-streams of different qualities. After parsing, displays quality selector:

```
1080p · 5.2 Mbps · avc1.640028
720p · 2.8 Mbps · avc1.64001f
480p · 1.2 Mbps · avc1.64001e
```

After selecting a quality, tool will download corresponding Media Playlist and get segment list.

![Quality Selector Dropdown](https://flowpick.net/_ipx/_/screenshots/quality-selector.png)

**Media Playlist (Single-bitrate)**

Directly contains TS segment list, no need to select quality. After parsing, directly displays segment count and estimated size.

### Encryption Support

M3U8 downloader supports following encryption methods:

| Encryption Method | Support Status | Description |
| --- | --- | --- |
| AES-128 CBC | Fully supported | Auto-obtains key and decrypts segments |
| SAMPLE-AES | Not supported | Requires dedicated decoder |
| Clear Key | Not supported | DRM protected content |
| Widevine / PlayReady | Not supported | Commercial DRM, cannot decrypt |

For AES-128 encrypted streams, tool will:

1. Parse `#EXT-X-KEY` tag to get key URI
2. Download key file
3. Use Web Crypto API (`crypto.subtle.decrypt`) to decrypt each segment in browser
4. Merge decrypted segments into final file

All decryption operations are completed locally in browser; keys and segment data are not uploaded to any server. See [Privacy & Security](https://flowpick.net/docs/features/privacy-security) for details.

### Segment Download & Merge

Download process is divided into three phases:

**Phase 1: Parsing (0% ~ 5%)**

- Download and parse M3U8 manifest
- Extract segment URI list
- Sample estimate total size

**Phase 2: Download (5% ~ 85%)**

- Multi-threaded concurrent segment download
- Real-time display of progress, speed, and estimated remaining time
- Auto-retry failed segments (up to 3 times, exponential backoff)

**Phase 3: Merge (85% ~ 100%)**

- Concatenate segments in order
- For TS format: Direct binary concatenation (fastest)
- For MP4 output: Use FFmpeg WASM for container conversion

For underlying implementation of concurrent download (Worker Pool, retry strategy, memory management), see [Download Engine Architecture](https://flowpick.net/docs/advanced/download-engine). For detailed principles of TS→MP4 remuxing, see [Format Conversion](https://flowpick.net/docs/features/format-conversion).

### Progress Information

Real-time display during download:

| Metric | Description |
| --- | --- |
| Progress percentage | Completed segments / Total segments |
| Download speed | Real-time speed estimate based on sliding window |
| Estimated remaining time | Calculated based on current speed and remaining segments |
| Downloaded bytes | Cumulative downloaded data volume |

![Download Progress Interface](https://flowpick.net/_ipx/_/screenshots/download-progress.png)

---

## MPD/DASH Stream Downloader

Access path: `/dash-downloader`

![DASH Downloader Interface](https://flowpick.net/_ipx/_/screenshots/dash-downloader.png)

### Overview

DASH downloader handles MPEG-DASH streaming protocol. Unlike HLS, DASH streams' video and audio are usually separated and need to be downloaded separately then merged.

For detailed principles of DASH protocol (MPD structure, audio-video separation, FMP4 packaging), see [Video Sniffing — DASH Streams](https://flowpick.net/docs/features/video-sniffing#dashmpd-streams).

### Usage Flow

1. **Get MPD URL**: Obtain `.mpd` address from browser developer tools
2. **Paste URL**: Paste address into input field
3. **Select quality**: Select from parsed video stream list
4. **Configure parameters**: Set concurrent thread count and filename
5. **Start download**: Tool automatically downloads video and audio segments and merges them

### MPD Manifest Parsing

DASH downloader uses `mpd-parser` library to parse MPD manifest, extracting following information:

- **Period**: Time period definition
- **AdaptationSet**: Media type grouping (video/audio)
- **Representation**: Specific quality/bitrate options
- **SegmentTemplate / SegmentList**: Segment URL pattern

Parsing results sorted by bitrate descending, display format:

```
1080p · 5.2 Mbps · avc1.640028
720p · 2.8 Mbps · avc1.64001f
```

### Audio-Video Separate Processing

A key characteristic of DASH streams is that video and audio tracks are separated. Downloader processing flow:

1. Parse MPD, identify video and audio AdaptationSets
2. After user selects video quality, auto-match best audio stream (usually highest bitrate)
3. Parallel download of video segments and audio segments
4. Use FFmpeg WASM to merge video and audio into single MP4 file

Merge command equivalent to:

```
ffmpeg -f concat -i video_list.txt -f concat -i audio_list.txt \
  -c:v copy -c:a copy -movflags +faststart output.mp4
```

DASH audio-video merge uses FFmpeg WASM engine. For FFmpeg WASM loading mechanism, memory limitations, and performance optimization, see [Format Conversion — FFmpeg WASM Engine](https://flowpick.net/docs/features/format-conversion#ffmpeg-wasm-engine).

### FMP4 Support

For DASH streams using FMP4 (Fragmented MP4) packaging, downloader supports:

- Identify FMP4 identifiers like `urn:mpeg:dash:ffmpeg:2013`
- Download init segments and media segments
- Use FFmpeg WASM to reassemble FMP4 segments into standard MP4

### Encryption Support

| Encryption Method | Support Status |
| --- | --- |
| AES-128 CTR | Partially supported |
| Widevine | Not supported |
| PlayReady | Not supported |
| ClearKey | Not supported |

---

## Common Features

### Save Directory Selection

Both online tools support File System Access API's directory persistence feature:

1. Click "Select Save Directory" button
2. Select target folder in system dialog that pops up
3. After authorization, all subsequent downloads write directly to that directory
4. Directory name displayed on interface, can be cleared or changed anytime

![Save Directory Selection](https://flowpick.net/_ipx/_/screenshots/save-directory.png)

**Technical Implementation:**

- Use `window.showDirectoryPicker()` to get directory handle
- Request persistent permission via `handle.requestPermission({ mode: 'readwrite' })`
- Directory name cached in `localStorage` for UI display
- Permission status silently checked via `navigator.permissions.query()`

File System Access API requires Chrome 86+ or Edge 86+. If your browser doesn't support it, will automatically fall back to StreamSaver.js or Blob download. See [Browser Compatibility](https://flowpick.net/docs/advanced/browser-compatibility) for details.

### Write Strategy Priority

Download engine selects file writing method by following priority:

| Priority | Strategy | Memory Usage | Applicable Conditions |
| --- | --- | --- | --- |
| 1 | File System Access API | Extremely low (streaming write) | Chrome 86+, Edge 86+ |
| 2 | StreamSaver.js | Extremely low (streaming write) | Requires Service Worker support |
| 3 | Blob download | Equal to file size | Fallback option, has 1.5GB limit |

For detailed comparison of write strategies and fallback logic, see [Download Engine Architecture — Write Strategy](https://flowpick.net/docs/advanced/download-engine#write-strategy).

### Error Handling

Errors that may be encountered during download and how they're handled:

| Error Type | Cause | Handling Method |
| --- | --- | --- |
| HTTP 403 | Resource requires authentication or Referer validation | Prompt user to check if URL is valid |
| HTTP 404 | Segment expired or URL incorrect | Prompt user to re-obtain stream address |
| CORS error | Cross-origin request rejected by server | Recommend using browser extension version |
| Network error | Connection interrupted or timeout | Auto-retry 3 times |
| Unsupported encryption | Encountered unsupported encryption method | Clearly indicate unable to process |
| DRM protection | Content DRM protected | Inform user content cannot be downloaded |

CORS errors are most common issue. In this case recommend installing [browser extension](https://flowpick.net/docs/getting-started/installation), which can bypass same-origin policy restrictions. See [Common Issues Troubleshooting](https://flowpick.net/docs/troubleshooting/common-issues) for more troubleshooting methods.

### After Download Completion

Following operations provided after download completion:

- **Copy path**: Copy file save path to clipboard
- **Download another**: Reset state, prepare for next download
- **Clear save directory**: Remove directory authorization

---

## Technical Limitations

Online tools have following limitations compared to extension version:

1. **Cross-origin restrictions**: Browser same-origin policy may block downloading segments from certain CDNs. Extension version can bypass some restrictions via `webRequest` permissions
2. **Referer validation**: Some streaming servers require specific Referer headers, which online tools cannot customize
3. **Service Worker dependency**: StreamSaver write strategy requires Service Worker support, and requires page to set correct COEP/COOP headers
4. **SharedArrayBuffer**: FFmpeg WASM multi-threaded mode requires `SharedArrayBuffer`, which requires page to set `Cross-Origin-Isolated` header

FlowPick website has configured necessary security headers to support these features:

```
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

For technical background of COEP/COOP headers and browser compatibility, see [Browser Compatibility — Security Header Requirements](https://flowpick.net/docs/advanced/browser-compatibility#security-header-requirements).

## Typical Use Cases

### Scenario 1: Quick Download of Known Stream Address

```
Get M3U8 URL → Open /m3u8-downloader → Paste → Select quality → Download
```

Suitable for scenarios where stream address has been obtained via F12 developer tools. Refer to [Video Platforms — Manual URL Retrieval](https://flowpick.net/docs/usecases/video-platforms#manual-url-retrieval-mode).

### Scenario 2: Temporary Device Download

```
Public computer → Open FlowPick website → Paste stream address → Download to USB drive
```

No software installation needed, suitable for environments like company computers, school computer labs, internet cafes where extensions cannot be installed.

### Scenario 3: Batch Download Multiple Streams

```
Collect all M3U8 URLs → Download each one in online tool → Queue auto-managed
```

Suitable for scenarios needing to download multiple course videos or series videos. Refer to [Online Courses Download](https://flowpick.net/docs/usecases/online-courses).

---

## Related Documentation

- [Installation Guide](https://flowpick.net/docs/getting-started/installation) — Browser extension installation method
- [Usage Guide](https://flowpick.net/docs/getting-started/usage) — Complete operation guide for extension and online tools
- [Video Sniffing](https://flowpick.net/docs/features/video-sniffing) — Detection principles for HLS/DASH protocols
- [Format Conversion](https://flowpick.net/docs/features/format-conversion) — TS→MP4 remuxing and FFmpeg WASM
- [Batch Download](https://flowpick.net/docs/features/batch-download) — Multi-resource queue management
- [Privacy & Security](https://flowpick.net/docs/features/privacy-security) — Local processing, zero data upload
- [Download Engine Architecture](https://flowpick.net/docs/advanced/download-engine) — Concurrent download and write strategies
- [Browser Compatibility](https://flowpick.net/docs/advanced/browser-compatibility) — FSA/StreamSaver support status
- [Video Platform Downloads](https://flowpick.net/docs/usecases/video-platforms) — Stream address retrieval methods for various platforms
- [Online Courses Download](https://flowpick.net/docs/usecases/online-courses) — Course video batch download
- [Live Replay Saving](https://flowpick.net/docs/usecases/live-replays) — Large file streaming download
- [Common Issues Troubleshooting](https://flowpick.net/docs/troubleshooting/common-issues) — Diagnosis of CORS errors and other issues
- [Known Issues](https://flowpick.net/docs/troubleshooting/known-issues) — Known limitations of online tools
- [Keyboard Shortcuts](https://flowpick.net/docs/features/keyboard-shortcuts) — Complete reference for FlowPick keyboard shortcuts — default bindings, customization methods, conflict resolution, and efficiency workflows.
