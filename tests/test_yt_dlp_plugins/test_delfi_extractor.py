"""Tests for the Delfi yt-dlp plugin extractor.

Covers URL matching for delfi.ee article pages and parsing of the embedded
JWPlayer HLS manifest. The article HTML stores the manifest URL on
``cdn.jwplayer.com/manifests/<id>.m3u8`` with its slashes JSON-escaped as
``\\u002F``, so the generic extractor never sees it. Network-free: the parser
helpers are exercised against fixture HTML shaped like the real pages.
"""

import pytest

from yt_dlp_plugins.extractor.delfi import DelfiIE

ARTICLE_URL = (
    "https://www.delfi.ee/artikkel/120605642/"
    "video-ja-fotod-nimekiri-lukus-isamaa-fraktsiooni-juhid-ei-andnud-ulle-madisele-allkirja"
)


class TestValidUrl:
    """URL matching for delfi.ee articles."""

    @pytest.mark.parametrize(
        "url",
        [
            ARTICLE_URL,
            "https://www.delfi.ee/artikkel/120605642/slug",
            "https://delfi.ee/artikkel/99999/slug",
            "https://majandus.delfi.ee/artikkel/12345/slug",
            "http://www.delfi.ee/artikkel/54321/",
        ],
    )
    def test_matches_article_urls(self, url):
        """All delfi.ee article URL shapes are recognized."""
        assert DelfiIE.suitable(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.delfi.ee/static/120605642",
            "https://www.delfi.ee/",
            "https://evil.com/delfi.ee/artikkel/12345/slug",
            "https://www.delfi.ee/artikkel/slug-only",
        ],
    )
    def test_rejects_non_article_urls(self, url):
        """Non-article and foreign URLs are not matched."""
        assert not DelfiIE.suitable(url)


class TestMediaParsing:
    """Parsing of the embedded JWPlayer manifest from article HTML."""

    MEDIA_ID = "590caa54-cba3-4021-b96b-1f653978b336"
    MANIFEST_ID = "RY4HCkFa"

    @pytest.fixture()
    def extractor(self):
        """Return an extractor instance for the parse tests."""
        return DelfiIE()

    def _page(self, with_video: bool = True) -> str:
        """Build an article-page fixture, optionally embedding a video."""
        head = (
            "<html><head>"
            '<meta property="og:title" content="VIDEO ja FOTOD | Nimekiri lukus">'
            "<title>fallback</title></head><body>"
        )
        body = ""
        if with_video:
            # Slashes stored JSON-escaped as \u002F, as on the real page.
            manifest = (
                f'"https:\\u002F\\u002Fcdn.jwplayer.com\\u002Fmanifests'
                f'\\u002F{self.MANIFEST_ID}.m3u8"'
            )
            body = (
                '<div id="media-video-' + self.MEDIA_ID + '"></div>'
                f"<script>window.__NUXT__={{config:{{media:{manifest}}}}}</script>"
            )
        return head + body + "</body></html>"

    def test_extracts_manifest_and_media_id(self, extractor):
        """The manifest URL and the media UUID are extracted from the page."""
        manifest, media_id = extractor._parse_media(self._page())
        assert manifest == f"https://cdn.jwplayer.com/manifests/{self.MANIFEST_ID}.m3u8"
        assert media_id == self.MEDIA_ID

    def test_returns_none_when_no_video(self, extractor):
        """A page without an embedded video returns (None, None)."""
        assert extractor._parse_media(self._page(with_video=False)) == (None, None)

    def test_handles_plain_slashes_too(self, extractor):
        """Plain (unescaped) manifest slashes are parsed as well."""
        page = (
            '<div id="media-video-' + self.MEDIA_ID + '"></div>'
            f"https://cdn.jwplayer.com/manifests/{self.MANIFEST_ID}.m3u8"
        )
        manifest, media_id = extractor._parse_media(page)
        assert manifest == f"https://cdn.jwplayer.com/manifests/{self.MANIFEST_ID}.m3u8"
        assert media_id == self.MEDIA_ID

    def test_matches_media_id_to_manifest_on_multi_video_page(self, extractor):
        """The media UUID next to the first manifest wins over the first div."""
        # The manifest travels in the NUXT payload with the media UUID as a
        # nearby data-id; a page with several videos must pair the *first*
        # manifest with the UUID that belongs to it, not the first placeholder.
        first_media = "11111111-2222-3333-4444-555555555555"
        second_media = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        page = (
            '<div id="media-video-' + first_media + '"></div>'
            f"<script>window.__NUXT__={{config:{{media:"
            f'"https:\\u002F\\u002Fcdn.jwplayer.com\\u002Fmanifests\\u002F{self.MANIFEST_ID}.m3u8",'
            f'"\\u003Cdiv data-id={second_media} data-type=VIDEO}}'
            "</script>"
        )
        manifest, media_id = extractor._parse_media(page)
        assert manifest == f"https://cdn.jwplayer.com/manifests/{self.MANIFEST_ID}.m3u8"
        assert media_id == second_media

    def test_decoy_uuid_near_manifest_is_ignored(self, extractor):
        """Only the data-id value right after the manifest is used, not decoys."""
        decoy = "99999999-8888-7777-6666-555555555555"
        media_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        page = (
            '<div id="media-video-' + self.MEDIA_ID + '"></div>'
            f"<script>window.__NUXT__={{config:{{media:"
            f'"https:\\u002F\\u002Fcdn.jwplayer.com\\u002Fmanifests\\u002F{self.MANIFEST_ID}.m3u8",'
            f'"thumb": {decoy}, '
            f'"\\u003Cdiv data-id={media_id} data-type=VIDEO}}'
            "</script>"
        )
        manifest, parsed_media_id = extractor._parse_media(page)
        assert manifest == f"https://cdn.jwplayer.com/manifests/{self.MANIFEST_ID}.m3u8"
        assert parsed_media_id == media_id
