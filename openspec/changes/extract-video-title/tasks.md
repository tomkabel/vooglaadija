## 1. Database Migration

- [x] 1.1 Generate Alembic migration to add nullable `title` column (`String(255)`) to `download_jobs` table

## 2. Model Update

- [x] 2.1 Add `title: Mapped[str | None]` field to `DownloadJob` model in `app/models/download_job.py`

## 3. Schema Update

- [x] 3.1 Add optional `title: str | None = None` field to `DownloadResponse` in `app/schemas/download.py`

## 4. yt-dlp Service Update

- [x] 4.1 Update `extract_media_url` in `app/services/yt_dlp_service.py` to return `(file_path, file_name, title)` tuple, extracting title from `info.get("title")` before sanitization

## 5. Circuit Breaker Wrapper Update

- [x] 5.1 Update `extract_media_with_circuit_breaker` to accept and return the new `title` value from the service

## 6. Worker Update

- [x] 6.1 In `worker/processor.py`, unpack `file_path, file_name, title` from `extract_media_with_circuit_breaker` result
- [x] 6.2 Store `title=title` in the `update(DownloadJob)` values dict on job completion

## 7. SSE Event Payload Update

- [x] 7.1 Add `"title": job.title` to `_job_to_sse_data` in `app/api/routes/sse.py`
- [x] 7.2 Add `"title": job.title` to the SSE publish payload in `web.py` (both creation and delete handlers)
- [x] 7.3 Add `"title": job.title` to `_publish_job_status` in `worker/processor.py`

## 8. Template Update — Server-Rendered Rows

- [x] 8.1 In `_download_list.html`, replace `{{ job.url }}` with `{{ job.title or job.url }}`
- [x] 8.2 In `_download_item.html`, replace `{{ job.url }}` with `{{ job.title or job.url }}`

## 9. Template Update — Client-Side JS (SSE Row Builder)

- [x] 9.1 In `dashboard.html`, update `getRowHTML()` to display `data.title || data.url` instead of `data.url`

## 10. Verify

- [ ] 10.1 Run existing test suite to confirm no regressions (skipped by user)
- [ ] 10.2 Verify that jobs without a title (existing rows) still render the URL as fallback (verified via code — `{{ job.title or job.url }}` and `data.title || data.url` fallback patterns in place)
