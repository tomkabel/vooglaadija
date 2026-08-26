"""Regression tests for Story 2.2 jitter consolidation."""

import ast
import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.error_classifier import (
    CATEGORY_POLICIES,
    ErrorCategory,
    JitterType,
    RetryPolicy,
    calculate_delay,
)

pytestmark = pytest.mark.slow

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = ("app", "core", "worker", "tests", "scripts")
THIS_FILE = Path(__file__).name


def _iter_python_files(*roots: str) -> list[Path]:
    return [
        path
        for root in roots
        if (root_path := PROJECT_ROOT / root).exists()
        for path in root_path.rglob("*.py")
        if path.is_file() and "__pycache__" not in path.parts
    ]


def test_canonical_delay_function_and_jitter_enum_have_single_active_definition():
    """The canonical delay function and JitterType enum each have one active source definition."""
    delay_definitions: list[Path] = []
    jitter_type_definitions: list[Path] = []

    for path in _iter_python_files(*SOURCE_ROOTS):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "calculate_delay":
                delay_definitions.append(path.relative_to(PROJECT_ROOT))
            if isinstance(node, ast.ClassDef) and node.name == "JitterType":
                jitter_type_definitions.append(path.relative_to(PROJECT_ROOT))

    assert delay_definitions == [Path("app/services/error_classifier.py")]
    assert jitter_type_definitions == [Path("app/services/error_classifier.py")]


def test_deleted_retry_module_is_not_importable():
    """The deleted retry module remains unavailable through its legacy import path."""
    module_name = ".".join(("app", "services", "retry" + "_" + "service"))
    sys.modules.pop(module_name, None)

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


def test_deleted_retry_api_symbols_have_no_active_references():
    """Deleted retry API names have no active first-party source references."""
    deleted_symbols = [
        ".".join(("app", "services", "retry" + "_" + "service")),
        "calculate_retry" + "_with_jitter",
        "get_retry" + "_delay_seconds",
        "Jitter" + "RetryCalculator",
        "default" + "_calculator",
        "RETRY" + "_BASE_SECONDS",
        "RETRY" + "_CAP_SECONDS",
    ]
    matches: list[str] = []

    for path in _iter_python_files(*SOURCE_ROOTS):
        if path.name == THIS_FILE:
            continue
        text = path.read_text(encoding="utf-8")
        for symbol in deleted_symbols:
            if symbol in text:
                matches.append(f"{path.relative_to(PROJECT_ROOT)}: {symbol}")

    assert matches == []


def test_no_jitter_returns_policy_base_delay(monkeypatch):
    """A no-jitter policy returns its configured base delay without randomization."""
    monkeypatch.setitem(
        CATEGORY_POLICIES,
        ErrorCategory.BLOCKED,
        RetryPolicy(
            max_retries=0,
            base_delay_seconds=42.0,
            max_delay_seconds=42.0,
            jitter=JitterType.NONE,
            circuit_breaker_eligible=False,
            respects_retry_after=False,
        ),
    )

    assert calculate_delay(ErrorCategory.BLOCKED, attempt=0) == 42.0


def test_first_decorrelated_retry_uses_zero_to_base_range():
    """The first decorrelated retry randomizes between zero and the category base delay."""
    with patch("app.services.error_classifier.random.uniform", return_value=7.5) as uniform:
        delay = calculate_delay(ErrorCategory.TRANSIENT, attempt=0, prev_delay=120.0)

    assert delay == 7.5
    uniform.assert_called_once_with(
        0, CATEGORY_POLICIES[ErrorCategory.TRANSIENT].base_delay_seconds
    )


def test_later_decorrelated_retry_uses_previous_delay_window_capped_to_policy_max():
    """A later decorrelated retry randomizes from base to three times previous delay and caps it."""
    policy = CATEGORY_POLICIES[ErrorCategory.TRANSIENT]

    with patch("app.services.error_classifier.random.uniform", return_value=999.0) as uniform:
        delay = calculate_delay(ErrorCategory.TRANSIENT, attempt=1, prev_delay=250.0)

    assert delay == policy.max_delay_seconds
    uniform.assert_called_once_with(policy.base_delay_seconds, 750.0)


def test_full_jitter_uses_zero_to_exponential_attempt_cap():
    """Full jitter randomizes between zero and the capped exponential attempt delay."""
    policy = CATEGORY_POLICIES[ErrorCategory.TIMEOUT]
    expected_cap = min(policy.max_delay_seconds, policy.base_delay_seconds * (2**2))

    with patch("app.services.error_classifier.random.uniform", return_value=88.0) as uniform:
        delay = calculate_delay(ErrorCategory.TIMEOUT, attempt=2)

    assert delay == 88.0
    uniform.assert_called_once_with(0, expected_cap)


def test_retry_after_only_changes_delay_for_categories_that_respect_it():
    """Retry-After expands the base delay only for policies that explicitly respect it."""
    with patch("app.services.error_classifier.random.uniform", return_value=13.0) as uniform:
        rate_limited_delay = calculate_delay(
            ErrorCategory.RATE_LIMITED,
            attempt=0,
            retry_after=90,
        )

    assert rate_limited_delay == 13.0
    uniform.assert_called_once_with(0, 90.0)

    with patch("app.services.error_classifier.random.uniform", return_value=4.0) as uniform:
        transient_delay = calculate_delay(
            ErrorCategory.TRANSIENT,
            attempt=0,
            retry_after=90,
        )

    assert transient_delay == 4.0
    uniform.assert_called_once_with(
        0, CATEGORY_POLICIES[ErrorCategory.TRANSIENT].base_delay_seconds
    )
