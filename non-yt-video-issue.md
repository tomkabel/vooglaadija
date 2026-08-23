---
title: "[HIGH] figure out universal and reliable non-youtube media download method · Issue #140 · tomkabel/vooglaadija"
source: "https://github.com/tomkabel/vooglaadija/issues/140"
author:
  - "[[tomkabel]]"
published: 2026-06-26
created: 2026-06-28
description: "is your feature request related to a problem? kind of. youtube is the only path that seems consistently real right now, but the app already"
tags:
  - "clippings"
---
## is your feature request related to a problem?

kind of. youtube is the only path that seems consistently real right now, but the app already accepts tiktok and instagram urls and then falls over later in the pipeline. `fail-result.md` has these two failed entries:

```
| 🔴 Failed | \`https://www.tiktok.com/@khaby.lame/video/7008477449723292934\` | 2d |
| 🔴 Failed | \`https://www.instagram.com/reel/DGcoPAktJAT/\`                  | 2d |
```

tiktok is especially weird because the same url also shows up as a live job in the same report, so this looks more like flaky extractor/auth/session behavior than a clean "not supported" case.

## describe the solution you'd like

i'd like us to make a deliberate call here instead of sitting in the in-between state:

- either make tiktok and instagram work reliably enough to count as supported
- or explicitly treat them as experimental / blocked until we have a stable auth + extraction path

right now we are sort of implying support at validation time and then failing later, which is the worst of both worlds.

## describe alternatives you've considered

- keep the product youtube-only for now and reject tiktok/instagram up front
- gate non-youtube platforms behind a feature flag or admin-only toggle
- keep them best-effort, but show platform-specific failure reasons instead of a generic failed state

## use case

as a user, i want to paste a tiktok or instagram reel url and either get a real download or a clear "not supported right now" answer, so i'm not guessing based on a generic failed row.

## additional context

a decent amount of groundwork is already there:

- `app/utils/validators.py` already accepts `tiktok.com` and `instagram.com`
- `app/services/yt_dlp_service.py` already detects both platforms
- the worker already has cookie hooks via `YT_DLP_COOKIES_FILE` / `YT_DLP_COOKIES_BROWSER`

so this feels less like "add support from scratch" and more like "close the gap between accepted urls and actually reliable extraction."

## implementation notes (optional)

possible directions, not married to any of these:

- validate the current `yt-dlp` version/extractor behavior first; this might just be extractor drift
- add platform-specific retry / format / user-agent settings instead of treating youtube, tiktok, and instagram the same
- support dedicated operator-managed accounts for platforms that need logged-in sessions, then load exported cookies from secrets or a mounted cookie file
- start with manual cookie export from a controlled browser profile; if that proves the path works, decide later whether a compliant refresh workflow is worth automating
- store and surface the real failure reason (`login required`, `challenge required`, `429`, `geo restricted`, etc.) in worker logs and maybe the UI
- add a tiny smoke matrix for youtube / tiktok / instagram so we notice regressions before demo time
- if this gets too brittle or too messy from a policy/compliance standpoint, block these platforms in the product instead of pretending they work

## acceptance criteria

- Move decide whether tiktok and instagram are supported, experimental, or blocked for now
	decide whether tiktok and instagram are supported, experimental, or blocked for now
	Open decide whether tiktok and instagram are supported, experimental, or blocked for now task options
	Move confirm at least one reproducible happy-path url for each platform in a non-local environment
	confirm at least one reproducible happy-path url for each platform in a non-local environment
	Open confirm at least one reproducible happy-path url for each platform in a non-local environment task options
	Move document the session/cookie approach if authenticated extraction is required
	document the session/cookie approach if authenticated extraction is required
	Open document the session/cookie approach if authenticated extraction is required task options
	Move expose platform-specific failure reasons in logs and/or the ui
	expose platform-specific failure reasons in logs and/or the ui
	Open expose platform-specific failure reasons in logs and/or the ui task options
	Move add some form of regression coverage or smoke-check plan for multi-platform support
	add some form of regression coverage or smoke-check plan for multi-platform support
	Open add some form of regression coverage or smoke-check plan for multi-platform support task options
	To pick up a draggable item, press the space bar. While dragging, use the arrow keys to move the item. Press space again to drop the item in its new position, or press escape to cancel.

## related

- [\[Sprint 4\] Implement download service for video URLs #27](https://github.com/tomkabel/team21-vooglaadija/issues/27)
- [Issue 7: Add URL Format Validation for Downloads #81](https://github.com/tomkabel/team21-vooglaadija/issues/81)

---

## Comments

> **tomkabel** · 2026-06-27
> 
> this could be a separate big improvement. what if for non-youtube links, we launch some low footprint rust headless browser in a vm, navigate to link, click play, and detect and download the media like video downloadhelper extension does it.
> 
> this would make the tool universal to nearly all media. only tricky part would be to properly close any popups and clutter and actually starting to play the video/media so the media stream is detected.
> 
> as a last case, if no media stream is detected. then record the playback in browser at 100x speed and later slow it down with ffmpeg.
> 
> that would be epic. if all three are added, then this would turn into something I think no downloader as of currently does.
> 
> very welcome to help and assistance with this.
