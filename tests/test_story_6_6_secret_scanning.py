import pytest

"""Tests for Story 6.6 secret-scanning guardrails."""

import json
import re
import tomllib
from pathlib import Path

pytestmark = pytest.mark.slow



PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read_project_file(*parts: str) -> str:
    """Read a project file as text."""
    return (PROJECT_ROOT.joinpath(*parts)).read_text()


def test_pre_commit_runs_detect_secrets_with_committed_baseline():
    """Pre-commit should run full secret scanning against the committed baseline."""
    config = read_project_file(".pre-commit-config.yaml")

    assert "https://github.com/Yelp/detect-secrets" in config
    assert re.search(r"rev: v1\.5\.0\b", config)
    assert "      - id: detect-secrets\n" in config
    assert "        args: [--baseline, .secrets.baseline]\n" in config
    assert "pnpm-lock\\.yaml|uv\\.lock" in config
    assert "^frontend/css/dist/" in config
    assert "      - id: detect-private-key\n" in config


def test_detect_secrets_baseline_exists_and_has_expected_shape():
    """The committed detect-secrets baseline should be valid JSON scanner metadata."""
    baseline_path = PROJECT_ROOT / ".secrets.baseline"

    assert baseline_path.exists()

    baseline = json.loads(baseline_path.read_text())
    assert baseline["version"] == "1.5.0"
    assert isinstance(baseline["plugins_used"], list)
    assert isinstance(baseline["filters_used"], list)
    assert isinstance(baseline["results"], dict)


def test_security_hatch_environment_exposes_blocking_secret_scan():
    """The Hatch security env should include detect-secrets and preserve scanner exit codes."""
    pyproject = tomllib.loads(read_project_file("pyproject.toml"))
    security_dependencies = pyproject["dependency-groups"]["security"]
    security_scripts = pyproject["tool"]["hatch"]["envs"]["security"]["scripts"]

    assert "detect-secrets>=1.5.0" in security_dependencies
    assert security_scripts["scan-secrets"].startswith(
        "detect-secrets-hook --baseline .secrets.baseline $(git ls-files"
    )
    assert "app/static/js/htmx.min.js" in security_scripts["scan-secrets"]
    assert "frontend/css/dist/*" in security_scripts["scan-secrets"]
    assert "frontend/pnpm-lock.yaml" in security_scripts["scan-secrets"]
    assert "pnpm-lock.yaml" in security_scripts["scan-secrets"]
    assert "uv.lock" in security_scripts["scan-secrets"]
    assert "|| true" not in security_scripts["scan-secrets"]


def test_security_ci_runs_secret_scan_without_masking_failures():
    """CI should run secret scanning in the blocking security job."""
    workflow = read_project_file(".github", "workflows", "fastapi-test.yml")

    # Verify ordering: secret scan comes before bandit scan
    assert workflow.index("Run secret scan") < workflow.index("Run Bandit security scan")
    assert "hatch run security:scan-secrets || true" not in workflow

    secret_scan_step = re.search(
        r"      - name: Run secret scan\n(?P<body>(?:        .*\n|          .*\n)+)",
        workflow,
    )
    assert secret_scan_step is not None
    body = secret_scan_step.group("body")
    assert "set -e" in body, "Secret scan step missing fail-fast (set -e)"
    assert "uv tool install" in body, "Secret scan step missing tool install"
    assert "hatch run security:scan-secrets" in body, "Secret scan step missing scan command"
    assert "continue-on-error: true" not in body
