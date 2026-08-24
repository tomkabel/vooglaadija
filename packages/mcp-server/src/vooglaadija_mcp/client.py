"""Stdlib-only REST client for the Vooglaadija API (MCP server transport).

Uses only the Python standard library so the MCP server has zero third-party
runtime dependencies. All failures are normalized into :class:`ApiErrorEnvelope`
so agents can decide whether to retry.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .errors import ApiErrorEnvelope, VooglaadijaApiError, map_http_error, map_network_error


class VooglaadijaClient:
    """Thin, deterministic client over the Vooglaadija REST API."""

    def __init__(
        self,
        api_base_url: str,
        api_key: str,
        *,
        timeout: float = 30.0,
        user_agent: str = "vooglaadija-mcp/1.0.0",
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.user_agent = user_agent

    def _request(self, method: str, path: str, json_body: Any | None = None) -> tuple[int, Any]:
        url = f"{self.api_base_url}{path}"
        data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.api_key}")
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", self.user_agent)
        if data is not None:
            request.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                return response.status, _maybe_json(raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            return exc.code, _maybe_json(raw)
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            raise VooglaadijaApiError(map_network_error(str(reason))) from exc

    def _ok(self, method: str, path: str, json_body: Any | None = None) -> Any:
        status, payload = self._request(method, path, json_body)
        if 200 <= status < 300:
            return payload
        raise VooglaadijaApiError(map_http_error(status, payload))

    # ── Domain operations ──────────────────────────────────────────────────

    def create_download(self, url: str) -> Any:
        return self._ok("POST", "/api/v1/downloads", {"url": url})

    def get_download(self, job_id: str) -> Any:
        return self._ok("GET", f"/api/v1/downloads/{job_id}")

    def list_downloads(self, page: int = 1, per_page: int = 20) -> Any:
        return self._ok("GET", f"/api/v1/downloads?page={page}&per_page={per_page}")

    def retry_download(self, job_id: str) -> Any:
        return self._ok("POST", f"/api/v1/downloads/{job_id}/retry")

    def delete_download(self, job_id: str) -> None:
        self._ok("DELETE", f"/api/v1/downloads/{job_id}")

    def health(self) -> Any:
        # Health is unauthenticated; fall back gracefully if no key is set.
        try:
            return self._ok("GET", "/api/v1/health")
        except VooglaadijaApiError:
            raise


def _maybe_json(raw: str) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {"raw": raw}
