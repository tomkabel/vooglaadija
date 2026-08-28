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
        "otel-collector": "256M",
        "worker": "512M",
    }

    for service_name, expected_memory in expected_memory_limits.items():
        limits = services[service_name]["deploy"]["resources"]["limits"]

        assert limits["memory"] == expected_memory


@pytest.mark.unit
def test_local_override_does_not_shadow_base_resource_limits():
    """The local override adds build targets/ports but never relaxes resource limits."""
    local_services = _load_yaml_file("docker-compose.local.yml")["services"]

    for service_name in ("api", "worker"):
        assert "deploy" not in local_services[service_name], (
            f"{service_name} local override must not change resource limits"
        )
