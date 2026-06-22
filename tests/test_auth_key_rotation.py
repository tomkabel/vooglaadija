"""Focused tests for JWT key rotation support."""

from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt

from app import auth

CURRENT_SECRET = "current-secret-key-for-rotation-tests-32chars"
PREVIOUS_SECRET = "previous-secret-key-for-rotation-tests-32chars"


class RotationSettings:
    """Minimal settings object for app.auth tests."""

    secret_key = CURRENT_SECRET
    secret_key_previous = PREVIOUS_SECRET
    access_token_expire_minutes = 15
    refresh_token_expire_days = 7


def _previous_key_token(
    *,
    subject: str = "rotation-user",
    token_type: str = auth.ACCESS_TOKEN_TYPE,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> str:
    issued_at = issued_at or datetime.now(UTC)
    expires_at = expires_at or datetime.now(UTC) + timedelta(days=1)
    return jwt.encode(
        {
            "sub": subject,
            "exp": expires_at,
            "type": token_type,
            "iat": issued_at,
            "jti": "previous-key-jti",
        },
        PREVIOUS_SECRET,
        algorithm=auth.ALGORITHM,
    )


def test_previous_key_token_with_recent_iat_is_accepted(monkeypatch):
    """A previous-key token issued inside the 24-hour window is accepted."""
    monkeypatch.setattr(auth, "settings", RotationSettings())

    token = _previous_key_token(issued_at=datetime.now(UTC) - timedelta(hours=1))
    payload = auth.verify_token(token, expected_type=auth.ACCESS_TOKEN_TYPE)

    assert payload is not None
    assert payload["sub"] == "rotation-user"


def test_previous_key_token_older_than_window_is_rejected(monkeypatch):
    """A previous-key token older than 24 hours is rejected even with future exp."""
    monkeypatch.setattr(auth, "settings", RotationSettings())

    token = _previous_key_token(issued_at=datetime.now(UTC) - timedelta(hours=25))

    assert auth.verify_token(token, expected_type=auth.ACCESS_TOKEN_TYPE) is None


def test_previous_key_token_is_rejected_when_previous_secret_empty(monkeypatch):
    """An empty previous secret disables backward-compatible verification."""
    monkeypatch.setattr(
        auth,
        "settings",
        RotationSettings(),
    )
    monkeypatch.setattr(auth.settings, "secret_key_previous", "")

    token = _previous_key_token(issued_at=datetime.now(UTC) - timedelta(hours=1))

    assert auth.verify_token(token, expected_type=auth.ACCESS_TOKEN_TYPE) is None


def test_new_tokens_are_signed_with_current_secret_only(monkeypatch):
    """New access and refresh tokens decode with the current key, not previous key."""
    monkeypatch.setattr(auth, "settings", RotationSettings())

    access = auth.create_access_token("rotation-user")
    refresh = auth.create_refresh_token("rotation-user")

    access_payload = jwt.decode(access, CURRENT_SECRET, algorithms=[auth.ALGORITHM])
    refresh_payload = jwt.decode(refresh, CURRENT_SECRET, algorithms=[auth.ALGORITHM])

    assert access_payload["type"] == auth.ACCESS_TOKEN_TYPE
    assert refresh_payload["type"] == auth.REFRESH_TOKEN_TYPE

    for token in (access, refresh):
        try:
            jwt.decode(token, PREVIOUS_SECRET, algorithms=[auth.ALGORITHM])
        except JWTError:
            pass
        else:
            raise AssertionError("new token unexpectedly decoded with previous key")


def test_current_and_previous_tokens_work_during_transition(monkeypatch):
    """Current-key and recent previous-key tokens both verify during transition."""
    monkeypatch.setattr(auth, "settings", RotationSettings())

    current_token = auth.create_access_token("current-user")
    previous_token = _previous_key_token(
        subject="previous-user",
        issued_at=datetime.now(UTC) - timedelta(minutes=30),
    )

    assert auth.verify_token(current_token, expected_type=auth.ACCESS_TOKEN_TYPE)["sub"] == (
        "current-user"
    )
    assert auth.verify_token(previous_token, expected_type=auth.ACCESS_TOKEN_TYPE)["sub"] == (
        "previous-user"
    )


def test_expected_type_filtering_still_rejects_wrong_type(monkeypatch):
    """Expected token type filtering still rejects valid tokens of another type."""
    monkeypatch.setattr(auth, "settings", RotationSettings())

    token = auth.create_access_token("rotation-user")

    assert auth.verify_token(token, expected_type=auth.REFRESH_TOKEN_TYPE) is None


def test_previous_key_token_missing_iat_is_rejected(monkeypatch):
    """A previous-key token without iat is rejected for transition safety."""
    monkeypatch.setattr(auth, "settings", RotationSettings())
    token = jwt.encode(
        {
            "sub": "rotation-user",
            "exp": datetime.now(UTC) + timedelta(days=1),
            "type": auth.ACCESS_TOKEN_TYPE,
            "jti": "missing-iat",
        },
        PREVIOUS_SECRET,
        algorithm=auth.ALGORITHM,
    )

    assert auth.verify_token(token, expected_type=auth.ACCESS_TOKEN_TYPE) is None


def test_previous_key_token_with_future_iat_is_rejected(monkeypatch):
    """A previous-key token with iat beyond clock skew is rejected."""
    monkeypatch.setattr(auth, "settings", RotationSettings())

    token = _previous_key_token(issued_at=datetime.now(UTC) + timedelta(minutes=10))

    assert auth.verify_token(token, expected_type=auth.ACCESS_TOKEN_TYPE) is None


def test_previous_key_token_with_malformed_iat_is_rejected(monkeypatch):
    """A previous-key token with malformed iat is rejected."""
    monkeypatch.setattr(auth, "settings", RotationSettings())
    token = jwt.encode(
        {
            "sub": "rotation-user",
            "exp": datetime.now(UTC) + timedelta(days=1),
            "type": auth.ACCESS_TOKEN_TYPE,
            "iat": "not-a-timestamp",
            "jti": "malformed-iat",
        },
        PREVIOUS_SECRET,
        algorithm=auth.ALGORITHM,
    )

    assert auth.verify_token(token, expected_type=auth.ACCESS_TOKEN_TYPE) is None
