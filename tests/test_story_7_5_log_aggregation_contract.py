"""Tests for Story 7.5 log aggregation contracts (json-file rotation)."""

import json
import logging
from pathlib import Path
from typing import Any

import pytest
import structlog
import structlog.contextvars
import yaml

from core.logging_config import configure_logging, get_logger

pytestmark = pytest.mark.slow


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# All compose services use Docker's json-file driver with rotation.
BASE_COMPOSE_SERVICES = {
    "storage-init",
    "api",
    "worker",
    "browser-downloader",
    "db",
    "redis",
    "otel-collector",
    "prometheus",
    "grafana",
    "backup",
    "backup-cron",
}

LOG_DRIVER = "json-file"
LOG_MAX_SIZE = "10m"
LOG_MAX_FILES = "3"

STRUCTLOG_JSON_SERVICES = {
    "api",
    "worker",
}


class ComposeLoader(yaml.SafeLoader):
    """YAML loader that understands Docker Compose custom tags used in this repo."""


def _read_project_file(*parts: str) -> str:
    return PROJECT_ROOT.joinpath(*parts).read_text()


def _load_yaml_file(*parts: str) -> dict[str, Any]:
    return yaml.load(_read_project_file(*parts), Loader=ComposeLoader)


@pytest.mark.unit
def test_base_compose_services_exist_and_use_json_file_rotation():
    """Every service defines json-file logging with rotation (no host plugin needed)."""
    config = _load_yaml_file("docker-compose.yml")
    services = config["services"]

    assert set(services) == BASE_COMPOSE_SERVICES
    for service_name, service_config in services.items():
        logging_config = service_config.get("logging")
        assert logging_config is not None, f"{service_name} must define logging"
        assert logging_config["driver"] == LOG_DRIVER
        options = logging_config["options"]
        assert str(options["max-size"]) == LOG_MAX_SIZE
        assert str(options["max-file"]) == LOG_MAX_FILES


@pytest.mark.unit
def test_compose_has_no_loki_logging_driver_or_loki_backend():
    """Loki logging was removed; nothing references the loki driver or backend."""
    compose = _read_project_file("docker-compose.yml")

    assert "logging.driver: 'loki'" not in compose
    assert "driver: 'loki'" not in compose
    assert "grafana/loki" not in compose
    assert "infra/loki" not in compose
    assert "loki" not in _load_yaml_file("docker-compose.yml")["services"]


@pytest.mark.unit
def test_no_custom_networks_for_coolify_compatibility():
    """Compose must not define custom networks (Coolify manages the network)."""
    config = _load_yaml_file("docker-compose.yml")

    assert "networks" not in config
    for service_name, service_config in config["services"].items():
        assert "networks" not in service_config, f"{service_name} must not define networks"


@pytest.mark.unit
def test_api_and_worker_are_registry_first():
    """API/worker pull prebuilt GHCR images; no build section in the base compose."""
    services = _load_yaml_file("docker-compose.yml")["services"]

    assert services["api"]["image"] == "ghcr.io/tomkabel/vooglaadija:${IMAGE_TAG:-latest}"
    assert services["worker"]["image"] == "ghcr.io/tomkabel/vooglaadija:worker-${IMAGE_TAG:-latest}"
    assert "build" not in services["api"]
    assert "build" not in services["worker"]


@pytest.mark.unit
def test_local_override_builds_and_exposes_debug_ports():
    """The local override adds build targets and loopback-only debug ports."""
    config = _load_yaml_file("docker-compose.local.yml")
    services = config["services"]

    assert services["api"]["build"]["target"] == "api"
    assert services["worker"]["build"]["target"] == "worker"
    assert "127.0.0.1:8000:8000" in services["api"]["ports"]
    assert "127.0.0.1:5432:5432" in services["db"]["ports"]


@pytest.mark.unit
def test_application_services_receive_production_environment_variable():
    """API and worker containers should default to production environment."""
    services = _load_yaml_file("docker-compose.yml")["services"]

    for service_name in STRUCTLOG_JSON_SERVICES:
        assert services[service_name]["environment"]["ENVIRONMENT"] == (
            "${ENVIRONMENT:-production}"
        )


@pytest.mark.unit
def test_grafana_datasource_provisions_prometheus_only():
    """Grafana provisioning keeps Prometheus as the default datasource (Loki removed)."""
    config = _load_yaml_file("infra", "grafana", "datasource.yml")
    datasources = {datasource["name"]: datasource for datasource in config["datasources"]}

    assert set(datasources) == {"Prometheus"}
    assert datasources["Prometheus"]["type"] == "prometheus"
    assert datasources["Prometheus"]["isDefault"] is True


@pytest.mark.unit
def test_ops_docs_cover_log_setup_and_troubleshooting():
    """Operator docs should explain the logging setup and troubleshooting."""
    ops = _read_project_file("docs", "OPS.md")

    assert "json-file" in ops
    assert "max-size" in ops
    assert "no host plugin required" in ops
    assert "docker compose logs" in ops


@pytest.mark.unit
def test_production_structlog_output_remains_queryable_json(
    monkeypatch: pytest.MonkeyPatch, capsys
):
    """Production logs should remain JSON with stable aggregation fields."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()

    try:
        configure_logging(log_level="INFO")
        structlog.contextvars.bind_contextvars(request_id="req-story-7-5")
        logger = get_logger("story_7_5_contract")

        logger.info("download_ready", job_id="job-123")

        captured = capsys.readouterr().out.strip().splitlines()
        payload = json.loads(captured[-1])

        assert payload["message"] == "download_ready"
        assert payload["job_id"] == "job-123"
        assert payload["request_id"] == "req-story-7-5"
        assert payload["service"] == "vooglaadija"
        assert payload["level"] == "info"
        assert payload["logger"] == "story_7_5_contract"
        assert payload["timestamp"]
        assert "environment" in payload
    finally:
        structlog.contextvars.clear_contextvars()
        logging.getLogger().handlers.clear()
