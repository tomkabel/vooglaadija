## ADDED Requirements

### Requirement: yt-dlp subprocess emits progress JSON to stdout

The yt-dlp subprocess script SHALL register a `progress_hooks` callback on the `YoutubeDL` instance that writes structured JSON progress lines to stdout during download.

#### Scenario: Progress hook fires during download
- **WHEN** yt-dlp is downloading a video
- **THEN** the progress hook SHALL fire at least once during download
- **THEN** each progress line SHALL be a complete JSON object followed by a newline

#### Scenario: Progress line contains required fields
- **WHEN** a progress event fires with `status` equal to `downloading`
- **THEN** the JSON object SHALL contain the fields: `"progress": true`, `"percent"` (float), `"downloaded_bytes"` (int), `"total_bytes"` (int or null), `"speed"` (float or null), `"eta"` (float or null)

#### Scenario: Progress throttled to avoid flooding
- **WHEN** consecutive progress events report `percent` values differing by less than 0.5
- **THEN** the hook SHOULD skip writing to stdout for the later event

#### Scenario: Final output line is extraction result, not progress
- **WHEN** extraction succeeds after download completes
- **THEN** the final line printed to stdout SHALL be `json.dumps(ydl.sanitize_info(info))` without a `"progress"` key

#### Scenario: Progress lines use flush=True
- **WHEN** a progress line is written to stdout
- **THEN** the script SHALL call `flush=True` on the print to ensure the parent process receives it immediately
