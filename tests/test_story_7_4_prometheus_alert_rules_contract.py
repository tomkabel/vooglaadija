"""Tests for Story 7.4 Prometheus alerting rule contracts."""

from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_ALERTS = {
    "HighErrorRate": "> 0.05",
    "HighLatency": "> 2",
    "WorkerBacklog": "> 100",
    "DBConnectionPoolExhaustion": "< 2",
    "RedisMemoryPressure": "> 0.9",
}

EXPECTED_FOR_DURATIONS = {
    "HighErrorRate": "5m",
    "HighLatency": "5m",
    "WorkerBacklog": "10m",
    "DBConnectionPoolExhaustion": "1m",
    "RedisMemoryPressure": "5m",
}

LEGACY_METRIC_NAMES = (
    "download_jobs_total",
    "download_queue_depth",
)


def _read_project_file(*parts: str) -> str:
    return PROJECT_ROOT.joinpath(*parts).read_text()


def _load_yaml_file(*parts: str) -> dict[str, Any]:
    return yaml.safe_load(_read_project_file(*parts))


def _alert_rules() -> list[dict[str, Any]]:
    config = _load_yaml_file("infra", "prometheus", "alerts.yml")
    groups = config["groups"]

    assert len(groups) == 1
    assert groups[0]["name"] == "vooglaadija"

    return groups[0]["rules"]


@pytest.mark.unit
def test_alerts_file_defines_exactly_required_story_7_4_alerts():
    """Alert rules should define exactly the five production-risk alerts."""
    rules = _alert_rules()

    assert {rule["alert"] for rule in rules} == set(REQUIRED_ALERTS)


@pytest.mark.unit
def test_alerts_use_expected_threshold_signals_and_durations():
    """Alert expressions should keep the required threshold contracts and anti-flap windows."""
    rules_by_name = {rule["alert"]: rule for rule in _alert_rules()}

    for alert_name, threshold in REQUIRED_ALERTS.items():
        rule = rules_by_name[alert_name]

        assert threshold in rule["expr"]
        assert rule["for"] == EXPECTED_FOR_DURATIONS[alert_name]


@pytest.mark.unit
def test_alerts_use_current_metric_contracts_and_guarded_expressions():
    """Alert expressions should use current metrics and guard division-based rules."""
    rules_by_name = {rule["alert"]: rule for rule in _alert_rules()}
    all_expressions = "\n".join(rule["expr"] for rule in rules_by_name.values())

    for legacy_metric_name in LEGACY_METRIC_NAMES:
        assert legacy_metric_name not in all_expressions

    high_error_rate_expr = rules_by_name["HighErrorRate"]["expr"]
    assert 'ytprocessor_jobs_completed_total{status="failed"}' in high_error_rate_expr
    assert "sum(rate(ytprocessor_jobs_completed_total[5m])) > 0" in high_error_rate_expr

    high_latency_expr = rules_by_name["HighLatency"]["expr"]
    assert "histogram_quantile(" in high_latency_expr
    assert "0.95" in high_latency_expr
    assert "ytprocessor_http_request_duration_seconds_bucket" in high_latency_expr
    assert "ytprocessor_http_request_duration_seconds_count" in high_latency_expr
    assert "sum(rate(ytprocessor_http_request_duration_seconds_count[5m])) > 0" in (
        high_latency_expr
    )

    assert rules_by_name["WorkerBacklog"]["expr"] == "ytprocessor_queue_depth > 100"
    assert rules_by_name["DBConnectionPoolExhaustion"]["expr"] == "db_connection_pool_available < 2"

    redis_expr = rules_by_name["RedisMemoryPressure"]["expr"]
    assert "redis_memory_used_bytes / redis_memory_max_bytes" in redis_expr
    assert "redis_memory_max_bytes > 0" in redis_expr


@pytest.mark.unit
def test_alerts_document_external_metric_dependencies():
    """DB and Redis alerts should document inactive source metric dependencies."""
    alerts_text = _read_project_file("infra", "prometheus", "alerts.yml")

    assert "# TODO: db_connection_pool_available is the ADR-10 alerting contract." in alerts_text
    assert (
        "Enable the source metric from app instrumentation or a PostgreSQL exporter" in alerts_text
    )
    assert (
        "# TODO: redis_memory_used_bytes and redis_memory_max_bytes require an active"
        in alerts_text
    )
    assert "Redis exporter scrape target" in alerts_text


@pytest.mark.unit
def test_alerts_include_operator_annotations():
    """Every alert should provide summary, description, and operator guidance."""
    for rule in _alert_rules():
        annotations = rule["annotations"]
        description = annotations["description"]
        guidance = annotations.get("runbook_url") or description

        assert annotations["summary"].strip()
        assert description.strip()
        assert isinstance(guidance, str)
        assert guidance.strip()


@pytest.mark.unit
def test_prometheus_loads_story_7_4_alert_rules():
    """Prometheus config should load the alert rule file without dropping scrape jobs."""
    config = _load_yaml_file("infra", "prometheus", "prometheus.yml")
    scrape_jobs = {job["job_name"]: job for job in config["scrape_configs"]}

    assert "alerts.yml" in config["rule_files"]
    assert scrape_jobs["prometheus"]["static_configs"][0]["targets"] == ["localhost:9090"]
    assert scrape_jobs["ytprocessor-api"]["metrics_path"] == "/prometheus-metrics"
    assert scrape_jobs["ytprocessor-api"]["static_configs"][0]["targets"] == ["api:8000"]
    assert scrape_jobs["ytprocessor-worker"]["metrics_path"] == "/metrics"
    assert scrape_jobs["ytprocessor-worker"]["static_configs"][0]["targets"] == ["worker:8082"]


@pytest.mark.unit
def test_demo_prometheus_service_can_read_alert_rules():
    """Demo compose should mount the Prometheus directory so alerts.yml is readable."""
    config = _load_yaml_file("docker-compose.demo.yml")
    volumes = config["services"]["prometheus"]["volumes"]

    assert "./infra/prometheus:/etc/prometheus:ro" in volumes


@pytest.mark.unit
def test_demo_prometheus_admin_api_is_only_bound_locally():
    """The demo admin API should not be exposed beyond localhost."""
    config = _load_yaml_file("docker-compose.demo.yml")
    prometheus = config["services"]["prometheus"]

    assert "--web.enable-admin-api" in prometheus["command"]
    assert "127.0.0.1:9090:9090" in prometheus["ports"]
