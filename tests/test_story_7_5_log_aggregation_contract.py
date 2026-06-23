"""Tests for Story 7.5 centralized log aggregation contracts."""

import json
import logging
from pathlib import Path
from typing import Any

import pytest
import structlog
import structlog.contextvars
import yaml

from core.logging_config import configure_logging, get_logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]

LOKI_DRIVER = "loki"
LOKI_PUSH_URL = "http://localhost:3100/loki/api/v1/push"

# Base compose services — Loki logging was moved to override files only.
# These services use Docker's default logging driver (json-file).
BASE_COMPOSE_SERVICES = {
    "api",
    "worker",
    "db",
    "redis",
    "otel-collector",
    "nginx",
    "swagger-ui",
    "loki",
}

# Services that override files (production, demo) add Loki logging.
PRODUCTION_OVERRIDE_LOGGED_SERVICES = {
    "storage-init",
    "nginx",
    "certbot",
}

DEMO_OVERRIDE_LOGGED_SERVICES = {
    "seed-demo-data",
    "prometheus",
    "grafana",
}

MONITORING_LOGGED_SERVICES = {
    "netdata-api",
    "netdata-worker",
    "netdata-db",
    "netdata-redis",
}

STRUCTLOG_JSON_SERVICES = {
    "api",
    "worker",
}


class ComposeLoader(yaml.SafeLoader):
    """YAML loader that understands Docker Compose custom tags used in this repo."""


def _compose_override(loader: ComposeLoader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


ComposeLoader.add_constructor("!override", _compose_override)


def _read_project_file(*parts: str) -> str:
    return PROJECT_ROOT.joinpath(*parts).read_text()


def _load_yaml_file(*parts: str) -> dict[str, Any]:
    return yaml.load(_read_project_file(*parts), Loader=ComposeLoader)


def _external_labels(options: dict[str, Any]) -> dict[str, str]:
    return dict(
        label.split("=", maxsplit=1) for label in options["loki-external-labels"].split(",")
    )


def _assert_loki_logging_contract(
    service_name: str,
    service_config: dict[str, Any],
    *,
    expected_service_label: str | None = None,
) -> None:
    logging_config = service_config.get("logging")

    assert logging_config is not None, f"{service_name} must define logging"
    assert logging_config["driver"] == LOKI_DRIVER

    options = logging_config["options"]
    assert options["loki-url"] == LOKI_PUSH_URL
    assert "max-size" not in options
    assert "max-file" not in options
    assert "compress" not in options

    labels = _external_labels(options)
    assert labels["service"] == (expected_service_label or service_name)
    assert labels["container_name"]
    assert labels["environment"] == "${ENVIRONMENT:-production}"
    assert labels["project"] == "ytprocessor"


@pytest.mark.unit
def test_base_compose_services_exist_and_use_default_logging():
    """Base compose services exist and use Docker's default logging driver (no explicit logging config)."""
    config = _load_yaml_file("docker-compose.yml")
    services = config["services"]

    assert set(services) == BASE_COMPOSE_SERVICES
    for service_name in services:
        service_config = services[service_name]
        # No explicit logging config — Docker's default json-file driver is used
        assert "logging" not in service_config, (
            f"{service_name} should not define explicit logging in base compose"
        )


@pytest.mark.unit
def test_override_only_services_define_loki_logging_contracts():
    """Override-only services should not fall back to Docker's default logging driver."""
    production_services = _load_yaml_file("docker-compose.production.yml")["services"]
    demo_services = _load_yaml_file("docker-compose.demo.yml")["services"]
    production_services_with_logging = {
        service_name
        for service_name, service_config in production_services.items()
        if "logging" in service_config
    }
    demo_services_with_logging = {
        service_name
        for service_name, service_config in demo_services.items()
        if "logging" in service_config
    }

    assert production_services_with_logging == PRODUCTION_OVERRIDE_LOGGED_SERVICES
    assert demo_services_with_logging == DEMO_OVERRIDE_LOGGED_SERVICES
    for service_name in PRODUCTION_OVERRIDE_LOGGED_SERVICES:
        _assert_loki_logging_contract(service_name, production_services[service_name])

    for service_name in DEMO_OVERRIDE_LOGGED_SERVICES:
        _assert_loki_logging_contract(service_name, demo_services[service_name])


@pytest.mark.unit
def test_monitoring_compose_netdata_services_use_loki_logging_driver():
    """Monitoring compose services should keep service-specific Loki labels."""
    monitoring_config = _load_yaml_file("docker-compose.monitoring.yml")
    monitoring_services = monitoring_config["services"]

    assert set(monitoring_services) == MONITORING_LOGGED_SERVICES
    for service_name in MONITORING_LOGGED_SERVICES:
        _assert_loki_logging_contract(service_name, monitoring_services[service_name])


@pytest.mark.unit
def test_loki_backend_service_config_and_volume_exist():
    """Compose should define Loki with persistent storage and read-only config."""
    config = _load_yaml_file("docker-compose.yml")
    loki = config["services"]["loki"]

    assert loki["image"] == "grafana/loki:3.0.0"
    assert "./infra/loki/loki.yml:/etc/loki/loki.yml:ro" in loki["volumes"]
    assert "loki_data:/loki" in loki["volumes"]
    assert "3100:3100" in loki["ports"]
    assert "loki_data" in config["volumes"]
    assert "ytprocessor-network" in loki["networks"]

    loki_config = _load_yaml_file("infra", "loki", "loki.yml")
    assert loki_config["limits_config"]["retention_period"] == "168h"
    assert loki_config["limits_config"]["max_query_lookback"] == "168h"
    assert loki_config["compactor"]["retention_enabled"] is True
    assert loki_config["storage_config"]["filesystem"]["directory"] == "/loki/chunks"


@pytest.mark.unit
def test_application_services_receive_production_environment_variable():
    """API and worker containers should default to production environment."""
    base_services = _load_yaml_file("docker-compose.yml")["services"]
    demo_services = _load_yaml_file("docker-compose.demo.yml")["services"]

    for service_name in STRUCTLOG_JSON_SERVICES:
        assert base_services[service_name]["environment"]["ENVIRONMENT"] == (
            "${ENVIRONMENT:-production}"
        )

    assert demo_services["seed-demo-data"]["environment"]["ENVIRONMENT"] == (
        "${ENVIRONMENT:-production}"
    )


@pytest.mark.unit
def test_grafana_datasource_provisions_prometheus_and_loki():
    """Grafana provisioning should keep Prometheus default and add Loki."""
    config = _load_yaml_file("infra", "grafana", "datasource.yml")
    datasources = {datasource["name"]: datasource for datasource in config["datasources"]}

    assert datasources["Prometheus"]["type"] == "prometheus"
    assert datasources["Prometheus"]["isDefault"] is True
    assert datasources["Loki"]["type"] == "loki"
    assert datasources["Loki"]["url"] == "http://loki:3100"
    assert datasources["Loki"]["isDefault"] is False


@pytest.mark.unit
def test_ops_docs_cover_log_setup_retention_queries_and_rollback():
    """Operator docs should explain setup, retention, queries, and rollback."""
    ops = _read_project_file("docs", "OPS.md")

    assert "Loki Docker logging plugin" in ops
    assert "docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d" in ops
    assert "7 days" in ops
    assert "168h" in ops
    assert '{service="api"} | json' in ops
    assert 'app_service="service"' in ops
    assert "rollback" in ops.lower()


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
