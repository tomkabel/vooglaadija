import asyncio
import ipaddress
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse

from core.logging_config import get_logger

logger = get_logger(__name__)

# Exact allowed YouTube domains
_YOUTUBE_DOMAINS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
}

_YOUTUBE_SHORT_DOMAINS = {
    "youtu.be",
}

_YOUTUBE_NOCOOKIE_DOMAINS = {
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}

_VIMEO_DOMAINS = {
    "vimeo.com",
    "www.vimeo.com",
}

_DAILYMOTION_DOMAINS = {
    "dailymotion.com",
    "www.dailymotion.com",
}

_TWITCH_DOMAINS = {
    "twitch.tv",
    "www.twitch.tv",
    "m.twitch.tv",
    "clips.twitch.tv",
}

_TIKTOK_DOMAINS = {
    "tiktok.com",
    "www.tiktok.com",
    "m.tiktok.com",
    "vm.tiktok.com",
}

_INSTAGRAM_DOMAINS = {
    "instagram.com",
    "www.instagram.com",
}

_EXTRA_DOMAINS = (
    _VIMEO_DOMAINS | _DAILYMOTION_DOMAINS | _TWITCH_DOMAINS | _TIKTOK_DOMAINS | _INSTAGRAM_DOMAINS
)


def is_supported_url(url: str) -> bool:
    """Validate if URL is from a supported platform.

    Uses exact domain matching to prevent subdomain bypass attacks
    (e.g., youtube.com.evil.com must NOT match).

    Supported platforms: YouTube, Vimeo, Dailymotion, Twitch, TikTok, Instagram.
    """
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()

        if scheme not in ("http", "https"):
            return False

        hostname = (parsed.hostname or "").lower()

        if hostname in _YOUTUBE_DOMAINS:
            return True
        if hostname in _YOUTUBE_SHORT_DOMAINS:
            return True
        if hostname in _YOUTUBE_NOCOOKIE_DOMAINS:
            return True
        if hostname in _EXTRA_DOMAINS:
            return True

        return False
    except (ValueError, AttributeError):
        return False


_PRIVATE_IPV4_NETWORKS = [
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("127.0.0.0/8"),
    ipaddress.IPv4Network("169.254.0.0/16"),
    ipaddress.IPv4Network("0.0.0.0/8"),
    ipaddress.IPv4Network("100.64.0.0/10"),
]

_PRIVATE_IPV6_NETWORKS = [
    ipaddress.IPv6Network("::1/128"),
    ipaddress.IPv6Network("fc00::/7"),
    ipaddress.IPv6Network("fe80::/10"),
    ipaddress.IPv6Network("fd00::/8"),
]


def _is_private_ip(addr_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(addr_str)
        networks = _PRIVATE_IPV6_NETWORKS if addr.version == 6 else _PRIVATE_IPV4_NETWORKS
        return any(addr in network for network in networks)
    except ValueError:
        return False


async def _validate_hostname_not_private(hostname: str) -> bool:
    """Resolve a hostname and verify none of its addresses are private.

    Returns True if all addresses are public, False if any resolves to
    a private/reserved IP range or resolution fails.
    """
    try:
        loop = asyncio.get_running_loop()
        addrs = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        for _family, _type, _proto, _cname, sockaddr in addrs:
            if _is_private_ip(sockaddr[0]):
                logger.warning("ssrf_private_ip_detected", hostname=hostname, ip=sockaddr[0])
                return False
        return True
    except (TimeoutError, OSError):
        logger.warning("ssrf_resolution_failed", hostname=hostname, exc_info=True)
        return False


async def _check_redirect_target(url: str) -> bool:
    """Follow one level of HTTP redirects and validate each target hostname.

    Makes a HEAD request with redirect-following DISABLED, then inspects
    any Location header to validate the redirect target's IP before ever
    connecting to it. Returns True if safe, False if any redirect target
    resolves to a private IP.
    """

    class _NoFollowRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            req._redirect_target = newurl  # stash for inspection
            raise urllib.error.HTTPError(url, code, "SSRF redirect check", headers, fp)

    def _check():
        opener = urllib.request.build_opener(_NoFollowRedirects)
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "Mozilla/5.0")
        try:
            with opener.open(req, timeout=10):
                pass  # no redirect — safe
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                redirect_url = e.headers.get("Location") or e.filename
                if redirect_url:
                    parsed = urlparse(redirect_url)
                    target = parsed.hostname
                    if target:
                        addrs = socket.getaddrinfo(target, None, type=socket.SOCK_STREAM)
                        for _family, _type, _proto, _cname, sockaddr in addrs:
                            if _is_private_ip(sockaddr[0]):
                                return False
            # Non-redirect HTTP errors are fine — doesn't affect SSRF check
        except (urllib.error.URLError, TimeoutError, OSError):
            pass  # Transient — don't block; the actual download will handle error
        return True

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _check)


async def validate_url_not_ssrf(url: str) -> bool:
    """Resolve the URL hostname and verify it does not point to a private IP.

    This is the last line of defense against SSRF attacks. It performs a
    non-blocking DNS resolution and checks each resolved address against
    private/reserved IP ranges (RFC 1918, loopback, link-local, CGNAT).

    Also follows one level of HTTP redirects to detect redirect-based SSRF
    bypasses where the initial URL resolves to a public CDN that then
    redirects to a private address (e.g. cloud metadata endpoint).

    Returns True if the URL is safe (public IP), False if it resolves to
    a private IP or resolution fails.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return False

    if not await _validate_hostname_not_private(hostname):
        return False

    if not await _check_redirect_target(url):
        return False

    return True


_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password123",
        "12345678",
        "123456789",
        "qwerty123",
        "abc123",
        "letmein",
        "welcome",
        "monkey",
        "dragon",
        "master",
        "sunshine",
        "princess",
        "football",
        "iloveyou",
        "trustno1",
        "passw0rd",
        "admin123",
        "test1234",
        "changeme",
    }
)


def validate_password(password: str) -> str | None:
    """Validate password strength. Returns None if valid, or an error message."""
    if len(password) < 8:
        return "Password must be at least 8 characters"
    if len(password) > 128:
        return "Password must be at most 128 characters"
    if password.lower() in _COMMON_PASSWORDS:
        return "Password is too common. Choose a more unique password."
    return None


def is_youtube_url(url: str) -> bool:
    """Validate if URL is a YouTube URL (backward-compatible).

    Uses exact domain matching to prevent subdomain bypass attacks.
    Only matches YouTube domains — does NOT match Vimeo, Twitch, etc.
    """
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()

        if scheme not in ("http", "https"):
            return False

        hostname = (parsed.hostname or "").lower()

        if hostname in _YOUTUBE_DOMAINS:
            return True
        if hostname in _YOUTUBE_SHORT_DOMAINS:
            return True
        if hostname in _YOUTUBE_NOCOOKIE_DOMAINS:
            return True

        return False
    except (ValueError, AttributeError):
        return False
