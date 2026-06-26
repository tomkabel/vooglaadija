from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from jose import JWTError, jwt

from core.config import settings

if TYPE_CHECKING:
    from starlette.responses import Response

ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"
PREVIOUS_SECRET_ACCEPTANCE_WINDOW = timedelta(hours=24)
PREVIOUS_SECRET_CLOCK_SKEW = timedelta(minutes=5)


def _make_token(
    subject: UUID | str,
    token_type: str,
    lifetime: timedelta,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    expire = datetime.now(UTC) + lifetime
    payload: dict[str, Any] = {
        "sub": str(subject),
        "exp": expire,
        "type": token_type,
        "iat": datetime.now(UTC),
        "jti": uuid4().hex,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_access_token(
    subject: UUID | str,
    token_version: int = 1,
) -> str:
    extra: dict[str, Any] = {"user_id": str(subject)}
    if token_version > 1:
        extra["ver"] = token_version
    return _make_token(
        subject,
        ACCESS_TOKEN_TYPE,
        timedelta(minutes=settings.access_token_expire_minutes),
        extra_claims=extra,
    )


def create_refresh_token(
    subject: UUID | str,
    token_version: int = 1,
) -> str:
    extra: dict[str, Any] = {}
    if token_version > 1:
        extra["ver"] = token_version
    extra["user_id"] = str(subject)
    return _make_token(
        subject,
        REFRESH_TOKEN_TYPE,
        timedelta(days=settings.refresh_token_expire_days),
        extra_claims=extra,
    )


def _decode_token(token: str, secret_key: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(
            token,
            secret_key,
            algorithms=[ALGORITHM],
            options={
                "verify_exp": True,
                "verify_signature": True,
                "require": ["sub", "exp"],
            },
        )
    except JWTError:
        return None


def _issued_at(payload: dict[str, Any]) -> datetime | None:
    value = payload.get("iat")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _is_within_previous_secret_window(payload: dict[str, Any]) -> bool:
    issued_at = _issued_at(payload)
    if issued_at is None:
        return False

    now = datetime.now(UTC)
    if issued_at > now + PREVIOUS_SECRET_CLOCK_SKEW:
        return False
    return now - issued_at <= PREVIOUS_SECRET_ACCEPTANCE_WINDOW


def verify_token(token: str, expected_type: str | None = None) -> dict[str, Any] | None:
    payload = _decode_token(token, settings.secret_key)
    if payload is None and settings.secret_key_previous:
        previous_payload = _decode_token(token, settings.secret_key_previous)
        if previous_payload is not None and _is_within_previous_secret_window(previous_payload):
            payload = previous_payload

    if payload is None:
        return None
    if expected_type is not None and payload.get("type") != expected_type:
        return None
    return payload


def set_token_cookies(
    response: "Response", access_token: str, refresh_token: str, secure: bool = True
) -> None:
    response.set_cookie(
        key="__Host-access_token",
        value=access_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=settings.access_token_expire_minutes * 60,
    )
    response.set_cookie(
        key="__Host-refresh_token",
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
    )


def clear_token_cookies(response: "Response") -> None:
    response.delete_cookie(key="__Host-access_token", path="/")
    response.delete_cookie(key="__Host-refresh_token", path="/")
