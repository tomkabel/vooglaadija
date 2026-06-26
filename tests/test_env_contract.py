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
PRODUCTION_DOMAIN_LITERAL = "youtube.tomabel.ee"


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

    token = auth.create_access_token("story-6-1-user")
    payload = auth.verify_token(token, expected_type=auth.ACCESS_TOKEN_TYPE)

    assert payload is not None
    assert payload["sub"] == "story-6-1-user"


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
def test_production_deploy_domain_is_parameterized():
    """Production deploy files use DEPLOY_DOMAIN rather than a fixed hostname."""
    files = [
        ".env.example",
        ".github/workflows/deploy-production.yml",
        "docker-compose.production.yml",
        "infra/deploy/deploy.sh",
        "infra/deploy/README.md",
        "infra/nginx/nginx.production.conf",
        "infra/ssl/README.md",
    ]

    for relative_path in files:
        assert PRODUCTION_DOMAIN_LITERAL not in (REPO_ROOT / relative_path).read_text()

    env_example = (REPO_ROOT / ".env.example").read_text()
    production_compose = (REPO_ROOT / "docker-compose.production.yml").read_text()
    nginx_template = (REPO_ROOT / "infra/nginx/nginx.production.conf").read_text()
    deploy_script = (REPO_ROOT / "infra/deploy/deploy.sh").read_text()

    assert "DEPLOY_DOMAIN=example.com" in env_example
    assert (
        "CORS_ORIGINS: 'https://${DEPLOY_DOMAIN:?DEPLOY_DOMAIN is required}'" in production_compose
    )
    assert "DEPLOY_DOMAIN: '${DEPLOY_DOMAIN:?DEPLOY_DOMAIN is required}'" in production_compose
    assert "server_name ${DEPLOY_DOMAIN};" in nginx_template
    assert "/etc/letsencrypt/live/${DEPLOY_DOMAIN}/fullchain.pem" in nginx_template
    assert ': "${DEPLOY_DOMAIN:?DEPLOY_DOMAIN is required}"' in deploy_script
    assert 'DOMAIN="$DEPLOY_DOMAIN"' in deploy_script


@pytest.mark.unit
def test_remote_deploy_uses_secret_files_not_ssh_env_payloads():
    """Production deploy passes secret material through files, not SSH env args."""
    workflow = (REPO_ROOT / ".github/workflows/deploy-production.yml").read_text()
    remote_script = (REPO_ROOT / "infra/deploy/remote-deploy.sh").read_text()

    assert "GHCR_PAT=.*bash -s" not in workflow
    assert "ENV_B64=.*bash -s" not in workflow
    assert "GHCR_PAT_FILE" in workflow
    assert "ENV_FILE_PATH" in workflow
    assert ': "${GHCR_PAT_FILE:?GHCR_PAT_FILE is required}"' in remote_script
    assert ': "${ENV_FILE_PATH:?ENV_FILE_PATH is required}"' in remote_script
    assert (
        'docker login "$GHCR_REGISTRY" -u "$GHCR_OWNER" --password-stdin < "$GHCR_PAT_FILE"'
        in remote_script
    )
    assert "printf '%s' \"$ENV_B64\"" not in remote_script


@pytest.mark.unit
def test_remote_deploy_verifies_rollback_images_before_restore():
    """Rollback verifies captured backup image tags before changing services."""
    remote_script = (REPO_ROOT / "infra/deploy/remote-deploy.sh").read_text()

    assert 'docker manifest inspect "$image"' in remote_script
    assert "verify_backup_images" in remote_script
    assert "Backup image is unavailable" in remote_script


@pytest.mark.unit
def test_deploy_health_gates_require_healthy_payloads():
    """Deploy-time health checks must inspect the JSON status, not only HTTP 200."""
    workflow = (REPO_ROOT / ".github/workflows/deploy-production.yml").read_text()
    remote_script = (REPO_ROOT / "infra/deploy/remote-deploy.sh").read_text()

    healthy_pattern = r'"status"[[:space:]]*:[[:space:]]*"healthy"'

    assert healthy_pattern in workflow
    assert "health_endpoint_is_healthy" in remote_script
    assert healthy_pattern in remote_script


@pytest.mark.unit
def test_fast_forward_workflow_ignores_bot_comments():
    """Fast-forward workflow should not run for bot-authored issue comments."""
    workflow = (REPO_ROOT / ".github/workflows/fast-forward.yml").read_text()

    assert "github.event.comment.user.type != 'Bot'" in workflow


@pytest.mark.unit
def test_pydantic_settings_dependency_stays_on_patched_floor():
    """Dependency metadata keeps pydantic-settings on the patched minimum release."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text()
    lockfile = (REPO_ROOT / "uv.lock").read_text()

    assert '"pydantic-settings>=2.14.2"' in pyproject
    # Scope the regex to the pydantic-settings package block so we don't
    # accidentally match a different package's version.
    import re

    marker = r'name = "pydantic-settings"\nversion = "([^"]+)"'
    lock_version_match = re.search(marker, lockfile)
    assert lock_version_match is not None, "pydantic-settings version not found in lockfile"
    lock_version = tuple(int(x) for x in lock_version_match.group(1).split("."))
    floor_version = (2, 14, 2)
    assert lock_version >= floor_version, (
        f"pydantic-settings {lock_version_match.group(1)} in lockfile is below "
        f"the minimum floor {'.'.join(str(x) for x in floor_version)}"
    )


@pytest.mark.unit
def test_production_certbot_no_longer_mounts_docker_socket():
    """Certbot renewal does not require a writable Docker socket."""
    production_compose = (REPO_ROOT / "docker-compose.production.yml").read_text()
    certbot_compose = (REPO_ROOT / "infra/certbot/docker-compose.certbot.yml").read_text()

    assert "/var/run/docker.sock" not in production_compose
    assert "/var/run/docker.sock" not in certbot_compose
    assert "docker exec ytprocessor-nginx" not in production_compose
    assert "docker exec ytprocessor-nginx" not in certbot_compose


@pytest.mark.unit
def test_production_nginx_consumes_certbot_reload_marker():
    """Production nginx should watch the shared certbot reload marker and reload itself."""
    production_compose = (REPO_ROOT / "docker-compose.production.yml").read_text()

    assert ".nginx-reload-required" in production_compose
    assert "nginx -s reload" in production_compose


@pytest.mark.unit
def test_dockerfile_verifies_swagger_assets_before_installing_them():
    """Dockerfile verifies downloaded Swagger assets with SHA-384 checksums."""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()

    assert "sha384sum -c" in dockerfile
    assert "swagger-ui-bundle.js" in dockerfile
    assert "swagger-ui.css" in dockerfile


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
