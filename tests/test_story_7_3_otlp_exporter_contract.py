"""Tests for Story 7.3 OTel Collector OTLP exporter contracts."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read_project_file(*parts: str) -> str:
    return PROJECT_ROOT.joinpath(*parts).read_text()


def _collector_config() -> dict[str, Any]:
    return yaml.safe_load(_read_project_file("infra", "otel-collector-config.yml"))


def _base_compose_config() -> dict[str, Any]:
    return yaml.safe_load(_read_project_file("docker-compose.yml"))


def _active_env_example_values() -> dict[str, str]:
    values: dict[str, str] = {}

    for raw_line in _read_project_file(".env.example").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", maxsplit=1)
        values[key.removeprefix("export ").strip()] = value.strip().strip("\"'")

    return values


def _compose_default(expansion: str, variable_name: str) -> str:
    pattern = rf"^\$\{{{re.escape(variable_name)}:-(?P<default>[^}}]+)\}}$"
    match = re.fullmatch(pattern, expansion)
    assert match is not None, f"{variable_name} default expansion not found"
    return match.group("default")


@pytest.mark.unit
def test_collector_defines_active_otlp_and_debug_exporters():
    """Collector config should define active OTLP and non-verbose debug exporters."""
    exporters = _collector_config()["exporters"]

    assert set(exporters) == {"debug", "otlp"}
    assert exporters["otlp"]["endpoint"] == "${env:OTEL_EXPORTER_OTLP_ENDPOINT}"
    assert ":-" not in exporters["otlp"]["endpoint"]
    assert exporters["otlp"]["tls"]["insecure"] == "${env:OTEL_EXPORTER_OTLP_INSECURE}"
    assert exporters["debug"].get("verbosity") != "detailed"
    assert exporters["debug"]["sampling_initial"] <= 5
    assert exporters["debug"]["sampling_thereafter"] >= 200


@pytest.mark.unit
def test_collector_traces_and_metrics_flow_to_otlp_before_debug():
    """Traces and metrics should flow from OTLP receiver through batch to OTLP export."""
    pipelines = _collector_config()["service"]["pipelines"]

    for pipeline_name in ("traces", "metrics"):
        pipeline = pipelines[pipeline_name]

        assert pipeline["receivers"] == ["otlp"]
        assert pipeline["processors"] == ["batch"]
        assert pipeline["exporters"] == ["otlp", "debug"]


@pytest.mark.unit
def test_collector_logs_pipeline_remains_debug_only_for_later_story():
    """Logs should remain startable without claiming Story 7.5 log aggregation."""
    logs_pipeline = _collector_config()["service"]["pipelines"]["logs"]

    assert logs_pipeline["receivers"] == ["otlp"]
    assert logs_pipeline["processors"] == ["batch"]
    assert logs_pipeline["exporters"] == ["debug"]


@pytest.mark.unit
def test_compose_and_env_document_otlp_endpoint_contract():
    """Compose and env examples should expose endpoint and TLS knobs without secret URLs."""
    base_compose = _read_project_file("docker-compose.yml")
    compose_config = _base_compose_config()
    common_env = compose_config["x-common-env"]
    otel_collector = compose_config["services"]["otel-collector"]
    collector_env = otel_collector["environment"]
    env_values = _active_env_example_values()

    assert env_values["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://otel-collector:4317"
    assert env_values["OTEL_EXPORTER_OTLP_INSECURE"] == "false"
    assert (
        common_env["OTEL_EXPORTER_OTLP_ENDPOINT"]
        == "${OTEL_EXPORTER_OTLP_ENDPOINT:-http://otel-collector:4317}"
    )
    assert (
        collector_env["OTEL_EXPORTER_OTLP_ENDPOINT"]
        == "${OTEL_EXPORTER_OTLP_ENDPOINT:-localhost:4317}"
    )
    assert collector_env["OTEL_EXPORTER_OTLP_INSECURE"] == "${OTEL_EXPORTER_OTLP_INSECURE:-false}"
    # Collector ports are exposed internally (no host binding; proxy/CI use local override)
    assert "4317" in otel_collector["expose"]
    assert "4318" in otel_collector["expose"]
    assert "ports" not in otel_collector
    assert "DATABASE_URL: postgresql+asyncpg://" not in base_compose
    assert "REDIS_URL: redis://:" not in base_compose


@pytest.mark.unit
def test_collector_exporter_fallback_is_grpc_authority_not_in_stack_receiver_url():
    """Collector's local fallback should not silently self-export to the compose receiver URL."""
    collector_env = _base_compose_config()["services"]["otel-collector"]["environment"]
    fallback = _compose_default(
        collector_env["OTEL_EXPORTER_OTLP_ENDPOINT"], "OTEL_EXPORTER_OTLP_ENDPOINT"
    )

    assert re.fullmatch(r"[A-Za-z0-9_.-]+:\d+", fallback)
    assert fallback == "localhost:4317"
    assert fallback != _active_env_example_values()["OTEL_EXPORTER_OTLP_ENDPOINT"]
