import subprocess
from pathlib import Path
from urllib.parse import quote_plus

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_PLACEHOLDER_KEYS = {
    "DB_PASSWORD",
    "REDIS_PASSWORD",
    "SECRET_KEY",
    "NETDATA_CLAIM_TOKEN",
}
COMPOSE_PASSWORD_URL_PATTERNS = (
    "DATABASE_URL: postgresql+asyncpg://",
    "REDIS_URL: redis://:",
)


def _git(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def _env_example_values() -> dict[str, str]:
    values: dict[str, str] = {}

    for raw_line in (REPO_ROOT / ".env.example").read_text().splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        values[key.removeprefix("export ").strip()] = value.strip().strip("\"'")

    return values


@pytest.mark.unit
def test_env_files_have_expected_git_contract():
    """Git tracks only the committed template and explicitly ignores local env files."""
    tracked = set(_git("ls-files", ".env", ".env.local", ".env.example", ".gitignore"))
    gitignore_lines = (REPO_ROOT / ".gitignore").read_text().splitlines()

    assert ".env" not in tracked
    assert ".env.local" not in tracked
    assert ".env.example" in tracked
    assert ".gitignore" in tracked
    assert ".env" in gitignore_lines
    assert ".env.local" in gitignore_lines
    assert ".env.*" in gitignore_lines
    assert "!.env.example" in gitignore_lines


@pytest.mark.unit
def test_env_example_contains_placeholder_only_required_secrets():
    """The env template contains required secret keys as obvious placeholders only."""
    values = _env_example_values()
    missing = REQUIRED_PLACEHOLDER_KEYS - values.keys()

    assert missing == set()

    for key in REQUIRED_PLACEHOLDER_KEYS:
        value = values[key]
        assert value
        assert "change-me" in value.lower() or "replace-me" in value.lower()


@pytest.mark.unit
def test_env_example_documents_local_secret_setup():
    """The env template documents local setup without embedding real rotated values."""
    template = (REPO_ROOT / ".env.example").read_text()

    assert "cp .env.example .env" in template
    assert "secrets.token_urlsafe" in template
    assert "DB_PASSWORD" in template
    assert "REDIS_PASSWORD" in template
    assert "NETDATA_CLAIM_TOKEN" in template
    assert "Netdata Cloud" in template


@pytest.mark.unit
def test_rotated_runtime_values_preserve_auth_and_service_url_contracts(monkeypatch):
    """Synthetic rotated values build service URLs and sign newly issued JWTs."""
    from app import auth
    from core.config import Settings

    rotated_secret = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"
    db_password = "rotated db password/with spaces"
    redis_password = "rotated redis password/with spaces"

    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)

    rotated_settings = Settings(
        _env_file=None,
        database_url="",
        db_user="vooglaadija",
        db_password=db_password,
        db_name="media",
        db_host="db",
        db_port="5432",
        redis_url="",
        redis_host="redis",
        redis_port="6379",
        redis_password=redis_password,
        secret_key=rotated_secret,
    )

    assert rotated_settings.database_url == (
        f"postgresql+asyncpg://vooglaadija:{quote_plus(db_password)}@db:5432/media"
    )
    assert rotated_settings.redis_url == (f"redis://:{quote_plus(redis_password)}@redis:6379")

    monkeypatch.setattr(auth, "settings", rotated_settings)

    token = auth.create_access_token("story-6-1-user", email="operator@example.com")
    payload = auth.verify_token(token, expected_type=auth.ACCESS_TOKEN_TYPE)

    assert payload is not None
    assert payload["sub"] == "story-6-1-user"
    assert payload["email"] == "operator@example.com"


@pytest.mark.unit
def test_compose_paths_do_not_embed_rotated_passwords_in_urls():
    """Compose paths pass rotated passwords as components so Settings URL-encodes them."""
    production_compose = (REPO_ROOT / "docker-compose.production.yml").read_text()
    demo_compose = (REPO_ROOT / "docker-compose.demo.yml").read_text()

    for pattern in COMPOSE_PASSWORD_URL_PATTERNS:
        assert pattern not in production_compose
        assert pattern not in demo_compose

    assert "DB_PASSWORD: ${DB_PASSWORD:?DB_PASSWORD is required}" in demo_compose
    assert "REDIS_PASSWORD: ${REDIS_PASSWORD:?REDIS_PASSWORD is required}" in demo_compose


@pytest.mark.unit
def test_netdata_claim_contract_keeps_tokens_out_of_logs():
    """Netdata claim wiring uses plural room env and redacts token output."""
    monitoring_compose = (REPO_ROOT / "docker-compose.monitoring.yml").read_text()
    claim_script = (REPO_ROOT / "scripts/claim-netdata.sh").read_text()

    assert "NETDATA_CLAIM_ROOMS:-${NETDATA_CLAIM_ROOM:-}" in monitoring_compose
    assert "Token: <redacted>" in claim_script
    assert "-token=<redacted>" in claim_script
    assert "${CLAIM_TOKEN:0:10}" not in claim_script
    assert "DRY RUN: ${cmd[*]}" not in claim_script
