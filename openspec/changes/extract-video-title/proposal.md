## Why

The download jobs table currently displays the raw YouTube URL as the primary identifier for each job. This is not user-friendly — users want to see the human-readable video title (e.g., "Never Gonna Give You Up") instead of a long URL, making the list scannable and useful at a glance.

## What Changes

- Add a `title` column to the `DownloadJob` database model
- Extract the video title from yt-dlp metadata during job processing (in the worker) and store it in the `title` field
- Include `title` in the `DownloadResponse` API schema and SSE event payloads
- Update the web templates to render `title` instead of `url` as the primary display text
- Update the client-side JavaScript (SSE row builder) to use `title` instead of `url`
- Add a lightweight title pre-fetch at job creation time using yt-dlp metadata extraction (non-blocking)
- Fall back to displaying the URL when no title is available (e.g., during initial pending state)

## Capabilities

### New Capabilities

- `video-title-extraction`: Extract and persist human-readable video title from yt-dlp metadata during job processing, and surface it in API responses, SSE events, and the web UI

### Modified Capabilities

*(No existing capabilities are being modified.)*

## Impact

- **Database**: New `title` nullable column on `download_jobs` table (requires Alembic migration)
- **Model**: Add `title` field to `DownloadJob` model
- **Schema**: Add `title` to `DownloadResponse` and `DownloadCreate` response handling
- **Worker**: Extract and store `title` in `processor.py` from yt-dlp info dict
- **SSE**: Add `title` to `_job_to_sse_data` and web.py publish payloads
- **Templates**: Update `_download_list.html` and `_download_item.html` to show `title` instead of `url`
- **Client JS**: Update `getRowHTML()` in `dashboard.html` to display `title`
- **yt-dlp Service**: Return `title` alongside `(file_path, file_name)` from `extract_media`
