---
title: "Video Sniffing"
source: "https://flowpick.net/docs/features/video-sniffing/"
author:
published:
created: 2026-07-13
description: "Complete technical documentation for FlowPick video detection and download, covering HLS, DASH, direct video files, encrypted stream decryption, and troubleshooting."
tags:
  - "clippings"
---
Complete technical documentation for FlowPick video detection and download, covering HLS, DASH, direct video files, encrypted stream decryption, and troubleshooting.

FlowPick automatically detects video content on webpages by monitoring browser network requests, supporting streaming protocols (HLS/DASH) and direct video files, with automatic decryption of AES-128 encrypted streams.

![Video Sniffing Overview](https://flowpick.net/_ipx/_/screenshots/video-sniffing-overview.png)

---

## Detection Principles

FlowPick's video detection is based on the Chrome extension's `webRequest` API, intercepting requests at the browser network layer:

1. **Network Interception**: Extension listens to all network requests via `webRequest.onResponseStarted`, identifying video and streaming resources from the response headers (the response `Content-Type` and related resource detection happen when headers are available, not at request time)
2. **Manifest Parsing**: For HLS (`.m3u8`) and DASH (`.mpd`), automatically downloads and parses manifest files to extract available stream information
3. **Quality Analysis**: Parses all available resolution and bitrate options from Master Playlist
4. **Encryption Detection**: Checks for presence of `EXT-X-KEY` (HLS) or `ContentProtection` (DASH) tags to determine if decryption is needed
5. **Resource Display**: Detected videos are displayed categorized by type in popup window

**Detection Timing:**

- After extension installation, need to **refresh target page** before detection can begin
- Streaming manifests typically load when video starts playing; recommend playing for 3-5 seconds before opening extension
- For dynamically loaded videos (e.g., loading when scrolling to specific position), need to trigger loading before detection

For extension `webRequest` API permissions and Manifest V3 limitations, see [Known Limitations — Manifest V3 Limitations](https://flowpick.net/docs/troubleshooting/known-issues#manifest-v3-%E9%99%90%E5%88%B6). For complete troubleshooting flow when media is not detected, see [Common Issues Troubleshooting — Media Detection Issues](https://flowpick.net/docs/troubleshooting/common-issues).

---

## Supported Video Formats

### Streaming Protocols

| Protocol | Manifest File | Content-Type | Description |
| --- | --- | --- | --- |
| HLS | `.m3u8` | `application/x-mpegurl` | Apple HTTP Live Streaming, most common streaming protocol |
| HLS | `.m3u8` | `application/vnd.apple.mpegurl` | Apple-specific MIME type for HLS |
| DASH | `.mpd` | `application/dash+xml` | MPEG-DASH, commonly used for high-quality content |

### Direct Video Files

| Content-Type | Extension | Description |
| --- | --- | --- |
| `video/mp4` | `.mp4` | Most universal video format |
| `video/x-m4v` | `.mp4` | iTunes video format |
| `video/webm` | `.webm` | Open-source Web video format |
| `video/ogg` | `.ogv` | Ogg container video |
| `video/x-flv` | `.flv` | Flash Video (legacy format) |
| `video/x-matroska` | `.mkv` | Matroska container |
| `video/quicktime` | `.mov` | Apple QuickTime |
| `video/x-msvideo` | `.avi` | Windows AVI |
| `video/3gpp` | `.3gp` | Mobile device video |
| `video/3gpp2` | `.3g2` | CDMA mobile device video |
| `video/mpeg` | `.mpeg` | MPEG video |

---

## HLS (M3U8) Streams

HLS (HTTP Live Streaming) is a streaming protocol developed by Apple and currently the most widely used video transmission method on the web. Major platforms like Bilibili, Tencent Video, and Youku all use HLS.

![HLS Stream Detection Results](https://flowpick.net/_ipx/_/screenshots/hls-detection.png)

### Master Playlist vs Media Playlist

HLS manifests are divided into two levels:

| Type | Content | Example |
| --- | --- | --- |
| Master Playlist | Lists all available quality variants, each pointing to a Media Playlist | Index containing multiple streams at 360p, 720p, 1080p, etc. |
| Media Playlist | Lists specific TS fragment URL sequence | Contains addresses of hundreds of `.ts` files |

FlowPick auto-identifies Master Playlists, parses out all quality options for you to select. After selecting quality, downloads corresponding Media Playlist to get fragment list.

```
Master Playlist (index.m3u8)
    │
    ├── 360p  → 360p.m3u8  →  segment-001.ts, segment-002.ts, ...
    ├── 720p  → 720p.m3u8  →  segment-001.ts, segment-002.ts, ...
    └── 1080p → 1080p.m3u8 →  segment-001.ts, segment-002.ts, ...
```

### Quality Selection

Quality options parsed from Master Playlist are sorted by bitrate from high to low:

| Quality | Typical Resolution | Typical Bitrate | Use Case |
| --- | --- | --- | --- |
| Original/Ultra HD | 1080p+ | 5-12 Mbps | Large screen viewing, best quality needed |
| HD | 720p | 1.5-3 Mbps | Laptop viewing |
| SD | 480p | 0.8-1.5 Mbps | Small screen devices |
| Smooth | 360p | 0.4-0.8 Mbps | Audio only, mobile networks |

For quality selection configuration (auto/manual), see [Configuration Reference — Quality Selection Strategy](https://flowpick.net/docs/getting-started/configuration#%E7%94%BB%E8%B4%A8%E9%80%89%E6%8B%A9%E7%AD%96%E7%95%A5).

### Fragment Download & Merging

HLS videos consist of large numbers of TS fragments (typically 2-10 seconds each). FlowPick's processing flow:

1. Parse Media Playlist to get all fragment URLs
2. Multi-threaded concurrent download of fragments (concurrency adjustable in settings)
3. After download completes, merge fragments into single file
4. If MP4 output selected, use FFmpeg WASM for remuxing

For Worker Pool implementation of concurrent fragment download and retry mechanism, see [Download Engine Architecture](https://flowpick.net/docs/advanced/download-engine). For TS to MP4 remuxing principles, see [Format Conversion](https://flowpick.net/docs/features/format-conversion).

### Encrypted Streams (AES-128)

Some HLS streams use AES-128 encryption to protect content. FlowPick supports automatic decryption:

- Detects `#EXT-X-KEY:METHOD=AES-128,URI="..."` tag
- Auto-downloads decryption key
- Uses Web Crypto API to decrypt each fragment in browser
- Decrypted fragments merge normally, output unencrypted file

FlowPick only supports **AES-128** encryption. Streams using DRM technologies like Widevine, PlayReady, or FairPlay **cannot be decrypted**. If DRM protection detected, corresponding error message displayed. For detailed technical explanation of DRM protection, see [Known Limitations — DRM Protected Content](https://flowpick.net/docs/troubleshooting/known-issues#drm-%E4%BF%9D%E6%8A%A4%E5%86%85%E5%AE%B9).

---

## DASH (MPD) Streams

DASH (Dynamic Adaptive Streaming over HTTP) is an international standard streaming protocol established by MPEG, widely used by platforms like YouTube and Netflix.

![DASH Stream Detection Results](https://flowpick.net/_ipx/_/screenshots/dash-detection.png)

### DASH Characteristics

Compared to HLS, DASH has these characteristics:

| Feature | HLS | DASH |
| --- | --- | --- |
| Fragment Format | Usually TS | Usually FMP4 (segmented MP4) |
| Audio/Video | Usually combined together | Usually separated as independent tracks |
| Manifest Format | M3U8 (text) | MPD (XML) |
| Encryption | AES-128 | AES-128 or DRM |
| Codecs | H.264 dominant | H.264, H.265, VP9, AV1 |

### Separate Audio/Video Processing

A key characteristic of DASH is that video and audio are usually separated into independent tracks. FlowPick's processing flow:

1. Parse MPD manifest, identify video track and audio track
2. Download video fragments and audio fragments separately
3. Download initialization segments (init segment), containing codec configuration info
4. Use FFmpeg WASM to merge video and audio into single MP4 file

```
MPD Manifest
    │
    ├── AdaptationSet: Video (H.264, 1080p)
    │   ├── init segment (codec config)
    │   ├── segment-001.m4s
    │   ├── segment-002.m4s
    │   └── ...
    │
    └── AdaptationSet: Audio (AAC, 128kbps)
        ├── init segment (codec config)
        ├── segment-001.m4s
        ├── segment-002.m4s
        └── ...
                    ↓
            FFmpeg WASM Merge
                    ↓
              output.mp4
```

For technical implementation of DASH FMP4 reassembly and audio-video merging, see [Download Engine Architecture — DASH Processing](https://flowpick.net/docs/advanced/download-engine). For FFmpeg WASM loading and performance, see [Format Conversion — FFmpeg WASM Engine](https://flowpick.net/docs/features/format-conversion).

### Encryption Support

Same as HLS, FlowPick supports AES-128 fragment-level encryption for DASH streams. DRM-protected streams (Widevine, PlayReady) cannot be handled.

---

## Direct Video Files

For direct video files (MP4, WebM, MKV, etc.), detection and download process is simplest:

- **Instant Detection**: Video URL immediately detected when appears in network requests
- **Direct Download**: No fragment merging or format conversion needed, browser directly downloads original file
- **Size Preview**: If server returns `Content-Length` header, file size displayed

Direct video files typically appear in following scenarios:

- MP4 videos embedded directly in websites
- Short videos on social media
- Video assets on gallery websites
- Directly linked video files

For direct video file download operations, see [Usage Guide — Download Single Resource](https://flowpick.net/docs/getting-started/usage#%E4%B8%8B%E8%BD%BD%E5%8D%95%E4%B8%AA%E8%B5%84%E6%BA%90).

---

## Detection Differences: Extension vs Online Tool

| Capability | Browser Extension | Online Tool |
| --- | --- | --- |
| Auto Detection | Auto-monitors all network requests | Requires manual paste of stream URL |
| CORS Restrictions | Unrestricted (extension permissions) | Subject to same-origin policy |
| Encrypted Streams | Supports AES-128 decryption | Supports AES-128 decryption |
| Detection Scope | All requests on current tab | Only user-provided URLs |
| Usage Barrier | Requires extension installation | No installation needed |

If you encounter CORS errors using online tool, it means streaming server hasn't configured cross-origin headers. In this case, recommend installing browser extension to bypass restrictions. For detailed feature comparison between extension and online tool and selection recommendations, see [Online Tools — Comparison with Extension](https://flowpick.net/docs/advanced/online-tools).

## Troubleshooting

### Video Not Detected

| Cause | Solution |
| --- | --- |
| Page loaded before extension installed | Refresh page and replay video |
| Video not started playing | Play video for 3-5 seconds, ensure manifest file loaded |
| Video uses dynamic loading | Drag progress bar to different positions to trigger more fragment requests |
| Website uses non-standard protocols | Check if HLS/DASH/direct video files supported |
| Other extension conflicts | Temporarily disable ad blockers or other network extensions and retry |

### Encrypted Stream Download Failed

| Cause | Solution |
| --- | --- |
| DRM protection (Widevine etc.) | Not supported, FlowPick cannot decrypt DRM content |
| Key server requires authentication | Some paid content keys require Cookie/Token, ensure logged in |
| Key URL inaccessible | Check network connection, key server may be blocked by firewall |

### Slow Download Speed

| Cause | Solution |
| --- | --- |
| Concurrent threads too low | Increase concurrent threads in settings (recommend 4-6) |
| CDN rate limiting | Some CDNs limit per-IP speed, cannot bypass |
| Insufficient network bandwidth | Select lower quality to reduce file size |

### Merged Video Cannot Play

| Cause | Solution |
| --- | --- |
| Some fragments failed to download | Re-download, ensure all fragments complete |
| Output format incompatible | Try switching output format (MP4 ↔ TS) |
| Encryption key error | Refresh page to re-obtain key |

If above methods don't resolve issue, please check [Common Issues Troubleshooting](https://flowpick.net/docs/troubleshooting/common-issues) for more detailed diagnostic steps. For technical limitations and edge cases of current version, see [Known Limitations](https://flowpick.net/docs/troubleshooting/known-issues).

---

## Related Documentation

- [Usage Guide](https://flowpick.net/docs/getting-started/usage) — Operation guide for video detection and download
- [Audio Capture](https://flowpick.net/docs/features/audio-capture) — Complete technical documentation for FlowPick audio detection and download, covering audio formats, podcast detection, metadata extraction, batch download, and troubleshooting.
- [Image Download](https://flowpick.net/docs/features/image-download) — Detection and batch download of image resources
- [Format Conversion](https://flowpick.net/docs/features/format-conversion) — Detailed technical explanation of TS/MP4 remuxing
- [Download Engine Architecture](https://flowpick.net/docs/advanced/download-engine) — Fragment download, concurrency control, retry mechanism
- [Online Tools](https://flowpick.net/docs/advanced/online-tools) — Feature comparison between extension and online tools
- [Configuration Reference](https://flowpick.net/docs/getting-started/configuration) — Complete configuration options for FlowPick, including download settings, filtering rules, save strategies, advanced parameters, and scenario-based configurations.
- [Common Issues Troubleshooting](https://flowpick.net/docs/troubleshooting/common-issues) — Diagnostic methods for download issues
- [Known Limitations](https://flowpick.net/docs/troubleshooting/known-issues) — DRM protection, Manifest V3 and other technical limitations
- [Privacy & Security](https://flowpick.net/docs/features/privacy-security) — Network monitoring and privacy protection notes
