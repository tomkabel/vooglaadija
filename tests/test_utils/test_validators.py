"""Tests for multi-platform URL validation with subdomain bypass prevention."""

from app.utils.validators import is_supported_url, is_youtube_url


class TestIsSupportedUrl:
    """Test the is_supported_url validator."""

    def test_valid_youtube_urls(self):
        valid = [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com/watch?v=dQw4w9WgXcQ",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
            "https://youtube-nocookie.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube-nocookie.com/watch?v=dQw4w9WgXcQ",
            "http://www.youtube.com/watch?v=dQw4w9WgXcQ",
        ]
        for url in valid:
            assert is_supported_url(url), f"Expected valid: {url}"

    def test_valid_vimeo_urls(self):
        valid = [
            "https://vimeo.com/123456789",
            "https://www.vimeo.com/video/abc",
            "http://vimeo.com/video/abc",
        ]
        for url in valid:
            assert is_supported_url(url), f"Expected valid: {url}"

    def test_valid_dailymotion_urls(self):
        valid = [
            "https://www.dailymotion.com/video/abc123",
            "https://dailymotion.com/video/abc123",
            "http://www.dailymotion.com/video/abc123",
        ]
        for url in valid:
            assert is_supported_url(url), f"Expected valid: {url}"

    def test_valid_twitch_urls(self):
        valid = [
            "https://www.twitch.tv/videos/123456789",
            "https://twitch.tv/videos/123456789",
            "https://m.twitch.tv/videos/123456789",
            "https://clips.twitch.tv/abc123",
            "http://www.twitch.tv/videos/123456789",
        ]
        for url in valid:
            assert is_supported_url(url), f"Expected valid: {url}"

    def test_valid_tiktok_urls(self):
        valid = [
            "https://www.tiktok.com/@user/video/123456789",
            "https://tiktok.com/@user/video/123456789",
            "https://m.tiktok.com/@user/video/123456789",
            "https://vm.tiktok.com/abc123/",
            "http://www.tiktok.com/@user/video/123456789",
        ]
        for url in valid:
            assert is_supported_url(url), f"Expected valid: {url}"

    def test_valid_instagram_urls(self):
        valid = [
            "https://www.instagram.com/p/ABC123/",
            "https://instagram.com/p/ABC123/",
            "http://www.instagram.com/p/ABC123/",
        ]
        for url in valid:
            assert is_supported_url(url), f"Expected valid: {url}"

    def test_subdomain_bypass_rejected_youtube(self):
        bypassed = [
            "https://youtube.com.evil.com/watch?v=abc",
            "https://notyoutube.com/watch?v=abc",
            "https://fakeyoutube.com/watch?v=abc",
            "https://youtube.com.attacker.net/watch?v=abc",
            "https://evil-youtube.com/watch?v=abc",
        ]
        for url in bypassed:
            assert not is_supported_url(url), f"Should reject bypass: {url}"

    def test_subdomain_bypass_rejected_vimeo(self):
        bypassed = [
            "https://vimeo.com.evil.com/",
            "https://fakevimeo.com/123",
        ]
        for url in bypassed:
            assert not is_supported_url(url), f"Should reject bypass: {url}"

    def test_subdomain_bypass_rejected_dailymotion(self):
        bypassed = [
            "https://dailymotion.com.evil.com/video/abc",
            "https://fakedailymotion.com/video/abc",
        ]
        for url in bypassed:
            assert not is_supported_url(url), f"Should reject bypass: {url}"

    def test_subdomain_bypass_rejected_twitch(self):
        bypassed = [
            "https://twitch.tv.evil.com/videos/123",
            "https://faketwitch.tv/videos/123",
        ]
        for url in bypassed:
            assert not is_supported_url(url), f"Should reject bypass: {url}"

    def test_subdomain_bypass_rejected_tiktok(self):
        bypassed = [
            "https://tiktok.com.evil.com/@user/video/123",
            "https://faketiktok.com/@user/video/123",
        ]
        for url in bypassed:
            assert not is_supported_url(url), f"Should reject bypass: {url}"

    def test_subdomain_bypass_rejected_instagram(self):
        bypassed = [
            "https://instagram.com.evil.com/p/ABC/",
            "https://fakeinstagram.com/p/ABC/",
        ]
        for url in bypassed:
            assert not is_supported_url(url), f"Should reject bypass: {url}"

    def test_invalid_schemes(self):
        invalid = [
            "ftp://youtube.com/watch?v=abc",
            "file://youtube.com/watch?v=abc",
            "javascript:alert(1)",
        ]
        for url in invalid:
            assert not is_supported_url(url), f"Should reject scheme: {url}"

    def test_unsupported_domains(self):
        invalid = [
            "https://www.google.com",
            "https://facebook.com/video/abc",
            "https://twitter.com/user/status/123",
        ]
        for url in invalid:
            assert not is_supported_url(url), f"Should reject domain: {url}"

    def test_invalid_input(self):
        assert not is_supported_url("")
        assert not is_supported_url("not-a-url")
        assert not is_supported_url("youtube.com")  # no scheme

    def test_case_insensitive(self):
        assert is_supported_url("https://WWW.YOUTUBE.COM/watch?v=abc")
        assert is_supported_url("HTTPS://YOUTU.BE/abc")
        assert is_supported_url("HTTPS://WWW.VIMEO.COM/123456")
        assert is_supported_url("HTTPS://WWW.TWITCH.TV/VIDEOS/123")

    def test_with_port(self):
        assert is_supported_url("https://youtube.com:443/watch?v=abc")
        assert not is_supported_url("https://youtube.com.evil.com:443/watch?v=abc")


class TestIsYouTubeUrlBackwardCompatibility:
    """Test that the old is_youtube_url name still works (YouTube-only)."""

    def test_old_name_accepts_youtube(self):
        assert is_youtube_url("https://www.youtube.com/watch?v=abc123")

    def test_old_name_rejects_new_platforms(self):
        assert not is_youtube_url("https://vimeo.com/123456789")
        assert not is_youtube_url("https://www.dailymotion.com/video/abc123")
        assert not is_youtube_url("https://www.twitch.tv/videos/123456789")
        assert not is_youtube_url("https://www.tiktok.com/@user/video/123456789")
        assert not is_youtube_url("https://www.instagram.com/p/ABC123/")

    def test_old_name_rejects_bypass(self):
        assert not is_youtube_url("https://youtube.com.evil.com/watch?v=abc")

    def test_old_name_agrees_with_new_name_on_youtube(self):
        urls = [
            "https://youtube.com/watch?v=abc",
            "https://youtu.be/abc123",
            "https://youtube.com.evil.com/watch?v=abc",
            "https://google.com",
            "",
            "not-a-url",
        ]
        for url in urls:
            assert is_youtube_url(url) == is_supported_url(url), f"Mismatch for: {url}"

    def test_old_name_rejects_new_platforms_where_supported_accepts(self):
        urls = [
            "https://vimeo.com/123",
            "https://www.dailymotion.com/video/abc",
            "https://www.twitch.tv/videos/123",
            "https://www.tiktok.com/@user/video/123",
            "https://www.instagram.com/p/ABC/",
        ]
        for url in urls:
            assert is_youtube_url(url) is False
            assert is_supported_url(url) is True
