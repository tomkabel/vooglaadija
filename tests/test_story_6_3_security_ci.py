"""Tests for Story 6.3 security CI gating."""

import re
import tomllib
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read_project_file(*parts: str) -> str:
    """Read a project file as text."""
    return (PROJECT_ROOT.joinpath(*parts)).read_text()


def test_codeql_runs_on_push_schedule_and_manual_dispatch():
    """CodeQL should run automatically on protected branches and weekly schedule."""
    workflow = read_project_file(".github", "workflows", "codeql.yml")

    assert "disabled" not in workflow.lower()
    assert "on:\n  push:\n    branches: [main, develop]\n" in workflow
    assert "  schedule:\n    - cron: '0 9 * * 1'\n" in workflow
    assert "  workflow_dispatch:\n" in workflow
    assert "languages: python" in workflow
    assert "build-mode: none" in workflow


def test_codeql_job_preserves_pinned_python_analysis_shape():
    """CodeQL should keep the pinned Python analysis job shape."""
    workflow = read_project_file(".github", "workflows", "codeql.yml")

    assert re.search(r"uses: actions/checkout@[0-9a-f]{40}\b", workflow)
    assert re.search(r"uses: github/codeql-action/init@[0-9a-f]{40}\b", workflow)
    assert re.search(r"uses: github/codeql-action/analyze@[0-9a-f]{40}\b", workflow)
    assert "persist-credentials: false" in workflow
    assert "timeout-minutes: 30" in workflow
    assert "security-events: write" in workflow
    assert "category: '/language:python'" in workflow


def test_active_checkout_steps_disable_persisted_git_credentials():
    """Repository checkout steps should not leave GitHub tokens in git config."""
    workflow = read_project_file(".github", "workflows", "fastapi-test.yml")

    checkout_steps = workflow.count(
        "uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5"
    )
    persist_flags = workflow.count("persist-credentials: false")

    assert checkout_steps == 6
    assert persist_flags >= checkout_steps


def test_security_job_remains_lint_dependent_and_blocks_build_check():
    """Security scans should run after lint and remain a build-check dependency."""
    workflow = read_project_file(".github", "workflows", "fastapi-test.yml")

    assert (
        "  security:\n"
        "    name: Security scan\n"
        "    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 10\n"
        "    needs: [lint]\n"
        "    permissions:\n"
        "      contents: read\n"
    ) in workflow
    assert "needs: [type-check, unit-tests, integration-tests, security]" in workflow


def test_security_workflow_uses_blocking_bandit_and_safety_steps():
    """Security workflow scan steps should fail the job when scanners fail."""
    workflow = read_project_file(".github", "workflows", "fastapi-test.yml")

    assert "hatch run security:scan-bandit || true" not in workflow
    assert "hatch run ci:security-safety-check || true" not in workflow
    assert "hatch run security:scan-bandit" in workflow
    assert "set -e" in workflow
    assert "hatch run security:scan-bandit || true" not in workflow
    assert (
        "      - name: Run Safety dependency check\n"
        "        env:\n"
        "          SAFETY_API_KEY: ${{ secrets.SAFETY_API_KEY }}\n"
        "        run: |\n"
        "          set -e\n"
        "          hatch run ci:security-safety-check\n"
    ) in workflow


def test_security_workflow_uploads_safety_report_even_on_failure():
    """Security workflow should keep the Safety JSON report artifact on scanner failure."""
    workflow = read_project_file(".github", "workflows", "fastapi-test.yml")

    assert (
        "      - name: Upload security report\n        uses: actions/upload-artifact@"
    ) in workflow
    assert "        if: always()\n" in workflow
    assert "          path: security-report.json\n" in workflow


def test_bandit_script_scans_app_and_core_at_blocking_threshold():
    """Bandit should scan application code at high-confidence medium-or-higher threshold."""
    pyproject = tomllib.loads(read_project_file("pyproject.toml"))
    security_scripts = pyproject["tool"]["hatch"]["envs"]["security"]["scripts"]

    assert security_scripts["scan-bandit"] == (
        "bandit -r app/ core/ -f screen --confidence-level high --severity-level medium"
    )
    assert "|| true" not in security_scripts["scan-bandit"]


def test_ci_safety_check_preserves_safety_exit_code():
    """The Hatch CI Safety check script should not mask vulnerability failures."""
    pyproject = tomllib.loads(read_project_file("pyproject.toml"))
    ci_scripts = pyproject["tool"]["hatch"]["envs"]["ci"]["scripts"]

    assert ci_scripts["security-safety-check"].startswith("safety --stage cicd scan")
    assert "|| true" not in ci_scripts["security-safety-check"]
    assert "SAFETY_API_KEY" not in ci_scripts["security-safety-check"]
    assert "--policy-file .safety-policy.yml" in ci_scripts["security-safety-check"]
    assert "--output json > security-report.json" in ci_scripts["security-safety-check"]
    assert "> security-report.json" in ci_scripts["security-safety"]
    assert "|| true" not in ci_scripts["security-safety"]


def test_safety_policy_fails_medium_or_higher_and_documents_ignores():
    """Safety policy should fail actionable findings and document accepted-risk ignores."""
    policy = read_project_file(".safety-policy.yml")
    policy_yaml = yaml.safe_load(policy)

    assert "fail-scan-with-exit-code:\n  dependency-vulnerabilities:\n    enabled: true" in policy
    assert "      cvss-severity:\n        - medium\n        - high\n        - critical\n" in policy
    assert "security-updates:\n  dependency-vulnerabilities:" in policy
    assert "auto-security-updates-limit:\n      - patch\n      - minor\n      - major\n" in policy

    ignored_vulnerabilities = (
        policy_yaml.get("report", {})
        .get("dependency-vulnerabilities", {})
        .get("auto-ignore-in-report", {})
        .get("vulnerabilities", {})
    )

    assert isinstance(ignored_vulnerabilities, dict)
    for vulnerability_id, suppression in ignored_vulnerabilities.items():
        reason = suppression.get("reason")
        expires = suppression.get("expires")

        assert str(vulnerability_id).isdigit()
        assert reason.strip()
        assert "existing accepted-risk suppression" not in reason.lower()
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", expires)
