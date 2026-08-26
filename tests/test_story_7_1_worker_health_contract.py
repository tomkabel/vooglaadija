import pytest

"""Tests for Story 7.1 worker health container contract."""

import re
from pathlib import Path

pytestmark = pytest.mark.slow



PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read_project_file(*parts: str) -> str:
    return PROJECT_ROOT.joinpath(*parts).read_text()


def _service_block(compose_text: str, service_name: str) -> str:
    pattern = rf"^  {re.escape(service_name)}:\n(?P<body>(?:    .*\n|      .*\n|        .*\n|          .*\n|          - .*\n|        - .*\n|      - .*\n|    - .*\n|^\s*$)+)"
    match = re.search(pattern, compose_text, flags=re.MULTILINE)
    assert match is not None, f"{service_name} service block not found"
    return match.group(0)


@pytest.mark.unit
def test_worker_docker_stage_exposes_health_port_and_uses_health_endpoint():
    """Worker Docker image should advertise and probe the FastAPI health endpoint."""
    dockerfile = _read_project_file("Dockerfile")
    worker_stage = dockerfile.split("FROM runtime-base AS worker", maxsplit=1)[1]

    assert "EXPOSE 8082" in worker_stage
    assert "http://localhost:8082/health" in worker_stage
    assert "HEALTHCHECK" in worker_stage
    assert "socket.socket" not in worker_stage


@pytest.mark.unit
def test_base_compose_worker_healthcheck_uses_http_health_endpoint():
    """The single compose file's worker healthcheck verifies application readiness."""
    compose = _read_project_file("docker-compose.yml")
    worker = _service_block(compose, "worker")

    assert "WORKER_HEALTH_PORT: ${WORKER_HEALTH_PORT:-8082}" in worker
    assert "http://localhost:8082/health" in worker
    assert "urllib.request.urlopen" in worker
    assert "socket.socket" not in worker


@pytest.mark.unit
def test_no_legacy_production_override_shadows_worker_healthcheck():
    """The obsolete production override file is gone; the base healthcheck is the contract."""
    assert not (PROJECT_ROOT / "docker-compose.production.yml").exists()

    compose = _read_project_file("docker-compose.yml")
    worker = _service_block(compose, "worker")

    assert "healthcheck:" in worker
    assert "socket.socket" not in worker
