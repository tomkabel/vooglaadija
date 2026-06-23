"""Regression tests for Story 9.3 Docker Compose resource limits."""

from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ComposeLoader(yaml.SafeLoader):
    """YAML loader that understands Docker Compose custom tags used in this repo."""


def _compose_override(loader: ComposeLoader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


ComposeLoader.add_constructor("!override", _compose_override)


def _load_yaml_file(*parts: str) -> dict[str, Any]:
    return yaml.load(PROJECT_ROOT.joinpath(*parts).read_text(), Loader=ComposeLoader)


@pytest.mark.unit
def test_every_base_compose_service_has_cpu_and_memory_limits():
    """Every base compose service resolves to CPU and memory resource limits."""
    compose = _load_yaml_file("docker-compose.yml")

    for service_name, service in compose["services"].items():
        limits = service.get("deploy", {}).get("resources", {}).get("limits")

        assert isinstance(limits, dict), f"{service_name} is missing deploy.resources.limits"
        assert limits.get("cpus"), f"{service_name} is missing a CPU limit"
        assert limits.get("memory"), f"{service_name} is missing a memory limit"


@pytest.mark.unit
def test_worker_has_explicit_limits_distinct_from_api():
    """Worker resource limits are sized independently from the API/base-service defaults."""
    compose = _load_yaml_file("docker-compose.yml")
    base_limits = compose["x-base-service"]["deploy"]["resources"]["limits"]
    api_limits = compose["services"]["api"]["deploy"]["resources"]["limits"]
    worker_limits = compose["services"]["worker"]["deploy"]["resources"]["limits"]

    assert api_limits == base_limits
    assert worker_limits == {"cpus": "0.75", "memory": "512M"}
    assert worker_limits != base_limits


@pytest.mark.unit
def test_story_required_service_memory_limits_match_contract():
    """Named Story 9.3 services use the exact memory limits required by the story."""
    services = _load_yaml_file("docker-compose.yml")["services"]

    expected_memory_limits = {
        "nginx": "64M",
        "swagger-ui": "128M",
        "otel-collector": "256M",
        "worker": "512M",
    }

    for service_name, expected_memory in expected_memory_limits.items():
        limits = services[service_name]["deploy"]["resources"]["limits"]

        assert limits["memory"] == expected_memory


@pytest.mark.unit
def test_production_overrides_do_not_shadow_base_resource_limits():
    """Production overrides keep resource limits for base services they customize."""
    base_services = _load_yaml_file("docker-compose.yml")["services"]
    production_services = _load_yaml_file("docker-compose.production.yml")["services"]

    for service_name in ("worker", "nginx", "swagger-ui"):
        base_limits = base_services[service_name]["deploy"]["resources"]["limits"]
        override_deploy = production_services[service_name].get("deploy")

        if override_deploy is None:
            override_limits = base_limits
        else:
            override_limits = override_deploy.get("resources", {}).get("limits")

        assert override_limits == base_limits


@pytest.mark.unit
def test_production_compose_preserves_swagger_resource_limits():
    """The production swagger override keeps resource limits while disabling replicas."""
    swagger_override = _load_yaml_file("docker-compose.production.yml")["services"]["swagger-ui"]

    assert swagger_override["deploy"]["replicas"] == 0
    assert swagger_override["deploy"]["resources"]["limits"] == {
        "cpus": "0.25",
        "memory": "128M",
    }
