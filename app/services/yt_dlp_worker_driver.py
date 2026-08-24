"""Long-lived yt-dlp worker driver for the warm subprocess pool.

This process is started ONCE per pool slot by ``YtDlpProcessPool`` and imports
``yt_dlp`` a single time. It then reads newline-delimited JSON job requests from
stdin and writes newline-delimited JSON responses to stdout:

Request (one line):
    {"job_id": "uuid", "mode": "extract" | "metadata", "url": "...",
     "output_template": "...", "platform": "youtube", "fallback_chain": [...],
     "extractor_args": {...}, "cookies_opts": {...}, "youtube_opts": bool}

Response lines (may be several per job):
    {"job_id": "uuid", "progress": true, "percent": 12.0, ...}   # progress hook
    {"job_id": "uuid", "result": {...}}                           # success
    {"job_id": "uuid", "error": "..."}                           # failure

On startup the driver prints ``{"_worker_ready": true}`` so the pool can confirm
the process imported yt_dlp successfully before handing it real jobs.

Keeping this as a standalone script (rather than inline ``python -c``) is the
whole point: ``import yt_dlp`` happens once per process instead of once per job,
eliminating the per-job cold start that dominates short downloads.

The driver intentionally owns no platform-specific logic — the parent
``yt_dlp_service`` computes ``fallback_chain`` / ``extractor_args`` / etc. and
ships them in the request, so behaviour stays identical to the inline path.
"""

import json
import sys

import yt_dlp

# Mirror of app.services.yt_dlp_service._OUTPUT_FIELDS — the fields forwarded
# from the sanitized_info dict back to the caller.
_OUTPUT_FIELDS = frozenset(
    {"title", "ext", "duration", "webpage_url", "thumbnail"}
)


def _run_extract(job: dict, progress_hook=None) -> dict | None:
    """Execute an extract job; return the result dict or raise.

    Returns the filtered sanitized_info dict on success, or None if every
    format in the fallback chain produced no info (caller records last_error).
    """
    url = job["url"]
    output_template = job["output_template"]
    fallback_chain = job.get("fallback_chain") or [{"format": "best"}]
    extractor_args = job.get("extractor_args") or {}
    cookies_opts = job.get("cookies_opts") or {}
    youtube_opts = bool(job.get("youtube_opts"))

    if "cookiesfrombrowser" in cookies_opts and isinstance(
        cookies_opts["cookiesfrombrowser"], list
    ):
        cookies_opts["cookiesfrombrowser"] = tuple(cookies_opts["cookiesfrombrowser"])

    last_error = None
    for format_spec in fallback_chain:
        ydl_opts = {
            "format": format_spec["format"],
            "format_sort": format_spec.get("format_sort", []),
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "socket_timeout": 60,
            "retries": 3,
        }
        if youtube_opts:
            ydl_opts["prefer_free_formats"] = True
            ydl_opts["check_formats"] = "missable"
        if progress_hook is not None:
            ydl_opts["progress_hooks"] = [progress_hook]
        ydl_opts.update(cookies_opts)
        if extractor_args:
            ydl_opts["extractor_args"] = extractor_args

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    last_error = "No video info returned"
                    continue
                sanitized = ydl.sanitize_info(info)
                return {
                    k: sanitized.get(k) for k in _OUTPUT_FIELDS if k in sanitized
                }
        except Exception as exc:
            err_str = str(exc)
            if "Requested format" in err_str and "not available" in err_str:
                last_error = err_str
                continue
            raise

    attempted = [spec["format"] for spec in fallback_chain]
    raise RuntimeError(
        f"[{job.get('platform', 'unknown')}] All formats failed. "
        f"Last error: {last_error}. Attempted formats: {attempted}"
    )


def _run_metadata(job: dict) -> dict | None:
    """Resolve a title without downloading; return {"title": ...} or raise."""
    url = job["url"]
    cookies_opts = job.get("cookies_opts") or {}
    if "cookiesfrombrowser" in cookies_opts and isinstance(
        cookies_opts["cookiesfrombrowser"], list
    ):
        cookies_opts["cookiesfrombrowser"] = tuple(cookies_opts["cookiesfrombrowser"])

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 10,
        "retries": 1,
    }
    ydl_opts.update(cookies_opts)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        sanitized = ydl.sanitize_info(info)
        title = sanitized.get("title")
        if not title:
            raise RuntimeError("no_title")
        return {"title": title}


def _progress_printer(job_id: str):
    """Return a yt-dlp progress hook that forwards JSON lines to stdout."""

    def _hook(d: dict) -> None:
        if d.get("status") != "downloading":
            return
        downloaded = d.get("downloaded_bytes", 0)
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        pct = (downloaded / total * 100) if total > 0 else 0
        _emit(
            {
                "job_id": job_id,
                "progress": True,
                "percent": round(pct, 1),
                "downloaded_bytes": downloaded,
                "total_bytes": total,
                "speed": d.get("speed"),
                "eta": d.get("eta"),
            }
        )

    return _hook


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> int:
    # Signal the pool that yt_dlp imported successfully.
    _emit({"_worker_ready": True})

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            job = json.loads(line)
        except json.JSONDecodeError:
            _emit({"error": "invalid_job_json"})
            continue

        job_id = job.get("job_id", "")
        mode = job.get("mode", "extract")
        try:
            if mode == "metadata":
                result = _run_metadata(job)
                _emit({"job_id": job_id, "result": result})
            else:
                result = _run_extract(job, progress_hook=_progress_printer(job_id))
                _emit({"job_id": job_id, "result": result})
        except Exception as exc:
            _emit({"job_id": job_id, "error": str(exc)})

    # stdin closed: clean shutdown.
    return 0


if __name__ == "__main__":
    sys.exit(main())
