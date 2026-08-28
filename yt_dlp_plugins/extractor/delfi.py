"""yt-dlp plugin extractor for Delfi (Estonian news portal) article videos.

Delfi embeds lead/inline videos through a custom ``portal-player`` Vue
component. The actual media payload is *not* exposed as a plain
``<video src>`` element — instead the article HTML carries a JWPlayer
HLS manifest URL on ``cdn.jwplayer.com/manifests/<id>.m3u8``. In the
page source the slashes are stored JSON/HTML-escaped as ``\\u002F``, so
the URL never appears as a literal ``src``/``href`` attribute.

Because of that, yt-dlp's generic extractor finds no video and reports
``ERROR: Unsupported URL`` for every Delfi article (the root cause of the
failed ``delfi.ee/artikkel/...`` jobs).

This plugin mirrors the sibling ``postimees.py`` extractor (Postimees is
Delfi's sister site, same problem class) but targets Delfi's own markup:
it locates the JWPlayer manifest (escaped or plain slashes), then lets
yt-dlp's HLS parser produce the formats.
"""

import re
from typing import ClassVar

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import ExtractorError, url_or_none


# Delfi article URLs: https://www.delfi.ee/artikkel/<id>/<slug>
_VALID_HOSTS = r"(?:[A-Za-z0-9-]+\.)*delfi\.ee"

# JWPlayer manifest id. Delfi stores the URL with escaped slashes
# (\\u002F), so accept either the escaped or the plain form.
_JWPLAYER_RE = re.compile(
    r"cdn\.jwplayer\.com(?:\\u002F|/)+manifests(?:\\u002F|/)+([A-Za-z0-9]+)\.m3u8",
    re.IGNORECASE,
)


class DelfiIE(InfoExtractor):
    """Extractor for Delfi articles that embed a JWPlayer-hosted video."""

    IE_NAME = "delfi"
    _VALID_URL = rf"https?://{_VALID_HOSTS}/artikkel/(?P<id>\d{{5,}})(?:[/?#]|$)"
    _TESTS: ClassVar[list] = [
        {
            "url": "https://www.delfi.ee/artikkel/120605642/video-ja-fotod-nimekiri-lukus-isamaa-fraktsiooni-juhid-ei-andnud-ulle-madisele-allkirja",
            "info_dict": {
                "id": "590caa54-cba3-4021-b96b-1f653978b336",
                "title": "VIDEO ja FOTOD | Nimekiri lukus: Isamaa fraktsiooni juhid ei andnud Ülle Madisele allkirja",
                "ext": "mp4",
            },
            "params": {"skip_download": True},
        }
    ]

    def _real_extract(self, url):
        article_id = self._match_id(url)
        webpage = self._download_webpage(url, article_id)

        manifest_url, video_id = self._parse_media(webpage)
        if not manifest_url:
            # No embedded video on the page (plain text article or a CMS
            # change). Fail fast as expected so the app's error classifier
            # does not burn retries on a generic "Unsupported URL".
            raise ExtractorError("Video not found", expected=True)

        manifest_url = url_or_none(manifest_url)
        if not manifest_url:
            raise ExtractorError("Video not found", expected=True)

        formats = self._extract_m3u8_formats(
            manifest_url, video_id or article_id, "mp4", m3u8_id="hls", fatal=False
        )
        if not formats:
            raise ExtractorError("No playable formats found", expected=True)

        title = (
            self._og_search_title(webpage)
            or self._html_search_regex(
                r"<title[^>]*>(.*?)</title>", webpage, "title", default=None
            )
            or article_id
        )

        return {
            "id": video_id or article_id,
            "title": title,
            "formats": formats,
            "webpage_url": url,
        }

    # -- helpers ---------------------------------------------------------

    def _parse_media(self, webpage: str):
        """Return (manifest_url, video_id) or (None, None)."""
        for portal_div in re.finditer(r'<div[^>]*\bid=["\']media-video-([0-9a-f-]{36})["\'][^>]*>', webpage):
            div_id = portal_div.group(1)
            div_start = portal_div.start()
            div_end = portal_end(div_start, webpage)
            div_html = webpage[div_start:div_end]
            jw = _JWPLAYER_RE.search(div_html)
            if jw:
                return ("https://cdn.jwplayer.com/manifests/%s.m3u8" % jw.group(1), div_id)
        return (None, None)


def div_end(start: int, html: str) -> int:
    """Return the index past the closing </div> of the opened div at `start`."""
    depth = 0
    pos = start
    while pos < len(html):
        m = re.search(r'<(/?div)(?:\s[^>]*)?\s*/?>', html[pos:], re.IGNORECASE)
        if not m:
            break
        pos += m.end()
        if m.group(1).lower() == 'div':
            if html[m.start() + 1] == '/':
                depth -= 1
                if depth < 0:
                    return pos
            else:
                depth += 1
    return len(html)
