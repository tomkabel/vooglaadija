"""Tests for the weekly audit's failure channel.

The audit is only trustworthy if a tool that could not run is reported as
*unknown* instead of "0 findings" — a false-clean gate is worse than no gate
(Ford et al., fitness functions). Every check parses stdout, so these tests pin
the exit-code contract per tool: linters exit non-zero *because they found
findings*, anything else means the check itself failed.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from scripts import audit_report


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["tool"], returncode, stdout, stderr)


def _patch_run(
    monkeypatch: pytest.MonkeyPatch, result: subprocess.CompletedProcess[str]
) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], timeout: int = 120, cwd: Any = None) -> Any:
        calls.append(cmd)
        return result

    monkeypatch.setattr(audit_report, "run", fake_run)
    return calls


# --------------------------------------------------------------------------
# tool_error contract
# --------------------------------------------------------------------------
def test_tool_error_is_silent_for_expected_exit_codes() -> None:
    assert audit_report.tool_error("ruff", _proc(1, "[]"), ok_codes=(0, 1)) == ""


def test_tool_error_reports_missing_binary() -> None:
    message = audit_report.tool_error(
        "vulture", _proc(127, "", "not found: vulture"), ok_codes=(0, 3)
    )
    assert "exit 127" in message
    assert "not found: vulture" in message


def test_tool_error_reports_timeout_without_output() -> None:
    assert "no output" in audit_report.tool_error("ruff", _proc(124), ok_codes=(0, 1))


# --------------------------------------------------------------------------
# Gates must never turn a tool failure into "0 findings"
# --------------------------------------------------------------------------
def test_vulture_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, _proc(2, "", "vulture: error: unrecognized arguments"))
    findings, error = audit_report.vulture_findings()
    assert findings == []
    assert "vulture failed" in error


def test_vulture_dead_code_exit_is_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, _proc(3, "app/x.py:12: unused function 'foo' (90% confidence)"))
    findings, error = audit_report.vulture_findings()
    assert error == ""
    assert findings == [
        {"path": "app/x.py", "line": 12, "name": "unused function 'foo'", "confidence": 90}
    ]


def test_ruff_gate_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, _proc(2, "", "ruff failed\n  Cause: Unknown rule selector"))
    for check in (audit_report.unused_imports, audit_report.tid251_violations):
        findings, error = check()
        assert findings == []
        assert "Unknown rule selector" in error


def test_ruff_gate_parses_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps(
        [
            {
                "filename": "app/x.py",
                "location": {"row": 3},
                "code": "F401",
                "message": "unused import",
            }
        ]
    )
    _patch_run(monkeypatch, _proc(1, payload))
    findings, error = audit_report.unused_imports()
    assert error == ""
    assert findings[0]["code"] == "F401"


def test_boundary_verifier_crash_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, _proc(1, "", "Traceback (most recent call last):\nSyntaxError: bad"))
    violations, error = audit_report.boundary_violations()
    assert violations == []
    assert "import_analysis.py failed" in error


def test_boundary_violations_exit_one_is_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, _proc(1, "worker/x.py:4: import app.api (worker must not import API)"))
    violations, error = audit_report.boundary_violations()
    assert error == ""
    assert violations[0]["path"] == "worker/x.py"


# --------------------------------------------------------------------------
# Secrets delta: real findings are parsed, a failed scan is an error
# --------------------------------------------------------------------------
def test_scan_delta_parses_hook_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit_report.shutil, "which", lambda _name: "/usr/bin/detect-secrets-hook")
    report = json.dumps(
        {
            "results": {
                "app/x.py": [
                    {"type": "AWS Access Key", "line_number": 7, "hashed_secret": "abc"},
                ]
            }
        }
    )
    _patch_run(monkeypatch, _proc(1, report))
    findings, errors = audit_report.scan_issues_delta()
    assert errors == []
    assert findings == [{"path": "app/x.py", "line": 7, "type": "AWS Access Key"}]
    # Location metadata only — never the (hashed) value.
    assert "hashed_secret" not in findings[0]


def test_scan_delta_failure_is_an_error_not_a_clean_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit_report.shutil, "which", lambda _name: "/usr/bin/detect-secrets-hook")
    _patch_run(monkeypatch, _proc(1, "", "error: Unable to read baseline."))
    findings, errors = audit_report.scan_issues_delta()
    assert findings == []
    assert "Unable to read baseline" in str(errors[0]["type"])


def test_missing_scanner_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit_report.shutil, "which", lambda _name: None)
    findings, errors = audit_report.scan_issues_delta()
    assert findings == []
    assert "scanner unavailable" in str(errors[0]["type"])


# --------------------------------------------------------------------------
# Auto-fix must fail loudly instead of reporting "nothing to fix"
# --------------------------------------------------------------------------
def test_cmd_fix_returns_nonzero_when_ruff_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_run(monkeypatch, _proc(2, "", "ruff failed\n  Cause: broken config"))
    assert audit_report.cmd_fix() == 1
    assert "AUTOFIX_ERROR" in capsys.readouterr().out


def test_cmd_fix_reports_clean_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_run(monkeypatch, _proc(0, ""))
    assert audit_report.cmd_fix() == 0
    assert "AUTOFIX_CLEAN" in capsys.readouterr().out


def test_cmd_fix_unfixable_findings_are_not_a_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # ruff check --fix exits 1 when unsafe fixes are left for a human: fixes were
    # still applied, so that is a normal run.
    def fake_run(cmd: list[str], timeout: int = 120, cwd: Any = None) -> Any:
        if cmd[0] == "git":
            return _proc(0, " M app/x.py")
        if cmd[:3] == ["ruff", "check", "--fix"]:
            return _proc(1, "app/x.py:1:1: F401 [*] unused import")
        return _proc(0)

    monkeypatch.setattr(audit_report, "run", fake_run)
    assert audit_report.cmd_fix() == 0
    output = capsys.readouterr().out
    assert "CHANGED: app/x.py" in output
    assert "AUTOFIX_ERROR" not in output


def test_fix_commands_flag_unrunnable_checks() -> None:
    commands = audit_report.fix_commands(
        {
            "unused_imports": [],
            "unused_deps": [],
            "dead_code": [],
            "lock_ok": True,
            "scanner_findings": [],
            "scanner_errors": [],
            "complexity": [],
            "tool_errors": [{"tool": "boundary verifier", "message": "failed (exit 127)"}],
        }
    )
    assert any("could not run" in command for command in commands)


# --------------------------------------------------------------------------
# The report must not print a reassuring "0" for a check that never ran
# --------------------------------------------------------------------------
def _empty_measures() -> dict[str, Any]:
    return {
        "boundary": [],
        "unused_imports": [],
        "tid251": [],
        "dead_code": [],
        "unused_deps": [],
        "deptry_note": "",
        "lock_ok": True,
        "lock_note": "",
        "scanner_findings": [],
        "scanner_errors": [],
        "tool_errors": [],
        "complexity": [],
        "hotspots": [],
        "temporal_coupling": [],
        "defensive": [],
        "jscpd": [],
        "jscpd_note": "",
        "commented_out": [],
        "window_globals": [],
        "todos": 0,
        "large_files": [],
        "gate_summary": [],
        "measure_summary": [],
        "fix_commands": [],
    }


def test_report_prints_zero_when_every_check_ran() -> None:
    markdown = audit_report.render_markdown(_empty_measures(), [])
    assert "### Dead code (vulture >=80%): 0" in markdown
    assert "Checks that could not run: **0**" in markdown


def test_report_prints_unknown_for_a_failed_check() -> None:
    measures = _empty_measures()
    measures["tool_errors"] = [
        {"tool": "dead code (vulture)", "key": "dead_code", "message": "vulture failed (exit 127)"}
    ]
    markdown = audit_report.render_markdown(measures, [])
    assert "### Dead code (vulture >=80%): 0" not in markdown
    assert "### Dead code (vulture >=80%): unknown" in markdown
    assert "vulture failed (exit 127)" in markdown
