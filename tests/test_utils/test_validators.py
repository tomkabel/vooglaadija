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

    def test_decoy_hosts_accepted_when_whitelist_disabled(self):
        # HOTFIX: the per-platform whitelist is disabled, so any http(s) host
        # (including look-alike / decoy hosts) is accepted. Scheme-based attacks
        # (file://, javascript:) are still rejected below, and SSRF resolution
        # remains a separate guard.
        accepted = [
            "https://youtube.com.evil.com/watch?v=abc",
            "https://notyoutube.com/watch?v=abc",
            "https://fakeyoutube.com/watch?v=abc",
            "https://youtube.com.attacker.net/watch?v=abc",
            "https://evil-youtube.com/watch?v=abc",
            "https://vimeo.com.evil.com/",
            "https://fakevimeo.com/123",
            "https://dailymotion.com.evil.com/video/abc",
            "https://fakedailymotion.com/video/abc",
            "https://twitch.tv.evil.com/videos/123",
            "https://faketwitch.tv/videos/123",
            "https://tiktok.com.evil.com/@user/video/123",
            "https://faketiktok.com/@user/video/123",
            "https://instagram.com.evil.com/p/ABC/",
            "https://fakeinstagram.com/p/ABC/",
        ]
        for url in accepted:
            assert is_supported_url(url), f"Should accept (whitelist disabled): {url}"

    def test_invalid_schemes(self):
        invalid = [
            "ftp://youtube.com/watch?v=abc",
            "file://youtube.com/watch?v=abc",
            "javascript:alert(1)",
        ]
        for url in invalid:
            assert not is_supported_url(url), f"Should reject scheme: {url}"

    def test_unsupported_domains_accepted_when_whitelist_disabled(self):
        # HOTFIX: whitelist disabled, so non-allowlisted hosts are now accepted.
        accepted = [
            "https://www.google.com",
            "https://facebook.com/video/abc",
            "https://twitter.com/user/status/123",
        ]
        for url in accepted:
            assert is_supported_url(url), f"Should accept (whitelist disabled): {url}"

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
        assert is_supported_url("https://youtube.com.evil.com:443/watch?v=abc")


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
        # Both accept YouTube URLs (whitelist disabled only affects the extra
        # platforms, not YouTube, which is_youtube_url also accepts).
        urls = [
            "https://youtube.com/watch?v=abc",
            "https://youtu.be/abc123",
        ]
        for url in urls:
            assert is_youtube_url(url) == is_supported_url(url), f"Mismatch for: {url}"

    def test_old_name_rejects_new_platforms_where_supported_accepts(self):
        # With the whitelist disabled, is_supported_url now accepts the extra
        # platforms while is_youtube_url (YouTube-only) still rejects them.
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

    def test_supported_accepts_google_where_old_name_rejects(self):
        # With the whitelist disabled, is_supported_url accepts non-YouTube
        # domains that is_youtube_url still rejects — this divergence is the
        # intended behaviour of the hotfix.
        assert is_youtube_url("https://google.com") is False
        assert is_supported_url("https://google.com") is True
        assert is_youtube_url("") is False
        assert is_supported_url("") is False
        assert is_youtube_url("not-a-url") is False
        assert is_supported_url("not-a-url") is False
