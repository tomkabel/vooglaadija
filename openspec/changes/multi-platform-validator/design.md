## Context

The current `is_youtube_url()` validator at `app/utils/validators.py` performs exact domain matching to prevent subdomain bypass attacks. It's secure but YouTube-only. yt-dlp already supports 1000+ sites — the validator is the only gate. Adding 5 major platforms requires ~10 lines of new domain declarations with zero architectural change because the extraction engine already handles them.

## Goals / Non-Goals

**Goals:**

- Add Vimeo, Dailymotion, Twitch, TikTok, Instagram to the supported URL validator
- Maintain exact domain matching security guarantee — no subdomain bypass
- Backward compatibility — old `is_youtube_url` name works as alias

**Non-Goals:**

- Not adding platform-specific extraction logic — yt-dlp handles this
- Not adding per-platform validation rules — all platforms use the same URL parsing
- Not adding domain discovery automation — manual add for the 5 target platforms

## Decisions

1. **Separate domain sets over combined set**: Each platform gets its own `_DOMAINS` set (e.g., `_VIMEO_DOMAINS`), keeping the exact-domain-matching pattern from YouTube. Alternative: single combined set — harder to maintain and trace. Decision: per-platform sets with a combined `_EXTRA_DOMAINS` for iteration.

1. **Function rename over overload**: Rename `is_youtube_url` to `is_supported_url` with `is_youtube_url` as a backward-compatible alias. Alternative: keep old name and add new function — leads to confusing naming. Decision: clean rename with alias.

1. **Same set of jobs API validation**: The dashboard job creation form and API both use the same validator function. No changes needed to the form or endpoint — just the shared validator. Alternative: separate validators — unnecessary duplication. Decision: single validator shared across all entry points.

## Risks / Trade-offs

- [yt-dlp extraction fails on non-YouTube site] We can validate the URL but extraction might fail. → Mitigation: yt-dlp is the mature extraction engine; failures are handled by existing retry logic. For the demo, pre-seed completed jobs as fallback.
- [New domains added but UI still says "YouTube URL"] Copy mismatch in error messages. → Mitigation: update error messages to say "supported URL".
