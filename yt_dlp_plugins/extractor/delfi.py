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

# Media UUID: the portal-player placeholder div (``media-video-<uuid>``) and
# the NUXT payload (``data-id=<uuid>`` next to the manifest) expose it. The
# div and the manifest travel separately in the page, so the UUID that sits
# next to a manifest is the one that belongs to that video.
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_MEDIA_DIV_RE = re.compile(r"media-video-([0-9a-f-]{36})", re.IGNORECASE)


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
        """Download the article page and return the embedded video metadata."""
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
        """Return (manifest_url, video_id) or (None, None).

        The manifest URL and the media UUID travel separately in the article
        markup: the UUID is exposed by the ``<div id="media-video-<uuid>">``
        portal-player placeholder, while the manifest sits in the NUXT JSON
        payload, which carries a ``data-id=<uuid>`` attribute right next to the
        manifest URL. Both can appear several times on multi-video pages, so
        the manifest and the UUID are correlated by proximity (the payload's
        ``data-id`` wins; the placeholder div is the fallback).
        """
        jw = _JWPLAYER_RE.search(webpage)
        if not jw:
            return (None, None)

        manifest_url = f"https://cdn.jwplayer.com/manifests/{jw.group(1)}.m3u8"

        # The NUXT payload embeds the media UUID right after the manifest
        # (``...manifests/<id>.m3u8", ..., "<div data-id=<uuid>...``). Grab it
        # from a short window so it is the UUID *of this manifest* on
        # multi-video pages, not the first placeholder div in the document.
        media_id = None
        nearby = _UUID_RE.search(webpage[jw.start() : jw.start() + 500])
        if nearby:
            media_id = nearby.group(0)
        else:
            div = _MEDIA_DIV_RE.search(webpage)
            if div:
                media_id = div.group(1)

        return (manifest_url, media_id)
