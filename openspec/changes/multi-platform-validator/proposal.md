## Why

The current validator only accepts YouTube URLs. yt-dlp (the extraction engine) already supports 1000+ sites — only the URL validator gates access. Adding 5 major platforms is a ~10 minute change that signals breadth and production-readiness. Every standout team in the competitive analysis demoed breadth of functionality. This closes that gap with negligible risk.

## What Changes

- **Modify** `app/utils/validators.py`: Add 5 new domain sets for Vimeo, Dailymotion, Twitch, TikTok, Instagram
- **Rename** `is_youtube_url()` → `is_supported_url()`: Reflect broader scope (old name kept as alias for backward compatibility)
- **Update** all import references from `is_youtube_url` to `is_supported_url` across the codebase
- **Update** validator tests in `tests/test_utils/` to cover new domains
- **Update** API validation schemas to use new validator

## Capabilities

### New Capabilities

- `multi-platform-validation`: URL validation for 6 platforms — YouTube, Vimeo, Dailymotion, Twitch, TikTok, Instagram. Same security guarantees (exact domain matching, no subdomain bypass).

### Modified Capabilities

- (No existing specs — this is a fresh capability)

## Impact

- **Modified file:** `app/utils/validators.py` (~10 new lines for domain sets, rename)
- **Modified file:** `app/api/routes/web.py` (update import)
- **Modified file:** `app/api/routes/downloads.py` (update import)
- **Modified tests:** `tests/test_utils/test_validators.py`
- **New tests:** Add test cases for each new domain
- **Zero risk:** yt-dlp already handles extraction on all added platforms
- **Non-breaking:** Old name `is_youtube_url` kept as alias for backward compatibility
