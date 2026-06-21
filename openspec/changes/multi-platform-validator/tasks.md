## 1. Validator Implementation

- [x] 1.1 Add domain sets for Vimeo, Dailymotion, Twitch, TikTok, Instagram in `app/utils/validators.py`
- [x] 1.2 Rename `is_youtube_url` to `is_supported_url` with backward-compatible alias
- [x] 1.3 Add combined validation function that checks all platform domain sets using exact domain matching

## 2. Import Updates

- [x] 2.1 Update import in `app/api/routes/web.py` from `is_youtube_url` to `is_supported_url`
- [x] 2.2 Update any error message strings from "YouTube URL" to "supported URL"

## 3. Tests

- [x] 3.1 Write tests for valid URLs from each of the 5 new platforms
- [x] 3.2 Write subdomain bypass attack tests for each new platform
- [x] 3.3 Write backward compatibility test ensuring old `is_youtube_url` name still works
- [x] 3.4 Write integration test creating download jobs via API with non-YouTube URLs
