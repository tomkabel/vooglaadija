"""Regression tests for sprint-status epic/story consistency."""

import re
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPRINT_STATUS_PATH = (
    PROJECT_ROOT / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
)
EPIC_KEY_RE = re.compile(r"^epic-(\d+)$")
STORY_KEY_RE = re.compile(r"^(\d+)-\d+-")
PROGRESSED_STORY_STATUSES = {"ready-for-dev", "in-progress", "review", "done"}


def _load_development_status() -> dict[str, str]:
    if not SPRINT_STATUS_PATH.exists():
        pytest.skip("sprint-status artifact is local to Story Automator runs")

    sprint_status = yaml.safe_load(SPRINT_STATUS_PATH.read_text())
    assert isinstance(sprint_status, dict)

    development_status = sprint_status["development_status"]
    assert isinstance(development_status, dict)
    return development_status


def _story_statuses_by_epic(statuses: dict[str, str]) -> dict[str, list[tuple[str, str]]]:
    grouped_statuses: dict[str, list[tuple[str, str]]] = {}

    for key, status in statuses.items():
        match = STORY_KEY_RE.match(key)
        if match is None:
            continue

        epic_key = f"epic-{match.group(1)}"
        grouped_statuses.setdefault(epic_key, []).append((key, status))

    return grouped_statuses


@pytest.mark.unit
def test_epic_status_is_done_when_all_child_stories_are_done():
    """An epic header is done once every child story is done."""
    statuses = _load_development_status()
    stories_by_epic = _story_statuses_by_epic(statuses)

    for epic_key, story_statuses in stories_by_epic.items():
        if all(status == "done" for _, status in story_statuses):
            assert statuses[epic_key] == "done", (
                f"{epic_key} has all child stories done but header is {statuses[epic_key]!r}: "
                f"{story_statuses}"
            )


@pytest.mark.unit
def test_epic_status_is_in_progress_when_child_work_has_started():
    """An epic header leaves backlog once any child story has started."""
    statuses = _load_development_status()
    stories_by_epic = _story_statuses_by_epic(statuses)

    for epic_key, story_statuses in stories_by_epic.items():
        if all(status == "done" for _, status in story_statuses):
            continue

        progressed_stories = [
            (key, status) for key, status in story_statuses if status in PROGRESSED_STORY_STATUSES
        ]
        if progressed_stories:
            assert statuses[epic_key] == "in-progress", (
                f"{epic_key} has started child stories but header is {statuses[epic_key]!r}: "
                f"{progressed_stories}"
            )


@pytest.mark.unit
def test_development_status_uses_known_epic_and_story_statuses():
    """Sprint status entries use the documented workflow status values."""
    statuses = _load_development_status()
    expected_epic_statuses = {"backlog", "in-progress", "done"}
    expected_story_statuses = {"backlog", "ready-for-dev", "in-progress", "review", "done"}
    expected_retrospective_statuses = {"optional", "done"}

    for key, status in statuses.items():
        assert isinstance(status, str)

        if EPIC_KEY_RE.fullmatch(key):
            assert status in expected_epic_statuses
        elif key.endswith("-retrospective"):
            assert status in expected_retrospective_statuses
        else:
            assert status in expected_story_statuses
