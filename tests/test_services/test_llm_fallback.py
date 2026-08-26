"""Tests for the LLM fallback extraction service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.llm_fallback import (
    LLMFallbackError,
    LLMFallbackResult,
    _sanitize_html,
    _validate_discovered_url,
    extract_with_llm_fallback,
    is_llm_fallback_available,
)


class TestSanitizeHtml:
    """Tests for HTML sanitization."""

    def test_removes_script_tags(self) -> None:
        html = '<html><script>alert("xss")</script><body>Hello</body></html>'
        result = _sanitize_html(html)
        assert "<script>" not in result
        assert "alert" not in result
        assert "Hello" in result

    def test_removes_style_tags(self) -> None:
        html = '<html><style>body{color:red}</style><body>Content</body></html>'
        result = _sanitize_html(html)
        assert "<style>" not in result
        assert "color" not in result
        assert "Content" in result

    def test_removes_html_comments(self) -> None:
        html = "<html><!-- comment --><body>Text</body></html>"
        result = _sanitize_html(html)
        assert "comment" not in result
        assert "Text" in result

    def test_collapses_whitespace(self) -> None:
        html = "<html>  <body>   Hello    World   </body>  </html>"
        result = _sanitize_html(html)
        assert "  " not in result
        assert "Hello World" in result

    def test_truncates_long_html(self) -> None:
        html = "<html>" + "x" * 20000 + "</html>"
        result = _sanitize_html(html)
        assert len(result) <= 15000

    def test_removes_html_tags_but_keeps_text(self) -> None:
        html = "<html><body><p>Paragraph</p><div>Div text</div></body></html>"
        result = _sanitize_html(html)
        assert "<p>" not in result
        assert "Paragraph" in result
        assert "Div text" in result


class TestValidateDiscoveredUrl:
    """Tests for URL validation."""

    def test_valid_https_url(self) -> None:
        assert _validate_discovered_url("https://example.com/video.mp4") is True

    def test_valid_http_url(self) -> None:
        assert _validate_discovered_url("http://cdn.example.com/stream.m3u8") is True

    def test_rejects_ftp_scheme(self) -> None:
        assert _validate_discovered_url("ftp://example.com/video.mp4") is False

    def test_rejects_localhost(self) -> None:
        assert _validate_discovered_url("http://localhost/video.mp4") is False

    def test_rejects_127_0_0_1(self) -> None:
        assert _validate_discovered_url("http://127.0.0.1/video.mp4") is False

    def test_rejects_10_x(self) -> None:
        assert _validate_discovered_url("http://10.0.0.1/video.mp4") is False

    def test_rejects_192_168_x(self) -> None:
        assert _validate_discovered_url("http://192.168.1.1/video.mp4") is False

    def test_rejects_empty_hostname(self) -> None:
        assert _validate_discovered_url("http:///video.mp4") is False

    def test_rejects_malformed_url(self) -> None:
        assert _validate_discovered_url("not a url") is False


class TestLLMFallbackResult:
    """Tests for LLMFallbackResult."""

    def test_found_property_true(self) -> None:
        result = LLMFallbackResult(url="https://example.com/v.mp4", format="mp4")
        assert result.found is True

    def test_found_property_false_when_none(self) -> None:
        result = LLMFallbackResult(url=None, format="none")
        assert result.found is False

    def test_found_property_false_when_format_none(self) -> None:
        result = LLMFallbackResult(url="https://example.com/v.mp4", format="none")
        assert result.found is False


class TestIsLlmFallbackAvailable:
    """Tests for availability check."""

    def test_not_available_when_disabled(self) -> None:
        with patch("app.services.llm_fallback.settings") as mock_settings:
            mock_settings.llm_fallback_enabled = False
            mock_settings.llm_fallback_api_key = "test-key"
            assert is_llm_fallback_available() is False

    def test_not_available_when_no_api_key(self) -> None:
        with patch("app.services.llm_fallback.settings") as mock_settings:
            mock_settings.llm_fallback_enabled = True
            mock_settings.llm_fallback_api_key = ""
            assert is_llm_fallback_available() is False

    def test_available_when_enabled_with_key(self) -> None:
        with patch("app.services.llm_fallback.settings") as mock_settings:
            mock_settings.llm_fallback_enabled = True
            mock_settings.llm_fallback_api_key = "test-key"
            assert is_llm_fallback_available() is True


class TestExtractWithLlmFallback:
    """Tests for the main extraction function."""

    @pytest.mark.asyncio
    async def test_returns_not_found_when_disabled(self) -> None:
        with patch("app.services.llm_fallback.settings") as mock_settings:
            mock_settings.llm_fallback_enabled = False
            result = await extract_with_llm_fallback("https://example.com/video")
            assert result.found is False
            assert result.format == "none"

    @pytest.mark.asyncio
    async def test_returns_not_found_for_invalid_url(self) -> None:
        with patch("app.services.llm_fallback.settings") as mock_settings:
            mock_settings.llm_fallback_enabled = True
            result = await extract_with_llm_fallback("http://localhost/video")
            assert result.found is False

    @pytest.mark.asyncio
    async def test_returns_not_found_when_fetch_fails(self) -> None:
        with (
            patch("app.services.llm_fallback.settings") as mock_settings,
            patch("app.services.llm_fallback._fetch_page_html", return_value=""),
        ):
            mock_settings.llm_fallback_enabled = True
            result = await extract_with_llm_fallback("https://example.com/video")
            assert result.found is False

    @pytest.mark.asyncio
    async def test_returns_not_found_when_llm_returns_none(self) -> None:
        mock_response = {
            "choices": [
                {
                    "message": {
                        "content": '{"url": null, "format": "none", "title": null}'
                    }
                }
            ]
        }
        mock_client = AsyncMock()
        mock_client.post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value=mock_response),
            raise_for_status=MagicMock(),
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("app.services.llm_fallback.settings") as mock_settings,
            patch(
                "app.services.llm_fallback._fetch_page_html",
                return_value="<html>video page</html>",
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            mock_settings.llm_fallback_enabled = True
            mock_settings.llm_fallback_api_key = "test-key"
            mock_settings.llm_fallback_api_base = "https://api.example.com/v1"
            mock_settings.llm_fallback_model = "test-model"
            mock_settings.llm_fallback_referer = ""

            result = await extract_with_llm_fallback("https://example.com/video")
            assert result.found is False

    @pytest.mark.asyncio
    async def test_returns_url_when_llm_finds_one(self) -> None:
        mock_response = {
            "choices": [
                {
                    "message": {
                        "content": '{"url": "https://cdn.example.com/video.mp4", "format": "mp4", "title": "Test Video"}'
                    }
                }
            ]
        }
        mock_client = AsyncMock()
        mock_client.post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value=mock_response),
            raise_for_status=MagicMock(),
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("app.services.llm_fallback.settings") as mock_settings,
            patch(
                "app.services.llm_fallback._fetch_page_html",
                return_value="<html>video page</html>",
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch(
                "app.services.llm_fallback.validate_url_not_ssrf",
                return_value=True,
            ),
        ):
            mock_settings.llm_fallback_enabled = True
            mock_settings.llm_fallback_api_key = "test-key"
            mock_settings.llm_fallback_api_base = "https://api.example.com/v1"
            mock_settings.llm_fallback_model = "test-model"
            mock_settings.llm_fallback_referer = ""

            result = await extract_with_llm_fallback("https://example.com/video")
            assert result.found is True
            assert result.url == "https://cdn.example.com/video.mp4"
            assert result.format == "mp4"
            assert result.title == "Test Video"

    @pytest.mark.asyncio
    async def test_handles_markdown_code_block_response(self) -> None:
        mock_response = {
            "choices": [
                {
                    "message": {
                        "content": '```json\n{"url": "https://cdn.example.com/v.mp4", "format": "mp4", "title": null}\n```'
                    }
                }
            ]
        }
        mock_client = AsyncMock()
        mock_client.post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value=mock_response),
            raise_for_status=MagicMock(),
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("app.services.llm_fallback.settings") as mock_settings,
            patch(
                "app.services.llm_fallback._fetch_page_html",
                return_value="<html>video page</html>",
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch(
                "app.services.llm_fallback.validate_url_not_ssrf",
                return_value=True,
            ),
        ):
            mock_settings.llm_fallback_enabled = True
            mock_settings.llm_fallback_api_key = "test-key"
            mock_settings.llm_fallback_api_base = "https://api.example.com/v1"
            mock_settings.llm_fallback_model = "test-model"
            mock_settings.llm_fallback_referer = ""

            result = await extract_with_llm_fallback("https://example.com/video")
            assert result.found is True
            assert result.url == "https://cdn.example.com/v.mp4"

    @pytest.mark.asyncio
    async def test_returns_not_found_when_llm_api_fails(self) -> None:
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.HTTPError("Connection failed")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("app.services.llm_fallback.settings") as mock_settings,
            patch(
                "app.services.llm_fallback._fetch_page_html",
                return_value="<html>video page</html>",
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            mock_settings.llm_fallback_enabled = True
            mock_settings.llm_fallback_api_key = "test-key"
            mock_settings.llm_fallback_api_base = "https://api.example.com/v1"
            mock_settings.llm_fallback_model = "test-model"
            mock_settings.llm_fallback_referer = ""

            result = await extract_with_llm_fallback("https://example.com/video")
            assert result.found is False

    @pytest.mark.asyncio
    async def test_rejects_ssrf_url_from_llm(self) -> None:
        mock_response = {
            "choices": [
                {
                    "message": {
                        "content": '{"url": "http://10.0.0.1/internal.mp4", "format": "mp4", "title": null}'
                    }
                }
            ]
        }
        mock_client = AsyncMock()
        mock_client.post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value=mock_response),
            raise_for_status=MagicMock(),
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("app.services.llm_fallback.settings") as mock_settings,
            patch(
                "app.services.llm_fallback._fetch_page_html",
                return_value="<html>video page</html>",
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            mock_settings.llm_fallback_enabled = True
            mock_settings.llm_fallback_api_key = "test-key"
            mock_settings.llm_fallback_api_base = "https://api.example.com/v1"
            mock_settings.llm_fallback_model = "test-model"
            mock_settings.llm_fallback_referer = ""

            result = await extract_with_llm_fallback("https://example.com/video")
            assert result.found is False
