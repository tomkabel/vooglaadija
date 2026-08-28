"""Regression tests for error_classifier retry-category routing.

These pin the contract that generic, transient media errors (e.g. a yt-dlp
``HTTP Error 403: Forbidden`` that is really YouTube-side throttling) are
routed to TRANSIENT so the worker retries them with jitter, while
request-specific permanent markers (geo, DRM, copyright, login required,
"blocked") stay in BLOCKED/NOT_FOUND and remain non-retryable.

See the 2026-08-23 incident: the same YouTube URL failed twice with a 403 and
then succeeded ~10h later, proving the 403 was transient, not permanent.
"""

import pytest

from app.services.error_classifier import (
    CATEGORY_POLICIES,
    ErrorCategory,
    classify_error,
    is_non_retryable,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "error_str,expected",
    [
        # Generic yt-dlp 403 — the classic transient throttling signature.
        (
            (
                "yt-dlp extraction failed: ERROR: unable to download video data: "
                "HTTP Error 403: Forbidden"
            ),
            ErrorCategory.TRANSIENT,
        ),
        # Bare 403 code.
        ("HTTP Error 403", ErrorCategory.TRANSIENT),
        # Geo / permanent markers must remain BLOCKED (non-retryable).
        # NOTE: "unavailable in your country" matches NOT_FOUND (which is
        # ordered before BLOCKED), so use a BLOCKED-specific marker here.
        (
            "Sign in to confirm your age",
            ErrorCategory.BLOCKED,
        ),
        (
            "Your IP address is blocked from accessing this post",
            ErrorCategory.BLOCKED,
        ),
        # DRM / copyright markers (BLOCKED-specific keywords).
        ("This video is blocked by the copyright owner", ErrorCategory.BLOCKED),
        ("Video removed due to a copyright claim (DMCA)", ErrorCategory.BLOCKED),
    ],
)
def test_classify_error_routes_transient_403_to_transient(error_str, expected):
    assert classify_error(error_str).category == expected


@pytest.mark.unit
def test_generic_403_is_retryable_while_blocked_markers_are_not():
    transient_403 = classify_error(
        "ERROR: unable to download video data: HTTP Error 403: Forbidden"
    )
    assert not is_non_retryable(transient_403.category)
    assert CATEGORY_POLICIES[transient_403.category].max_retries > 0

    blocked = classify_error("Your IP address is blocked from accessing this post")
    assert is_non_retryable(blocked.category)
    assert CATEGORY_POLICIES[blocked.category].max_retries == 0


@pytest.mark.unit
def test_geo_unavailable_in_your_country_routes_to_blocked():
    # "unavailable in your country" is a permanent, request-specific condition.
    # The BLOCKED pattern "unavailable in your" matches it before NOT_FOUND,
    # and BLOCKED is non-retryable — which is the correct terminal verdict.
    assert (
        classify_error("This video is unavailable in your country").category
        == ErrorCategory.BLOCKED
    )
    assert is_non_retryable(ErrorCategory.BLOCKED)


@pytest.mark.unit
def test_known_permanent_categories_remain_non_retryable():
    for category in (ErrorCategory.BLOCKED, ErrorCategory.NOT_FOUND):
        assert is_non_retryable(category)
        assert CATEGORY_POLICIES[category].max_retries == 0


@pytest.mark.unit
@pytest.mark.parametrize(
    "error_str",
    [
        "Requested format is not available",
        "[youtube] All formats failed. Last error: Requested format is not available",
        # Emitted by the single-pass format fallback (issue #169) when yt-dlp
        # returns no info object at all; must stay format_unavailable, not UNKNOWN.
        "[youtube] No video info returned",
        "[tiktok] No video info returned",
    ],
)
def test_format_failure_signals_route_to_format_unavailable(error_str):
    """Exhausted format chains keep classifying as FORMAT_UNAVAILABLE (0 retries).

    This guards the #169 rewrite: the old per-spec loop emitted an
    "All formats failed" summary that matched the classifier; the native
    single-pass chain must not silently degrade these to UNKNOWN.
    """
    result = classify_error(error_str)
    assert result.category == ErrorCategory.FORMAT_UNAVAILABLE
    assert is_non_retryable(result.category)


@pytest.mark.unit
def test_transient_category_retains_retry_budget():
    policy = CATEGORY_POLICIES[ErrorCategory.TRANSIENT]
    assert policy.max_retries == 3
    assert policy.jitter.name == "DECORRELATED"
