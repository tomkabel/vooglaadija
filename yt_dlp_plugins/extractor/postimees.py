"""yt-dlp plugin extractor for the Postimees Group article platform.

Covers every site served by the Postimees (PMO) CMS that embeds the Vue
``<video-player-wrapper>`` component in its articles:

- reporter.kanal2.ee (Kanal 2 news portal; the URL in the original bug report)
- www.postimees.ee and regional Postimees portals (tartu.postimees.ee, ...)
- elu24.ee

Why this plugin exists
----------------------
The article pages carry the media payload as an HTML-escaped JSON blob inside
the ``:item`` attribute of the ``<video-player-wrapper>`` component (HLS/DASH
source URLs live on ``router.euddn.net``). The URLs never appear as plain
``src``/``href`` attributes, so yt-dlp's generic extractor cannot find any
video and reports ``ERROR: Unsupported URL`` for every such article (this was
the root cause of the failed ``reporter.kanal2.ee`` job).

The historical ``Kanal2IE`` extractor (old ``kanal2.postimees.ee/pluss``
player) was removed upstream in 2024 as dead (yt-dlp PR #9238); this plugin is
the replacement for the current Postimees CMS player.
"""

import html
import json
import re
from typing import ClassVar

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import ExtractorError, int_or_none, traverse_obj, url_or_none

# Postimees CMS article URLs look like https://<site>/<article_id>/<slug>.
# Subdomains vary: reporter.kanal2.ee, tartu.postimees.ee, ...
_VALID_HOSTS = r"(?:[A-Za-z0-9-]+\.)*(?:kanal2\.ee|postimees\.ee|elu24\.ee)"


class PostimeesIE(InfoExtractor):
    """Extractor for Postimees CMS articles with a lead video."""

    IE_NAME = "postimees"
    _VALID_URL = rf"https?://{_VALID_HOSTS}/(?P<id>\d{{5,}})(?:[/?#]|$)"
    _TESTS: ClassVar[list] = [
        {
            "url": "https://reporter.kanal2.ee/8531612/jaagup-tuisk-jattis-joomise-avastas-jooksmise-ja-kaotas-8-kilo",
            "info_dict": {
                "id": "7761295",
                "title": "Jaagup Tuisk jättis joomise, avastas jooksmise ja kaotas 8 kilo",
                "duration": 192.0,
                "ext": "mp4",
            },
        }
    ]

    def _real_extract(self, url):
        article_id = self._match_id(url)
        webpage = self._download_webpage(url, article_id)

        item = self._extract_player_item(webpage, article_id)
        if not item:
            # No <video-player-wrapper> on the page (plain text article or a
            # platform change). Phrase the error so the app's error classifier
            # buckets it as NOT_FOUND and fails fast instead of retrying.
            raise ExtractorError("Video not found", expected=True)

        video_id = str(traverse_obj(item, ("id")) or article_id)

        hls_url = url_or_none(traverse_obj(item, ("sources", "hls")))
        dash_url = url_or_none(traverse_obj(item, ("sources", "dash")))

        formats = []
        if hls_url:
            formats.extend(
                self._extract_m3u8_formats(hls_url, video_id, "mp4", m3u8_id="hls", fatal=False)
            )
        if dash_url:
            formats.extend(
                self._extract_mpd_formats(dash_url, video_id, mpd_id="dash", fatal=False)
            )
        if not formats:
            raise ExtractorError("No playable formats found", expected=True)

        return {
            "id": video_id,
            "title": (
                traverse_obj(item, ("headline"))
                or self._extract_article_headline(webpage)
                or self._page_title(webpage)
            ),
            "formats": formats,
            "thumbnail": url_or_none(
                traverse_obj(item, ("thumbnail", "sources", "landscape", "large"))
            ),
            "duration": (
                int_or_none(traverse_obj(item, ("meta", "duration"))) / 1000
                if traverse_obj(item, ("meta", "duration"))
                else None
            ),
            "webpage_url": url,
        }

    # -- helpers ---------------------------------------------------------

    def _extract_player_item(self, webpage: str, video_id: str) -> dict | None:
        """Parse the JSON payload of the first <video-player-wrapper> component.

        The ``:item`` attribute is a double-quoted, HTML-escaped JSON blob
        (inner quotes are ``&quot;``, slashes ``&#92;``); the attribute is
        terminated by the following ``:opts`` attribute.
        """
        match = re.search(r':item="(.*?)"\s+:opts="', webpage, re.DOTALL)
        if not match:
            return None
        try:
            raw = html.unescape(match.group(1))
            data = json.loads(raw)
        except (ValueError, TypeError) as exc:
            # report_warning needs a live downloader; unit tests call the
            # parser helpers without one, so degrade to a silent skip.
            if self._downloader is not None:
                self.report_warning(f"Failed to parse video-player-wrapper payload: {exc}")
            return None
        return data if isinstance(data, dict) else None

    def _extract_article_headline(self, webpage: str) -> str | None:
        """Fall back to the article headline embedded in the :opts attribute."""
        match = re.search(r':opts="(.*?)"\s+:recommendations=', webpage, re.DOTALL)
        if not match:
            return None
        try:
            opts = json.loads(html.unescape(match.group(1)))
        except (ValueError, TypeError):
            return None
        return traverse_obj(opts, ("article", "headline"))

    def _page_title(self, webpage: str) -> str | None:
        """Last-resort title from the HTML <title> tag."""
        match = re.search(r"<title[^>]*>(.*?)</title>", webpage, re.DOTALL)
        if match:
            return html.unescape(match.group(1)).strip() or None
        return None
