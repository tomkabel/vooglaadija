import math
import os
import warnings
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _estimate_entropy(text: str) -> float:
    """Estimate Shannon entropy of a string in bits.

    A truly random hex string has 4 bits per character.
    A truly random alphanumeric string has ~6.5 bits per character.
    We flag anything below 3 bits/char as suspiciously low-entropy.
    """
    if not text:
        return 0.0
    freq: dict[str, int] = {}
    for c in text:
        freq[c] = freq.get(c, 0) + 1
    length = len(text)
    entropy = 0.0
    for count in freq.values():
        p = count / length
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy


class Settings(BaseSettings):
    # Application mode - used for structlog, sentry, etc.
    environment: str = "development"

    database_url: str = ""
    secret_key: str = ""
    redis_url: str = ""
    cors_origins: str = "http://localhost:3000"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    file_expire_hours: int = 24
    storage_path: str = "./storage"
    bcrypt_rounds: int = 12

    # Cookie security — False for local dev (no HTTPS), True for production
    cookie_secure: bool = False

    # Feature flags — following FEATURE_*_ENABLED convention
    feature_chaos_api_enabled: bool = False
    feature_throttle_preemptive_enabled: bool = False

    # Throttle predictor — sliding window 429 detection
    throttle_window_seconds: int = 60
    throttle_risk_threshold_scale: int = 10
    throttle_risk_threshold: float = 0.7

    # Used to construct DATABASE_URL if not set directly
    db_user: str = "postgres"
    db_password: str = ""
    db_name: str = "ytprocessor"
    db_host: str = "localhost"
    db_port: str = "5432"

    # Used to construct REDIS_URL if not set directly
    redis_host: str = "localhost"
    redis_port: str = "6379"
    redis_password: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_and_construct(self) -> "Settings":
        testing_val = os.environ.get("TESTING", "").lower()
        is_testing = testing_val in ("1", "true", "yes", "on")
        if is_testing:
            return self._apply_testing_defaults()

        self._build_database_url()
        self._validate_secret_key()
        self._warn_cors()
        self._resolve_storage()
        self._build_redis_url()
        return self

    def _apply_testing_defaults(self) -> "Settings":
        if not self.database_url:
            self.database_url = "sqlite+aiosqlite:///:memory:"
        if not self.redis_url:
            self.redis_url = "redis://localhost:6379"
        return self

    def _build_database_url(self) -> None:
        if self.database_url:
            return
        if not self.db_password:
            raise ValueError(
                "Either DATABASE_URL or DB_PASSWORD must be set. "
                "For Docker: set DB_PASSWORD in .env. "
                "For local dev: set DATABASE_URL in .env."
            )
        encoded_password = quote_plus(self.db_password)
        self.database_url = (
            f"postgresql+asyncpg://{self.db_user}:{encoded_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    def _validate_secret_key(self) -> None:
        if not self.secret_key:
            raise ValueError(
                "SECRET_KEY is required. "
                'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
            )

        entropy_per_char = _estimate_entropy(self.secret_key)
        if entropy_per_char < 2.9:
            raise ValueError(
                "SECRET_KEY has insufficient entropy "
                f"(~{entropy_per_char:.1f} bits/char, need >= 2.9). "
                'Generate a secure key with: python -c "import secrets; print(secrets.token_hex(32))"'
            )
        if len(self.secret_key) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters for security")

    def _warn_cors(self) -> None:
        if self.cors_origins == "*":
            warnings.warn(
                "CORS_ORIGINS is set to '*', allowing all origins. "
                "This is insecure for production.",
                stacklevel=2,
            )

    def _resolve_storage(self) -> None:
        self.storage_path = str(Path(self.storage_path).resolve())

    def _build_redis_url(self) -> None:
        if self.redis_url:
            return
        if self.redis_password:
            encoded_password = quote_plus(self.redis_password)
            self.redis_url = f"redis://:{encoded_password}@{self.redis_host}:{self.redis_port}"
        else:
            self.redis_url = f"redis://{self.redis_host}:{self.redis_port}"


settings = Settings()
