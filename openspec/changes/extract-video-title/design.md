## Context

The download jobs table on `/web/downloads` currently renders the raw YouTube URL as the primary identifier. The yt-dlp extraction service already extracts `info.get("title")` during job processing, but this value is only used to construct the `file_name` field and is never stored in the database or surfaced in the UI.

The change requires: a new `title` database column, extracting the title from yt-dlp in the worker and persisting it, and updating the full display chain (API schema, SSE payloads, templates, client-side JS).

## Goals / Non-Goals

**Goals:**
- Display human-readable video title instead of raw URL in the downloads table
- Extract and persist the title during job processing using the existing yt-dlp call
- Include title in all API responses, SSE events, and web templates
- Fall back to showing the URL when title is unavailable (pending/failed states)

**Non-Goals:**
- Not extracting title at job creation time (relies on worker processing)
- No new external dependencies
- No changes to file naming logic
- No changes to the yt-dlp download/extraction behavior

## Decisions

**Decision 1: Extract title during worker processing, not at creation time**
- **Why**: The worker already runs yt-dlp and has access to the full metadata dict including title. Pre-extracting at creation time would require a separate yt-dlp invocation on the API server, adding latency and a new failure path.
- **Alternative considered**: Lightweight `--print title` call at creation in the API route. Rejected because it adds API latency, couples the API to yt-dlp, and the title will be available moments later when the worker finishes.

**Decision 2: Store title as a nullable column on `DownloadJob`**
- **Why**: Simplest persistence model. The title is optional (not available until the worker runs), so nullable is correct. No need for a separate table since it's 1:1 with the job.
- **Alternative considered**: Derive title from `file_name` at display time. Rejected because `file_name` is sanitized (underscores replace spaces) and is not guaranteed to contain the original title.

**Decision 3: Return title from `extract_media` as part of the result tuple**
- **Why**: Minimum disruption to the existing service interface. Adds a third return value `(file_path, file_name, title)` rather than a breaking signature change.
- **Alternative considered**: Store title in a separate lookup. Rejected as over-engineered for a single string field.

**Decision 4: Show URL as fallback when title is None**
- **Why**: Jobs in `pending` status won't have a title yet. The URL is the most meaningful fallback — it's the only identifier available. For `failed` jobs, the URL allows the user to re-submit.

## Risks / Trade-offs

- **Title not available until job completes**: Pending/processing jobs will show the URL. This is acceptable since processing is usually fast (seconds for yt-dlp metadata-only extraction).
- **Title could be empty string or non-descriptive**: yt-dlp may return empty or generic titles. The fallback logic handles this by checking both `None` and empty string.
- **DB migration required**: The new column requires an Alembic migration. Existing jobs will have `NULL` title, which is handled by the URL fallback.
