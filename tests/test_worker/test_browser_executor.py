"""Tests for worker.browser_executor module.

Covers the I/O matrix from spec-gh-140-p2-worker-integration.md:
- success path returns (file_path, file_name, None)
- error code → ErrorCategory mapping for every documented microservice code
- HTTP transport failures map to TRANSIENT/TIMEOUT
- circuit breaker integration (open circuit → TRANSIENT, no HTTP call)
- no httpx exception leaks past extract_media
"""

from __future__ import annotations

import httpx
import pytest

from app.services.circuit_breaker import CircuitState
from app.services.error_classifier import ErrorCategory
from worker.browser_executor import (
    BrowserExecutorError,
    _map_response_to_category,
    extract_media,
    get_browser_downloader_circuit_breaker,
    select_executor,
)


@pytest.fixture(autouse=True)
def _reset_breaker_state():
    """Reset the module-level circuit breaker between tests.

    The breaker is a singleton; without this fixture a test that opens the
    breaker (e.g. via `record_failure` loops) leaks state into subsequent
    tests and triggers spurious `circuit_open` errors.
    """
    breaker = get_browser_downloader_circuit_breaker()
    breaker._state = CircuitState.CLOSED
    breaker._failure_count = 0
    breaker._success_count = 0
    breaker._last_failure_time = None
    breaker._half_open_calls = 0
    yield
    breaker._state = CircuitState.CLOSED
    breaker._failure_count = 0
    breaker._success_count = 0
    breaker._last_failure_time = None
    breaker._half_open_calls = 0


# -- select_executor: hostname routing -----------------------------------


class TestSelectExecutor:
    """Hostname-based dispatch — pure function, no settings touch."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.tiktok.com/@user/video/123",
            "https://tiktok.com/@u/v/1",
            "https://m.tiktok.com/v/1.html",
            "https://tiktokv.com/share/video/1",
            "https://vm.tiktok.com/abcdef",
            "https://www.instagram.com/reel/abc",
            "https://instagram.com/p/xyz",
            "https://instagr.am/p/abc",
            "https://twitter.com/user/status/1",
            "https://x.com/user/status/1",
            "https://t.co/abc",
        ],
    )
    def test_browser_platforms_route_to_browser(self, url: str) -> None:
        assert select_executor(url) == "browser"

    @pytest.mark.unit
    def test_fqdn_trailing_dot_routes_to_browser(self) -> None:
        # Some DNS resolvers return FQDN form (with trailing dot).
        # We must still match.
        assert select_executor("https://www.tiktok.com./@u/v/1") == "browser"
        assert select_executor("https://instagram.com./p/x") == "browser"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=abc",
            "https://youtu.be/abc",
            "https://example.com/foo",
            "https://vimeo.com/123",
            "",
        ],
    )
    def test_non_browser_platforms_route_to_youtube(self, url: str) -> None:
        assert select_executor(url) == "youtube"

    @pytest.mark.unit
    def test_unparseable_url_falls_through_to_youtube(self) -> None:
        # Malformed URL is treated as unknown → yt-dlp (current behavior)
        assert select_executor("not a url at all") == "youtube"


# -- _map_response_to_category: error code → ErrorCategory ---------------


class TestMapResponseToCategory:
    """Single source of truth for microservice error codes."""

    @pytest.mark.unit
    def test_drm_detected_is_blocked(self) -> None:
        assert _map_response_to_category("drm_detected") == ErrorCategory.BLOCKED

    @pytest.mark.unit
    def test_anti_bot_block_is_blocked(self) -> None:
        assert _map_response_to_category("anti_bot_block") == ErrorCategory.BLOCKED

    @pytest.mark.unit
    def test_no_media_found_is_not_found(self) -> None:
        assert _map_response_to_category("no_media_found") == ErrorCategory.NOT_FOUND

    @pytest.mark.unit
    def test_network_error_is_transient(self) -> None:
        assert _map_response_to_category("network_error") == ErrorCategory.TRANSIENT

    @pytest.mark.unit
    def test_timeout_is_timeout(self) -> None:
        assert _map_response_to_category("request_timeout") == ErrorCategory.TIMEOUT

    @pytest.mark.unit
    def test_http_5xx_is_transient(self) -> None:
        assert _map_response_to_category("http_503") == ErrorCategory.TRANSIENT

    @pytest.mark.unit
    def test_http_4xx_unknown_is_blocked(self) -> None:
        assert _map_response_to_category("http_400") == ErrorCategory.BLOCKED

    @pytest.mark.unit
    def test_invalid_request_is_blocked(self) -> None:
        assert _map_response_to_category("invalid_request") == ErrorCategory.BLOCKED

    @pytest.mark.unit
    def test_http_429_rate_limit_is_transient(self) -> None:
        # 429 from the microservice (in the error code) is rate limiting,
        # not a platform-level block. Retries should kick in.
        assert _map_response_to_category("http_429") == ErrorCategory.TRANSIENT

    @pytest.mark.unit
    def test_unknown_code_defaults_to_transient(self) -> None:
        assert _map_response_to_category("something_new") == ErrorCategory.TRANSIENT


# -- extract_media: HTTP path --------------------------------------------


def _make_mock_client(response_status: int, body: dict | str) -> httpx.AsyncClient:
    """Build an httpx.AsyncClient whose single POST returns the given response.

    Uses httpx.MockTransport (httpx's built-in test utility — no external
    mocking library required).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(body, str):
            return httpx.Response(response_status, text=body)
        return httpx.Response(response_status, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestExtractMediaSuccess:
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_success_returns_tuple_with_filename_derived_from_path(self) -> None:
        client = _make_mock_client(
            200,
            {"status": "success", "file_path": "/storage/abc-123.mp4", "tier_used": 1},
        )
        result = await extract_media(
            "https://tiktok.com/@u/v/1",
            "/storage",
            client=client,
        )
        assert result == ("/storage/abc-123.mp4", "abc-123.mp4", None)


class TestExtractMediaErrorCodes:
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_drm_detected_maps_to_blocked(self) -> None:
        client = _make_mock_client(
            502,
            {"status": "failed", "error": "drm_detected"},
        )
        with pytest.raises(BrowserExecutorError) as exc:
            await extract_media("https://tiktok.com/@u/v/1", "/storage", client=client)
        assert exc.value.category == ErrorCategory.BLOCKED
        assert exc.value.signal == "drm_detected"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_anti_bot_block_maps_to_blocked(self) -> None:
        client = _make_mock_client(
            502,
            {"status": "failed", "error": "anti_bot_block"},
        )
        with pytest.raises(BrowserExecutorError) as exc:
            await extract_media("https://instagram.com/p/x", "/storage", client=client)
        assert exc.value.category == ErrorCategory.BLOCKED

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_no_media_found_maps_to_not_found(self) -> None:
        client = _make_mock_client(
            502,
            {"status": "failed", "error": "no_media_found"},
        )
        with pytest.raises(BrowserExecutorError) as exc:
            await extract_media("https://tiktok.com/@u/v/1", "/storage", client=client)
        assert exc.value.category == ErrorCategory.NOT_FOUND

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_http_5xx_with_json_error_maps_to_transient(self) -> None:
        client = _make_mock_client(
            503,
            {"status": "failed", "error": "concurrency_limit"},
        )
        with pytest.raises(BrowserExecutorError) as exc:
            await extract_media("https://tiktok.com/@u/v/1", "/storage", client=client)
        # 'concurrency_limit' is not a known code → falls through to TRANSIENT
        assert exc.value.category == ErrorCategory.TRANSIENT
        assert exc.value.signal == "concurrency_limit"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_http_503_empty_body_maps_to_transient_with_status_signal(self) -> None:
        """AC4: 503 with an empty body should still classify as TRANSIENT
        with the synthetic http_<status> signal — covers the case where
        the microservice is overloaded and closes the response without
        writing JSON.
        """
        client = _make_mock_client(503, "")
        with pytest.raises(BrowserExecutorError) as exc:
            await extract_media("https://tiktok.com/@u/v/1", "/storage", client=client)
        assert exc.value.category == ErrorCategory.TRANSIENT
        assert exc.value.signal == "http_503"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_non_json_error_body_maps_to_transient(self) -> None:
        client = _make_mock_client(502, "internal server error")
        with pytest.raises(BrowserExecutorError) as exc:
            await extract_media("https://tiktok.com/@u/v/1", "/storage", client=client)
        assert exc.value.category == ErrorCategory.TRANSIENT
        assert exc.value.signal == "http_502"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_http_400_maps_to_blocked(self) -> None:
        client = _make_mock_client(
            400,
            {"status": "failed", "error": "invalid_request", "message": "bad url"},
        )
        with pytest.raises(BrowserExecutorError) as exc:
            await extract_media("https://tiktok.com/@u/v/1", "/storage", client=client)
        assert exc.value.category == ErrorCategory.BLOCKED

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_200_with_missing_file_path_maps_to_transient(self) -> None:
        client = _make_mock_client(200, {"status": "success"})
        with pytest.raises(BrowserExecutorError) as exc:
            await extract_media("https://tiktok.com/@u/v/1", "/storage", client=client)
        assert exc.value.category == ErrorCategory.TRANSIENT
        assert exc.value.signal == "missing_file_path"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_200_with_non_dict_json_body_maps_to_transient(self) -> None:
        """Microservice contract violation: 200 OK with a JSON list/null/scalar body."""
        client = _make_mock_client(200, [1, 2, 3])
        with pytest.raises(BrowserExecutorError) as exc:
            await extract_media("https://tiktok.com/@u/v/1", "/storage", client=client)
        assert exc.value.category == ErrorCategory.TRANSIENT
        assert exc.value.signal == "invalid_response_shape"


class TestExtractMediaCircuitBreaker:
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_open_circuit_raises_transient_without_http_call(self) -> None:
        # When the breaker is OPEN, no HTTP call should be made. We use a
        # transport that would raise if invoked, proving the call was skipped.
        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200, json={"status": "success", "file_path": "/x.mp4"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        # Force the breaker open by recording 5 consecutive failures
        breaker = get_browser_downloader_circuit_breaker()
        for _ in range(breaker.failure_threshold):
            await breaker.record_failure(RuntimeError("boom"))

        with pytest.raises(BrowserExecutorError) as exc:
            await extract_media(
                "https://tiktok.com/@u/v/1",
                "/storage",
                client=client,
            )
        assert exc.value.category == ErrorCategory.TRANSIENT
        assert exc.value.signal == "circuit_open"
        assert called is False, "HTTP transport was called despite open breaker"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_no_httpx_exception_leaks_past_extract_media(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadError("stream broke")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with pytest.raises(BrowserExecutorError):
            await extract_media("https://tiktok.com/@u/v/1", "/storage", client=client)
        # The exception must be BrowserExecutorError, not httpx.HTTPError
        # (defensive — the type annotation in extract_media's docstring).


# -- Circuit breaker singleton -------------------------------------------


class TestBreakerSingleton:
    @pytest.mark.unit
    def test_get_breaker_returns_same_instance(self) -> None:
        a = get_browser_downloader_circuit_breaker()
        b = get_browser_downloader_circuit_breaker()
        assert a is b
        assert a.name == "browser_downloader"
