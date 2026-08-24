"""Persistent yt-dlp worker subprocess.

This script is launched once per extraction concurrency slot by
``app.services.yt_dlp_service.YtDlpProcessPool``. It imports ``yt_dlp`` a
single time (lazily, on the first request) and then serves extraction
requests over stdin/stdout using newline-delimited JSON:

    request  (parent -> worker, one JSON object per line):
        {
            "url": str,
            "output_template": str,
            "platform": str,
            "fallback_chain": list[dict],
            "extractor_args": dict,
            "cookies_opts": dict,
            "output_fields": list[str],
            "youtube_opts": bool,
        }

    response (worker -> parent, one JSON object per line):
        progress line:  {"progress": true, "percent": float, ...}
        terminal line:  {<output_fields...>, "_stderr": str}   (success)
                        or {"error": str, "_stderr": str}      (failure)

The parent reads responses until it sees the first non-progress (terminal)
line for a request. Importing ``yt_dlp`` at request time (rather than module
import time) keeps this module importable in environments where ``yt_dlp`` is
not installed (e.g. unit-test collection); Python caches the import after the
first request so each worker slot still imports ``yt_dlp`` only once.
"""

import io
import json
import sys


def make_ydl_opts(req: dict, format_spec: dict) -> dict:
    """Build a single yt-dlp options dict for one format fallback entry.

    This is a pure function (no ``yt_dlp`` import) so it can be unit-tested in
    isolation and mirrors the option shape previously embedded in the
    per-call extraction subprocess script.
    """
    ydl_opts: dict = {
        "format": format_spec["format"],
        "format_sort": format_spec.get("format_sort", []),
        "outtmpl": req["output_template"],
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 60,
        "retries": 3,
    }
    if req.get("youtube_opts"):
        ydl_opts["prefer_free_formats"] = True
        ydl_opts["check_formats"] = "missable"

    cookies_opts = dict(req.get("cookies_opts") or {})
    if "cookiesfrombrowser" in cookies_opts and isinstance(
        cookies_opts.get("cookiesfrombrowser"), list
    ):
        cookies_opts["cookiesfrombrowser"] = tuple(cookies_opts["cookiesfrombrowser"])
    ydl_opts.update(cookies_opts)

    extractor_args = req.get("extractor_args") or {}
    if extractor_args:
        ydl_opts["extractor_args"] = extractor_args

    return ydl_opts


def _tail(buffer: io.StringIO, max_bytes: int = 4096) -> str:
    """Return the trailing ``max_bytes`` of a captured stderr buffer."""
    text = buffer.getvalue()
    if len(text) > max_bytes:
        text = text[-max_bytes:]
    return text


def run_request(req: dict) -> None:
    """Process a single extraction request and write the terminal response line.

    All exceptions are caught and reported back to the parent as a JSON error
    line so the worker event loop never dies.
    """
    import yt_dlp

    output_fields = req.get("output_fields") or [
        "title",
        "ext",
        "duration",
        "webpage_url",
        "thumbnail",
    ]
    fallback_chain = req.get("fallback_chain") or [
        {"format": "best", "format_sort": ["quality"]}
    ]

    last_error: str | None = None
    last_progress_pct = -1.0
    stderr_buf = io.StringIO()
    old_stderr = sys.stderr
    # Capture Python-level stderr (yt-dlp warnings/errors) so the parent can
    # still run throttle/error detection without a per-request subprocess pipe.
    sys.stderr = stderr_buf

    try:
        for format_spec in fallback_chain:
            last_progress_pct = -1.0

            def _progress_hook(d: dict) -> None:
                nonlocal last_progress_pct
                if d.get("status") == "downloading":
                    downloaded = d.get("downloaded_bytes", 0)
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    pct = (downloaded / total * 100) if total > 0 else 0
                    if pct - last_progress_pct < 0.5:
                        return
                    last_progress_pct = pct
                    print(
                        json.dumps(
                            {
                                "progress": True,
                                "percent": round(pct, 1),
                                "downloaded_bytes": downloaded,
                                "total_bytes": total,
                                "speed": d.get("speed"),
                                "eta": d.get("eta"),
                            }
                        ),
                        flush=True,
                    )

            ydl_opts = make_ydl_opts(req, format_spec)
            ydl_opts["progress_hooks"] = [_progress_hook]

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(req["url"], download=True)
                    if info is None:
                        last_error = "No video info returned"
                        continue
                    sanitized_info = ydl.sanitize_info(info)
                    filtered = {
                        k: sanitized_info.get(k) for k in output_fields if k in sanitized_info
                    }
                    filtered["_stderr"] = _tail(stderr_buf)
                    print(json.dumps(filtered), flush=True)
                    return
            except Exception as exc:  # noqa: BLE001 - surface to parent as JSON error
                err_str = str(exc)
                if "Requested format" in err_str and "not available" in err_str:
                    last_error = err_str
                    continue
                print(
                    json.dumps({"error": err_str, "_stderr": _tail(stderr_buf)}),
                    flush=True,
                )
                return
    except Exception as exc:  # noqa: BLE001 - worker must never crash the loop
        print(
            json.dumps({"error": f"worker fatal: {exc}", "_stderr": _tail(stderr_buf)}),
            flush=True,
        )
        return
    finally:
        sys.stderr = old_stderr

    attempted_formats = [spec["format"] for spec in fallback_chain]
    platform = req.get("platform", "unknown")
    message = (
        f"[{platform}] All formats failed. "
        f"Last error: {last_error}. Attempted formats: {attempted_formats}"
    )
    print(json.dumps({"error": message, "_stderr": _tail(stderr_buf)}), flush=True)


def main() -> None:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(req, dict):
            continue
        try:
            run_request(req)
        except Exception:  # noqa: BLE001 - never let the request loop die
            print(json.dumps({"error": "worker request handler crashed"}), flush=True)


if __name__ == "__main__":
    main()
