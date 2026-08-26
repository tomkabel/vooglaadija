"""Regression tests for Story 9.5 Safety suppression policy quality."""

from __future__ import annotations

import re
import tomllib
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.slow


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = PROJECT_ROOT / ".safety-policy.yml"
GENERIC_REASON_PATTERNS = (
    "existing accepted-risk suppression",
    "revisit when upstream fix is available",
)
EXPECTED_SEVERITIES = ["medium", "high", "critical"]


def read_project_file(*parts: str) -> str:
    """Read a project file as text."""
    return PROJECT_ROOT.joinpath(*parts).read_text()


def load_safety_policy() -> dict:
    """Load the Safety policy as YAML."""
    loaded_policy = yaml.safe_load(POLICY_PATH.read_text())
    assert isinstance(loaded_policy, dict)
    return loaded_policy


def get_suppressed_vulnerabilities(policy: dict) -> dict:
    """Return the Safety auto-ignore vulnerability map when it exists."""
    vulnerabilities = (
        policy.get("report", {})
        .get("dependency-vulnerabilities", {})
        .get("auto-ignore-in-report", {})
        .get("vulnerabilities", {})
    )
    assert isinstance(vulnerabilities, dict)
    return vulnerabilities


def test_safety_policy_suppressions_have_specific_reasons():
    """Safety suppressions should have non-generic reasons tied to each finding."""
    suppressed_vulnerabilities = get_suppressed_vulnerabilities(load_safety_policy())

    for vulnerability_id, suppression in suppressed_vulnerabilities.items():
        reason = suppression.get("reason")

        assert isinstance(reason, str)
        assert reason.strip()
        assert not any(pattern in reason.lower() for pattern in GENERIC_REASON_PATTERNS)
        assert str(vulnerability_id) in reason or re.search(
            r"\b(package|dependency|CVE-|advisory|python-jose|ecdsa|cryptography)\b",
            reason,
            flags=re.IGNORECASE,
        )
        assert re.search(r"\b(revisit|upgrade|remove|review|remediate)\b", reason, re.I)


def test_safety_policy_suppressions_have_non_expired_expiration_dates():
    """Safety suppressions should carry ISO dates that are not expired."""
    suppressed_vulnerabilities = get_suppressed_vulnerabilities(load_safety_policy())
    today = datetime.now(UTC).date()

    for suppression in suppressed_vulnerabilities.values():
        expires = suppression.get("expires")

        assert isinstance(expires, str)
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", expires)
        assert date.fromisoformat(expires) >= today


def test_safety_policy_has_no_unverified_auto_ignore_suppressions():
    """Safety policy should not keep unverified dependency vulnerability suppressions."""
    suppressed_vulnerabilities = get_suppressed_vulnerabilities(load_safety_policy())

    assert suppressed_vulnerabilities == {}


def test_safety_policy_keeps_blocking_medium_or_higher_failures():
    """Safety policy should keep medium, high, and critical findings blocking."""
    policy = load_safety_policy()
    fail_config = policy["fail-scan-with-exit-code"]["dependency-vulnerabilities"]

    assert fail_config["enabled"] is True
    assert fail_config["fail-on-any-of"]["cvss-severity"] == EXPECTED_SEVERITIES


def test_ci_safety_command_uses_policy_file_and_preserves_exit_code():
    """The CI Safety command should use the policy file and preserve scanner failures."""
    pyproject = tomllib.loads(read_project_file("pyproject.toml"))
    ci_scripts = pyproject["tool"]["hatch"]["envs"]["ci"]["scripts"]
    safety_check = ci_scripts["security-safety-check"]

    assert safety_check.startswith("safety --stage cicd scan")
    assert "--policy-file .safety-policy.yml" in safety_check
    assert "--output json > security-report.json" in safety_check
    assert "|| true" not in safety_check
