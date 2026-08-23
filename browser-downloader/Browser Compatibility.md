---
title: "Browser Compatibility"
source: "https://flowpick.net/docs/advanced/browser-compatibility/"
author:
published:
created: 2026-07-13
description: "FlowPick feature support across different browsers, including API compatibility matrix, fallback strategies, and browser selection recommendations."
tags:
  - "clippings"
---
FlowPick feature support across different browsers, including API compatibility matrix, fallback strategies, and browser selection recommendations.

FlowPick's feature availability across different browsers depends on browser support for modern Web APIs. This document provides detailed feature compatibility matrix and fallback strategy explanations.

---

## Quick Browser Check

Before starting to use FlowPick, you can quickly determine if your browser meets requirements through following methods:

| Check Item | How to Verify | Alternative if Not Met |
| --- | --- | --- |
| Browser version ≥ 86 | Enter `chrome://version` in address bar | Upgrade browser to latest version |
| Chromium-based kernel | Check browser about page | Switch to Chrome or Edge |
| File System Access API | Open [Online Tools](https://flowpick.net/m3u8-downloader), check if "Select Save Directory" button exists | Use Blob download mode (auto fallback) |
| SharedArrayBuffer | Open [Online Tools](https://flowpick.net/m3u8-downloader), download a TS file and check merge speed | Auto fallback to single-threaded FFmpeg |

If unsure whether your browser is compatible, just install latest version of [Chrome](https://www.google.com/chrome/) or [Edge](https://www.microsoft.com/edge) for best experience. See [Installation Guide](https://flowpick.net/docs/getting-started/installation) for details.

---

## Browser Support Overview

| Browser | Minimum Version | Extension Support | Online Tools | Recommendation Level |
| --- | --- | --- | --- | --- |
| Google Chrome | 86+ | Full support | Full support | Recommended |
| Microsoft Edge | 86+ | Full support | Full support | Recommended |
| Brave | 86+ | Full support | Full support | Recommended |
| Opera | 72+ | Full support | Full support | Usable |
| 360 Speed Browser | Latest version | Full support | Full support | Usable |
| QQ Browser | Latest version | Full support | Full support | Usable |
| Firefox | TBD | Coming soon | Partially supported | In development |
| Safari | Not supported | Not supported | Partially supported | Not recommended |

360 Speed Browser and QQ Browser are both based on Chromium kernel, theoretically compatible with all features. However, domestic browsers may have additional restrictions on some APIs, so recommend prioritizing Chrome or Edge.

## Feature Availability Quick Reference

Following table shows browser support status by feature dimension for quick lookup:

| Feature | Chrome | Edge | Firefox | Safari | Related Documentation |
| --- | --- | --- | --- | --- | --- |
| Extension auto-detection | ✅ | ✅ | 🚧 | ❌ | [Video Sniffing](https://flowpick.net/docs/features/video-sniffing) |
| Online tool download | ✅ | ✅ | ⚠️ | ⚠️ | [Online Tools](https://flowpick.net/docs/advanced/online-tools) |
| Directory selection & persistence | ✅ | ✅ | ❌ | ❌ | [Online Tools — Save Directory](https://flowpick.net/docs/advanced/online-tools#save-directory-selection) |
| Streaming write (FSA) | ✅ | ✅ | ❌ | ❌ | [Download Engine — Write Strategy](https://flowpick.net/docs/advanced/download-engine#write-strategy) |
| StreamSaver.js | ✅ | ✅ | ⚠️ | ⚠️ | [Download Engine](https://flowpick.net/docs/advanced/download-engine) |
| FFmpeg WASM multi-threaded | ✅ | ✅ | ⚠️ | ❌ | [Format Conversion](https://flowpick.net/docs/features/format-conversion) |
| AES-128 decryption | ✅ | ✅ | ✅ | ✅ | [Video Sniffing — Encrypted Streams](https://flowpick.net/docs/features/video-sniffing#encrypted-streams-aes-128) |
| Batch download queue | ✅ | ✅ | ⚠️ | ⚠️ | [Batch Download](https://flowpick.net/docs/features/batch-download) |
| Desktop notifications | ✅ | ✅ | ✅ | ✅ | [Configuration Reference](https://flowpick.net/docs/getting-started/configuration) |
| Large file download (>2GB) | ✅ | ✅ | ❌ | ❌ | [Download Engine](https://flowpick.net/docs/advanced/download-engine) |

> ✅ Full support · ⚠️ Partial support (with fallback) · 🚧 In development · ❌ Not supported

---

## API Compatibility Matrix

FlowPick depends on following browser APIs. Different browser support directly affects feature availability.

### Core APIs

| API | Purpose | Chrome | Edge | Firefox | Safari |
| --- | --- | --- | --- | --- | --- |
| `fetch` | Download segments | 42+ | 14+ | 39+ | 10.1+ |
| `WritableStream` | Streaming write | 59+ | 79+ | 100+ | 14.1+ |
| `Web Crypto API` | AES decryption | 37+ | 79+ | 34+ | 10.1+ |
| `WebAssembly` | FFmpeg runtime | 57+ | 16+ | 52+ | 11+ |
| `Service Worker` | StreamSaver | 45+ | 79+ | 44+ | 11.1+ |
| `Notifications API` | Download notifications | 22+ | 14+ | 22+ | 7+ |

### Advanced APIs

| API | Purpose | Chrome | Edge | Firefox | Safari |
| --- | --- | --- | --- | --- | --- |
| File System Access API | Directory selection & persistence | 86+ | 86+ | ❌ | ❌ |
| `SharedArrayBuffer` | FFmpeg multi-threading | Requires COEP/COOP | Requires COEP/COOP | Requires COEP/COOP | ❌ |
| `showSaveFilePicker` | Save As dialog | 86+ | 86+ | ❌ | ❌ |
| `showDirectoryPicker` | Directory picker | 86+ | 86+ | ❌ | ❌ |
| `requestIdleCallback` | Lazy loading optimization | 47+ | 79+ | 55+ | ❌ |

![API Support Matrix Visualization](https://flowpick.net/_ipx/_/screenshots/api-support-matrix.png)

---

## Feature Fallback Strategies

When a browser doesn't support certain API, FlowPick automatically falls back to alternative solution to ensure core functionality is always available.

### File Write Fallback Chain

```
File System Access API (Best)
    ↓ Fallback when unavailable
StreamSaver.js (Streaming write)
    ↓ Fallback when unavailable
Blob + <a> download (Memory write, has size limit)
```

![Write Strategy Fallback Flow](https://flowpick.net/_ipx/_/screenshots/write-strategy-fallback.png)

**Impact when File System Access API unavailable**:

- Cannot use "Select Save Directory" feature
- Need to manually select save location each time (popup mode)
- Large files (>1.5GB) may fail due to memory limitations

**Impact when StreamSaver.js unavailable**:

- File fully loaded into memory before triggering download
- Subject to Blob size limit (1.5GB hard cap)
- Warning displayed before large file downloads

For detailed comparison of three write strategies, applicable scenarios, and performance data, see [Download Engine Architecture — Write Strategy](https://flowpick.net/docs/advanced/download-engine#write-strategy).

### FFmpeg Multi-threading Fallback

```
SharedArrayBuffer available → Multi-threaded FFmpeg (Fast)
    ↓ Fallback when unavailable
SharedArrayBuffer unavailable → Single-threaded FFmpeg (Slower)
    ↓ Fallback when FFmpeg loading fails
TS format → Direct binary concatenation (Fastest, no FFmpeg needed)
```

**Impact when SharedArrayBuffer unavailable**:

- FFmerge speed reduced by ~40-60%
- Functionality completely unaffected, only slower
- Selecting TS output format can completely bypass FFmpeg

For FFmpeg WASM loading mechanism, memory management, and performance optimization, see [Format Conversion — FFmpeg WASM Engine](https://flowpick.net/docs/features/format-conversion#ffmpeg-wasm-engine). For TSToMP4Muxer streaming remuxing (alternative that doesn't need FFmpeg), see [Format Conversion — Dual Engine Architecture](https://flowpick.net/docs/features/format-conversion#technical-principles-of-conversion).

### Notification Fallback

```
Notifications API available → System desktop notification
    ↓ Fallback when unavailable
In-app status prompt
```

---

## Security Header Requirements

FlowPick website needs following HTTP response headers configured to enable all features:

### Required Security Headers

```
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

These two headers are prerequisites for `SharedArrayBuffer`, affecting FFmpeg multi-threaded mode.

### Recommended Security Headers

```
X-Frame-Options: SAMEORIGIN
Cross-Origin-Resource-Policy: same-origin
```

These headers ensure StreamSaver.js's mitm.html and Service Worker work properly.

FlowPick official website has configured above security headers. If you deploy yourself, refer to configuration instructions in [Online Tools — Technical Limitations](https://flowpick.net/docs/advanced/online-tools#technical-limitations).

### Deployment Checklist

If you deploy FlowPick website yourself, please confirm:

- `/` path returns `Cross-Origin-Opener-Policy: same-origin`
- `/` path returns `Cross-Origin-Embedder-Policy: require-corp`
- `/mitm.html` accessible normally
- `/streamsaver-sw.js` accessible normally with correct Service-Worker-Allowed header
- `/_nuxt/*` static resource paths have correct COEP/COOP headers set

---

## Detailed Browser Descriptions

### Google Chrome (Recommended)

**Version requirement**: 86+

**All features supported**:

- File System Access API (directory selection, persistent permissions)
- SharedArrayBuffer (requires COEP/COOP headers)
- StreamSaver.js
- FFmpeg WASM multi-threading
- All extension features

**Known issues**: None

Chrome is FlowPick's main development and testing platform; all features most thoroughly verified on Chrome. Recommend installing extension from [Chrome Web Store](https://chromewebstore.google.com/). See [Installation Guide](https://flowpick.net/docs/getting-started/installation) for details.

### Microsoft Edge (Recommended)

**Version requirement**: 86+

**All features supported**: Same as Chrome (based on Chromium)

**Known issues**:

- Some enterprise policies may disable File System Access API
- Extension needs to be installed from Edge Add-ons store

Edge shares Chromium kernel with Chrome, complete functional compatibility. If your company computer comes with Edge pre-installed and cannot install Chrome, Edge is perfect alternative. See [Installation Guide — Edge](https://flowpick.net/docs/getting-started/installation#microsoft-edge).

### Firefox (In Development)

**Current status**: Extension version in development, online tools partially available

**Limitations**:

- Does not support File System Access API
- Does not support `showSaveFilePicker`
- SharedArrayBuffer requires COEP/COOP headers (Firefox 79+)
- Limited StreamSaver.js support

**Fallback behavior**: All downloads use Blob mode

Firefox extension version is under development. Before official release, Firefox users can use [Online Tools](https://flowpick.net/docs/advanced/online-tools) for downloading, but functionality will be limited (no directory selection, large files subject to Blob limitations).

### Safari (Not Recommended)

**Current status**: Extension version not supported

**Limitations**:

- Does not support File System Access API
- Does not support SharedArrayBuffer
- Does not support `showSaveFilePicker`
- Does not support `showDirectoryPicker`
- Limited WebAssembly support
- Restricted Service Worker functionality

**Fallback behavior**: Only supports basic Blob download mode, FFmpeg may fail to load

---

## Mobile Browsers

FlowPick is primarily designed for desktop browsers. Mobile browser extension support is limited:

| Platform | Extension Support | Online Tools |
| --- | --- | --- |
| Android Chrome | No extension support | Partially available |
| Android Firefox | Limited support | Partially available |
| iOS Safari | No extension support | Partially available |
| iOS Chrome | No extension support | Partially available |

Mobile limitations:

- Does not support File System Access API
- Stricter memory limitations
- Download management experience inferior to desktop

If you need to download streaming media on mobile, recommend using FlowPick on desktop to download then transfer to mobile device. Mobile browser memory and API limitations make large file download experience poor.

---

## How to Choose Browser

| Use Case | Recommended Browser | Reason |
| --- | --- | --- |
| Daily use, pursuing best experience | Google Chrome | Most complete features, most thoroughly tested |
| Company computer, cannot install Chrome | Microsoft Edge | Same kernel as Chrome, pre-installed on Windows |
| Privacy-focused | Brave | Based on Chromium, built-in privacy protection |
| Domestic users, accustomed to domestic browsers | 360 Speed Browser / QQ Browser | Based on Chromium, good compatibility |
| Only using online tools | Chrome / Edge / Firefox | Online tools have lower browser requirements |
| Need to download very large files (>2GB) | Chrome / Edge | Need File System Access API for streaming write |

Regardless of which browser you choose, recommend keeping it updated to latest version. Older versions may lack key API support, causing feature degradation or unavailability.

## FAQ

### Why don't I see "Select Save Directory" button?

"Select Save Directory" relies on File System Access API, requires Chrome/Edge 86+. If your browser version meets requirements but still doesn't show it, enterprise policy may have disabled this API. Will automatically fall back to Blob download mode.

### What if FFmpeg merge is very slow?

FFmpeg multi-threaded mode requires `SharedArrayBuffer`, which requires website to configure COEP/COOP security headers. If you deploy FlowPick yourself without configuring these headers, FFmpeg will automatically fall back to single-threaded mode, reducing speed by ~40-60%.

**Solutions**:

1. Use FlowPick official website (security headers already configured)
2. Select TS output format to skip FFmpeg merging
3. Configure COEP/COOP headers when deploying yourself

### Can I use FlowPick on mobile?

Mobile browsers don't support extensions, but can use [Online Tools](https://flowpick.net/docs/advanced/online-tools). However, mobile devices have memory limitations and missing APIs, making large file download experience poor. Recommend completing downloads on desktop.

### When will Firefox have full support?

Firefox extension version is under development. Firefox's Manifest V3 support is still being improved, and File System Access API currently has no implementation plan in Firefox. Before extension release, Firefox users can use online tools.

---

## Related Documentation

- [Installation Guide](https://flowpick.net/docs/getting-started/installation) — Chrome/Edge extension installation steps
- [Online Tools](https://flowpick.net/docs/advanced/online-tools) — Web-based downloader requiring no installation
- [Download Engine Architecture](https://flowpick.net/docs/advanced/download-engine) — Write strategies and fallback logic details
- [Format Conversion](https://flowpick.net/docs/features/format-conversion) — FFmpeg WASM engine and dual engine architecture
- [Video Sniffing](https://flowpick.net/docs/features/video-sniffing) — Extension auto-detection principles
- [Batch Download](https://flowpick.net/docs/features/batch-download) — Queue management and concurrency control
- [Configuration Reference](https://flowpick.net/docs/getting-started/configuration) — Download behavior configuration items
- [Common Issues Troubleshooting](https://flowpick.net/docs/troubleshooting/common-issues) — Diagnosis of browser-related issues
- [Known Issues](https://flowpick.net/docs/troubleshooting/known-issues) — Known limitations per browser[Online Tools](https://flowpick.net/docs/advanced/online-tools)

[

M3U8/HLS and MPD/DASH streaming downloaders that work without installing extensions — get stream URL, paste it, select quality, download with one click.

](https://flowpick.net/docs/advanced/online-tools)[

Download Engine Architecture

Technical architecture details of FlowPick's download engine — segment concurrent download, AES-128 decryption, three-tier write strategy, memory safety management, and progress tracking.

](https://flowpick.net/docs/advanced/download-engine)
