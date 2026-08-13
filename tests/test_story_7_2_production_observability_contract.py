"""Tests for Story 7.2 production observability deployment contracts."""

import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from prometheus_client import CONTENT_TYPE_LATEST

from app.main import app
from worker.health import health_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read_project_file(*parts: str) -> str:
    return PROJECT_ROOT.joinpath(*parts).read_text()


def _service_block(compose_text: str, service_name: str) -> str:
    pattern = (
        rf"^  {re.escape(service_name)}:\n"
        rf"(?P<body>(?:    .*\n|      .*\n|        .*\n|          .*\n|"
        rf"          - .*\n|        - .*\n|      - .*\n|    - .*\n|^\s*$)+)"
    )
    match = re.search(pattern, compose_text, flags=re.MULTILINE)
    assert match is not None, f"{service_name} service block not found"
    return match.group(0)


def _active_env_example_values() -> dict[str, str]:
    values: dict[str, str] = {}

    for raw_line in _read_project_file(".env.example").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", maxsplit=1)
        values[key.removeprefix("export ").strip()] = value.strip().strip("\"'")

    return values


@pytest.mark.unit
def test_compose_explicitly_enables_metrics_and_tracing():
    """The compose file should make observability enablement an explicit contract."""
    compose = _read_project_file("docker-compose.yml")

    assert "FEATURE_METRICS_ENABLED: ${FEATURE_METRICS_ENABLED:-true}" in compose
    assert "FEATURE_TRACING_ENABLED: ${FEATURE_TRACING_ENABLED:-true}" in compose
    assert "CORS_ORIGINS: ${CORS_ORIGINS:-" in compose
    assert "COOKIE_SECURE: ${COOKIE_SECURE:-False}" in compose
    assert "DATABASE_URL: postgresql+asyncpg://" not in compose
    assert "REDIS_URL: redis://:" not in compose


@pytest.mark.unit
def test_compose_keeps_otel_collector_enabled():
    """The compose stack keeps the OTel collector service enabled for production."""
    compose = _read_project_file("docker-compose.yml")

    assert "otel-collector:" in compose
    assert "swagger-ui:" not in compose
    assert "nonprod" not in compose


@pytest.mark.unit
def test_base_compose_defines_otel_collector_without_nonprod_profile():
    """Base compose should provide the collector service used by production merges."""
    base_compose = _read_project_file("docker-compose.yml")
    otel_collector = _service_block(base_compose, "otel-collector")

    assert "otel/opentelemetry-collector:0.88.0" in otel_collector
    assert "--config=/etc/otel-collector-config.yml" in otel_collector
    assert "otel-collector" in otel_collector
    assert "profiles:" not in otel_collector


@pytest.mark.unit
def test_env_example_matches_observability_defaults_from_compose():
    """Operator env template should document the same safe observability defaults."""
    base_compose = _read_project_file("docker-compose.yml")
    values = _active_env_example_values()

    expected_defaults = {
        "FEATURE_METRICS_ENABLED": "true",
        "FEATURE_TRACING_ENABLED": "true",
        "FEATURE_CHAOS_API_ENABLED": "false",
        "FEATURE_THROTTLE_PREEMPTIVE_ENABLED": "false",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector:4317",
        "OTEL_SERVICE_NAME": "vooglaadija",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://otel-collector:4317/v1/traces",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "http://otel-collector:4317/v1/metrics",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "http://otel-collector:4317/v1/logs",
    }

    for key, expected_value in expected_defaults.items():
        assert values[key] == expected_value
        assert f"{key}: ${{{key}:-{expected_value}}}" in base_compose


@pytest.mark.unit
def test_prometheus_scrape_contract_reaches_production_metrics_endpoints():
    """Production smoke contract should target API and worker metrics endpoints."""
    prometheus_config = _read_project_file("infra", "prometheus", "prometheus.yml")

    assert "job_name: 'ytprocessor-api'" in prometheus_config
    assert "metrics_path: /prometheus-metrics" in prometheus_config
    assert "targets: ['api:8000']" in prometheus_config
    assert "job_name: 'ytprocessor-worker'" in prometheus_config
    assert "metrics_path: /metrics" in prometheus_config
    assert "targets: ['worker:8082']" in prometheus_config


@pytest.mark.unit
@pytest.mark.asyncio
async def test_production_metrics_smoke_endpoints_are_reachable(monkeypatch: pytest.MonkeyPatch):
    """Production-mode API and worker scrape endpoints should return Prometheus output."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("FEATURE_METRICS_ENABLED", "true")
    monkeypatch.setenv("FEATURE_TRACING_ENABLED", "true")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://api") as client:
        api_response = await client.get("/prometheus-metrics")

    async with AsyncClient(
        transport=ASGITransport(app=health_app), base_url="http://worker:8082"
    ) as client:
        worker_response = await client.get("/metrics")

    for response in (api_response, worker_response):
        assert response.status_code == 200
        assert response.headers["content-type"] == CONTENT_TYPE_LATEST
        assert "# HELP" in response.text
        assert "# TYPE" in response.text
