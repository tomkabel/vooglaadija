"""Application configuration shared by API and worker processes."""

import math
import os
from pathlib import Path
from urllib.parse import quote_plus, urlparse

from pydantic import ValidationInfo, field_validator, model_validator
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


_DB_POOL_ENV_NAMES = {
    "db_pool_size": "DB_POOL_SIZE",
    "db_max_overflow": "DB_MAX_OVERFLOW",
    "db_pool_timeout": "DB_POOL_TIMEOUT",
    "db_pool_recycle": "DB_POOL_RECYCLE",
}
_DB_POOL_DEFAULTS = {
    "db_pool_size": 10,
    "db_max_overflow": 5,
    "db_pool_timeout": 30,
    "db_pool_recycle": 1800,
}


def _is_testing_enabled() -> bool:
    return os.environ.get("TESTING", "").lower() in ("1", "true", "yes", "on")


class Settings(BaseSettings):
    database_url: str = ""
    secret_key: str = ""
    secret_key_previous: str = ""
    redis_url: str = ""
    cors_origins: str = "http://localhost:3000"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    file_expire_hours: int = 24
    storage_path: str = "./storage"
    bcrypt_rounds: int = 12

    # Cookie security — True for production (HTTPS), override to False for local HTTP dev
    cookie_secure: bool = True

    # Feature flags — following FEATURE_*_ENABLED convention
    feature_chaos_api_enabled: bool = False
    feature_throttle_preemptive_enabled: bool = False

    # Throttle predictor — sliding window 429 detection
    throttle_window_seconds: int = 60
    throttle_risk_threshold_scale: int = 10
    throttle_risk_threshold: float = 0.7

    # Browser downloader microservice (Phase 2 worker integration).
    # When disabled (default), the worker routes all jobs to yt-dlp,
    # matching pre-Phase-2 behavior. P4 will expand the rest of the
    # surface (image, sandbox_runtime, recording fallback).
    browser_downloader_enabled: bool = False
    browser_downloader_endpoint: str = "http://browser-downloader:3000"
    browser_downloader_timeout: int = 300
    browser_downloader_cb_use_redis: bool = False

    # Used to construct DATABASE_URL if not set directly
    db_user: str = "postgres"
    db_password: str = ""
    db_name: str = "ytprocessor"
    db_host: str = "localhost"
    db_port: str = "5432"
    db_replica_host: str = ""
    db_replica_port: str = "5432"
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800

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
        """
        Validate the application settings and construct derived configuration values.

        Applies testing defaults when testing is enabled; otherwise validates configured
        values, resolves storage, and constructs database and Redis URLs.

        Returns:
                Settings: The validated and fully constructed settings instance
        """
        if _is_testing_enabled():
            return self._apply_testing_defaults()

        self._validate_ports()
        self._validate_db_pool_settings()
        self._build_database_url()
        self._validate_secret_key()
        self._validate_cors()
        self._validate_browser_downloader()
        self._resolve_storage()
        self._build_redis_url()
        return self

    @field_validator(
        "db_pool_size",
        "db_max_overflow",
        "db_pool_timeout",
        "db_pool_recycle",
        mode="before",
    )
    @classmethod
    def _parse_db_pool_integer(cls, value: object, info: ValidationInfo) -> int:
        field_name = info.field_name
        if field_name is None:
            raise ValueError("Invalid DB pool setting: field name unavailable")
        env_name = _DB_POOL_ENV_NAMES[field_name]
        if isinstance(value, bool):
            raise ValueError(f"Invalid {env_name}: {value!r} must be an integer")
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            normalized = value.strip()
            if normalized and normalized.lstrip("+-").isdigit():
                return int(normalized)
        if _is_testing_enabled():
            return _DB_POOL_DEFAULTS[field_name]
        raise ValueError(f"Invalid {env_name}: {value!r} must be an integer")

    def _apply_testing_defaults(self) -> "Settings":
        if not self.database_url:
            self.database_url = "sqlite+aiosqlite:///:memory:"
        if not self.redis_url:
            self.redis_url = "redis://localhost:6379"
        # Tests drive the app over plain HTTP (ASGITransport), where Secure
        # cookies would be dropped. The production default stays True.
        self.cookie_secure = False
        return self

    def _validate_ports(self) -> None:
        self._validate_port("DB_PORT", self.db_port)
        self._validate_port("REDIS_PORT", self.redis_port)

    def _validate_db_pool_settings(self) -> None:
        self._validate_minimum("DB_POOL_SIZE", self.db_pool_size, 1)
        self._validate_minimum("DB_MAX_OVERFLOW", self.db_max_overflow, 0)
        self._validate_minimum("DB_POOL_TIMEOUT", self.db_pool_timeout, 1)
        self._validate_minimum("DB_POOL_RECYCLE", self.db_pool_recycle, 1)

    @staticmethod
    def _validate_minimum(name: str, value: int, minimum: int) -> None:
        if value < minimum:
            raise ValueError(f"Invalid {name}: {value!r} must be >= {minimum}")

    @staticmethod
    def _validate_port(name: str, value: str) -> None:
        try:
            port = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid {name}: {value!r} must be an integer in 1-65535") from exc

        if not 1 <= port <= 65535:
            raise ValueError(f"Invalid {name}: {value!r} must be in range 1-65535")

    def _build_database_url(self) -> None:
        """
        Build the database connection URL when one has not been provided.

        Raises:
            ValueError: If no database URL or database password is configured.
        """
        if self.database_url:
            return
        if not self.db_password:
            raise ValueError(
                "Either DATABASE_URL or DB_PASSWORD must be set. "
                "For Docker: set DB_PASSWORD in .env. "
                "For local dev: set DATABASE_URL in .env.",
            )
        encoded_password = quote_plus(self.db_password)
        self.database_url = (
            f"postgresql+asyncpg://{self.db_user}:{encoded_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def database_replica_url(self) -> str:
        """Build the read replica URL from configured replica host."""
        if not self.db_replica_host:
            return self.database_url
        encoded_password = quote_plus(self.db_password)
        return (
            f"postgresql+asyncpg://{self.db_user}:{encoded_password}"
            f"@{self.db_replica_host}:{self.db_replica_port}/{self.db_name}"
        )
        encoded_password = quote_plus(self.db_password)
        self.database_url = (
            f"postgresql+asyncpg://{self.db_user}:{encoded_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    def _validate_secret_key(self) -> None:
        """
        Validate that the configured secret key is present, sufficiently long, and has adequate entropy.

        Raises:
            ValueError: If the secret key is missing, shorter than 32 characters, or has insufficient entropy.
        """
        if not self.secret_key:
            raise ValueError(
                "SECRET_KEY is required. "
                'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"',
            )

        if len(self.secret_key) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters for security")

        entropy_per_char = _estimate_entropy(self.secret_key)
        if entropy_per_char < 2.9:
            raise ValueError(
                "SECRET_KEY has insufficient entropy "
                f"(~{entropy_per_char:.1f} bits/char, need >= 2.9). "
                'Generate a secure key with: python -c "import secrets; print(secrets.token_hex(32))"',
            )

    def _validate_cors(self) -> None:
        """
        Validate and normalize the configured CORS origins.

        Raises:
            ValueError: If wildcard origins are configured or an origin is invalid.
        """
        if self.cors_origins == "*":
            raise ValueError("CORS_ORIGINS cannot be '*' when credentialed requests are enabled")

        origins = [origin.strip() for origin in self.cors_origins.split(",")]
        for origin in origins:
            self._validate_cors_origin(origin)
        self.cors_origins = ",".join(origins)

    @staticmethod
    def _validate_cors_origin(origin: str) -> None:
        try:
            parsed = urlparse(origin)
            port = parsed.port
        except ValueError as exc:
            raise ValueError(f"Invalid CORS origin: {origin!r}") from exc

        if (
            not origin
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.params
            or parsed.query
            or parsed.fragment
            or any(char.isspace() for char in origin)
            or port == 0
        ):
            raise ValueError(f"Invalid CORS origin: {origin!r}")

    def _resolve_storage(self) -> None:
        path = Path(self.storage_path).expanduser().resolve()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"Storage path not writable: {path}") from exc

        if not os.access(path, os.W_OK):
            raise ValueError(f"Storage path not writable: {path}")

        self.storage_path = str(path)

    def _build_redis_url(self) -> None:
        """
        Construct the Redis connection URL from the configured host, port, and optional password.

        The existing Redis URL is preserved when provided.
        """
        if self.redis_url:
            return
        if self.redis_password:
            encoded_password = quote_plus(self.redis_password)
            self.redis_url = f"redis://:{encoded_password}@{self.redis_host}:{self.redis_port}"
        else:
            self.redis_url = f"redis://{self.redis_host}:{self.redis_port}"

    def _validate_browser_downloader(self) -> None:
        """
        Validate the browser downloader timeout and endpoint configuration.

        Raises:
                ValueError: If the timeout is less than one or the endpoint is missing or is not an HTTP(S) URL with a host.
        """
        if self.browser_downloader_timeout < 1:
            raise ValueError(
                f"Invalid BROWSER_DOWNLOADER_TIMEOUT: {self.browser_downloader_timeout!r} must be >= 1",
            )
        if not self.browser_downloader_endpoint:
            raise ValueError("BROWSER_DOWNLOADER_ENDPOINT must be a non-empty URL")
        try:
            parsed = urlparse(self.browser_downloader_endpoint)
            # parsed.netloc is a shallow check: "http://:8080" has a truthy
            # netloc but no host. Require an actual hostname instead.
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("must be an http(s) URL with a host")
            # Force evaluation of the port: urlparse defers port parsing, so a
            # malformed port (e.g. "http://host:not-a-port") only raises here.
            _ = parsed.port
        except ValueError as exc:
            raise ValueError(
                f"Invalid BROWSER_DOWNLOADER_ENDPOINT: {self.browser_downloader_endpoint!r}",
            ) from exc


settings = Settings()
