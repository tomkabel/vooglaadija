## ADDED Requirements

### Requirement: System SHALL validate URLs from 6 platforms

The URL validator SHALL accept URLs from YouTube, Vimeo, Dailymotion, Twitch, TikTok, and Instagram using exact domain matching to prevent subdomain bypass attacks.

#### Scenario: Valid Vimeo URL is accepted

- **WHEN** `is_supported_url("https://vimeo.com/123456789")` is called
- **THEN** it SHALL return `True`

#### Scenario: Valid Dailymotion URL is accepted

- **WHEN** `is_supported_url("https://www.dailymotion.com/video/abc123")` is called
- **THEN** it SHALL return `True`

#### Scenario: Valid Twitch URL is accepted

- **WHEN** `is_supported_url("https://www.twitch.tv/videos/123456789")` is called
- **THEN** it SHALL return `True`

#### Scenario: Valid TikTok URL is accepted

- **WHEN** `is_supported_url("https://www.tiktok.com/@user/video/123456789")` is called
- **THEN** it SHALL return `True`

#### Scenario: Valid Instagram URL is accepted

- **WHEN** `is_supported_url("https://www.instagram.com/p/ABC123/")` is called
- **THEN** it SHALL return `True`

### Requirement: Validator SHALL prevent subdomain bypass attacks

The validator SHALL use exact domain matching, not substring matching, for all platforms. A URL like `vimeo.com.evil.com` SHALL NOT be accepted.

#### Scenario: Subdomain bypass rejected for Vimeo

- **WHEN** `is_supported_url("https://vimeo.com.evil.com/")` is called
- **THEN** it SHALL return `False`

### Requirement: Backward compatibility alias SHALL exist

The function SHALL be renamed from `is_youtube_url` to `is_supported_url`. The old name `is_youtube_url` SHALL remain as a backward-compatible alias.

#### Scenario: Old function name still works

- **WHEN** `is_youtube_url("https://www.youtube.com/watch?v=abc123")` is called
- **THEN** it SHALL return `True`

### Requirement: API validation SHALL accept all supported platforms

The download job creation endpoint and web form SHALL accept URLs from all 6 supported platforms. Error messages SHALL say "supported URL" instead of "YouTube URL".

#### Scenario: Vimeo URL accepted via API

- **WHEN** a POST request to `/api/v1/downloads` includes `{"url": "https://vimeo.com/123456789"}`
- **THEN** the system SHALL create a download job for that URL

#### Scenario: Unsupported platform rejected

- **WHEN** a POST request to `/api/v1/downloads` includes `{"url": "https://facebook.com/video/abc"}`
- **THEN** the system SHALL return a 422 validation error
