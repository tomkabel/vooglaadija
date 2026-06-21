## ADDED Requirements

### Requirement: Persist video title on DownloadJob
The system SHALL store a human-readable video title on the `DownloadJob` model after extraction.

- The `DownloadJob` model SHALL have a nullable `title` column of type `String(255)`
- The worker SHALL extract the title from yt-dlp's `info.get("title")` during job processing
- The title SHALL be persisted to the database when the job status is updated to `completed`
- If yt-dlp returns no title, the field SHALL remain `NULL`

#### Scenario: Title extracted and stored during processing
- **WHEN** the worker processes a job and yt-dlp returns a title
- **THEN** the `title` field on the `DownloadJob` SHALL be set to that value

#### Scenario: No title returned by yt-dlp
- **WHEN** the worker processes a job and yt-dlp does not return a title
- **THEN** the `title` field SHALL remain `NULL`

### Requirement: Surface title in all API responses and SSE events
The system SHALL include the `title` field in the download job API response and SSE streaming events.

- `DownloadResponse` schema SHALL include an optional `title` field
- SSE event payload (`_job_to_sse_data`) SHALL include a `title` field
- The HTMX form handler in `web.py` SHALL include `title` in the published SSE event

#### Scenario: API returns title in response
- **WHEN** a client calls `GET /api/v1/downloads/{id}`
- **THEN** the response SHALL include a `title` field when the job has one

#### Scenario: SSE event includes title
- **WHEN** a job status update is published via SSE
- **THEN** the SSE event payload SHALL contain a `title` field

### Requirement: Display title in web UI
The system SHALL display the video title instead of the raw URL in the download jobs table.

- The `_download_list.html` template SHALL render `job.title` if present, otherwise `job.url`
- The `_download_item.html` partial SHALL render `job.title` if present, otherwise `job.url`
- The client-side JavaScript `getRowHTML()` function in `dashboard.html` SHALL display `data.title` if present, otherwise `data.url`

#### Scenario: Title available — displayed instead of URL
- **WHEN** the download list is rendered and `job.title` is not null
- **THEN** the title text SHALL be displayed in place of the URL

#### Scenario: Title not available — URL shown as fallback
- **WHEN** the download list is rendered and `job.title` is null
- **THEN** the raw URL SHALL be displayed
