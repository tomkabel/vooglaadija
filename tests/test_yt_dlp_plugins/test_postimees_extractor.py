"""Tests for the Postimees CMS yt-dlp plugin extractor.

Covers URL matching for the Postimees family of portals and parsing of the
``<video-player-wrapper>`` component payload (the HTML-escaped JSON blob that
carries the HLS/DASH source URLs). Network-free: the parser helpers are
exercised against fixture HTML shaped like the real pages.
"""

import json

import pytest

from yt_dlp_plugins.extractor.postimees import PostimeesIE

ARTICLE_URL = (
    "https://reporter.kanal2.ee/8531612/"
    "jaagup-tuisk-jattis-joomise-avastas-jooksmise-ja-kaotas-8-kilo"
)


class TestValidUrl:
    """URL matching for the Postimees CMS family."""

    @pytest.mark.parametrize(
        "url",
        [
            ARTICLE_URL,
            "https://reporter.kanal2.ee/8531612/",
            "https://www.postimees.ee/8531798/hind-maarab-eestlased",
            "https://tartu.postimees.ee/12345/slug-here",
            "https://elu24.ee/8532041/malestusgalerii-siim-kallas",
            "http://kanal2.ee/12345/slug",
        ],
    )
    def test_matches_family_domains(self, url):
        assert PostimeesIE.suitable(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.kanal2.ee/player/123",
            "https://postimees.ee/not-an-article",
            "https://evil.com/postimees.ee/12345/slug",
            "https://www.postimees.ee/comments/8531798",
        ],
    )
    def test_rejects_non_article_urls(self, url):
        assert not PostimeesIE.suitable(url)


class TestPlayerItemParsing:
    """Parsing of the escaped JSON inside <video-player-wrapper>."""

    @pytest.fixture()
    def extractor(self):
        return PostimeesIE()

    def _item_json(self) -> str:
        """Build the HTML-escaped JSON payload the CMS emits in :item."""
        payload = {
            "id": 7761295,
            "type": "video",
            "headline": "",
            "geoBlock": True,
            "meta": {"duration": 192000},
            "thumbnail": {
                "sources": {
                    "landscape": {
                        "large": "https://f12.pmo.ee/uxFotRUj87i9xO5nx1DqOWYUTT4=/1920x1080/smart/nginx/o/2026/08/23/17873390t1h2202.jpg",
                    },
                },
            },
            "sources": {
                "hls": "https://router.euddn.net/437523f5e0232f49b8c4d532bd9a566c/smil:8301/2026/08/23/jaagup_tuisk_kaotas_8_kilo_160826_valmis_TRczEqlr/play_hd.smil/playlist.m3u8?c=7F00&t=rest-apia03910e53cd2c333ecdb188067b30cba",
                "dash": "https://router.euddn.net/437523f5e0232f49b8c4d532bd9a566c/smil:8301/2026/08/23/jaagup_tuisk_kaotas_8_kilo_160826_valmis_TRczEqlr/play_hd.smil/manifest.mpd?c=7F00&t=rest-apia03910e53cd2c333ecdb188067b30cba",
            },
        }
        # Emulate the CMS escaping: quotes -> &quot;, slashes -> &#92;
        escaped = json.dumps(payload).replace("\\", "&#92;").replace('"', "&quot;")
        return escaped

    def _opts_json(self) -> str:
        """Build the :opts payload holding the article headline."""
        opts = {
            "article": {
                "id": 8531612,
                "headline": "Jaagup Tuisk jättis joomise, avastas jooksmise ja kaotas 8 kilo",
            }
        }
        return json.dumps(opts).replace("\\", "&#92;").replace('"', "&quot;")

    def _page(self, with_player: bool = True) -> str:
        head = "<html><head><title>Some title</title></head><body>"
        player = ""
        if with_player:
            player = (
                f'<video-player-wrapper component="video-player" '
                f':item="{self._item_json()}" '
                f':opts="{self._opts_json()}" '
                f':recommendations="{{section: 2993}}">'
                f"</video-player-wrapper>"
            )
        return head + player + "</body></html>"

    def test_extracts_hls_and_dash_sources(self, extractor):
        item = extractor._extract_player_item(self._page(), "8531612")
        assert item is not None
        assert item["id"] == 7761295
        assert item["sources"]["hls"].startswith("https://router.euddn.net/")
        assert "playlist.m3u8?c=7F00&t=rest-api" in item["sources"]["hls"]
        assert "manifest.mpd?c=7F00&t=rest-api" in item["sources"]["dash"]

    def test_returns_none_when_no_player_component(self, extractor):
        assert extractor._extract_player_item(self._page(with_player=False), "8531612") is None

    def test_extracts_article_headline_from_opts(self, extractor):
        headline = extractor._extract_article_headline(self._page())
        assert headline == "Jaagup Tuisk jättis joomise, avastas jooksmise ja kaotas 8 kilo"

    def test_title_fallback_prefers_headline_over_page_title(self, extractor):
        # headline is empty in :item -> falls back to :opts article headline
        page = self._page()
        assert extractor._extract_article_headline(page) != ""

    def test_malformed_json_returns_none(self, extractor):
        page = '<video-player-wrapper :item="not-json" :opts=""></video-player-wrapper>'
        assert extractor._extract_player_item(page, "8531612") is None
