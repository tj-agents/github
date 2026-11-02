from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".agents/github/utility/work-item/scripts"

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / "runtime/src"))
reader: Any = import_module("read_problem_record")
github: Any = import_module("github_tracker")
records: Any = import_module("tj_github_work_items.records")

CONFIG = {"repository": "owner/project"}
URL = "https://github.com/owner/project/issues/1"


class LocalTracker:
    def __init__(self, issue: dict[str, str], compact: dict[str, str]) -> None:
        self.repo = "owner/project"
        self.issue = issue
        self.value = compact

    def get(self, number: int) -> dict[str, str]:
        assert number == 1
        return self.issue

    def compact(self, issue: dict[str, str]) -> dict[str, str]:
        assert issue is self.issue
        return self.value


def item(kind: str) -> dict[str, str]:
    return {
        name: kind if name == "kind" else f"Observed {name}"
        for name in records.skeleton(kind)
    }


def managed_issue(
    kind: str, state: str = "open"
) -> tuple[dict[str, str], dict[str, str]]:
    body = github.body_for(item(kind), "request-0001")
    return (
        {"body": body},
        {"url": URL, "kind": kind, "state": state},
    )


def read_local(
    issue: dict[str, str],
    compact: dict[str, str],
    reference: str = URL,
    repository: str = "owner/project",
) -> dict[str, str]:
    tracker = LocalTracker(issue, compact)
    tracker.repo = repository
    api = object()
    with (
        patch.object(reader, "selection", return_value=CONFIG) as selected,
        patch.object(reader, "GitHub", return_value=api) as client,
        patch.object(reader, "Tracker", return_value=tracker) as tracker_type,
    ):
        result = cast(dict[str, str], reader.read("source", reference))
    selected.assert_called_once_with("source")
    client.assert_called_once_with(timeout=10)
    tracker_type.assert_called_once_with(CONFIG, api)
    return result


@pytest.mark.parametrize("kind", ("bug", "mistake", "debt"))
def test_reader_returns_the_exact_core_problem_record_protocol(kind: str) -> None:
    issue, compact = managed_issue(kind)
    assert read_local(issue, compact) == {
        "record": URL,
        "kind": kind,
        "state": "open",
        "body": issue["body"],
    }


def test_reader_rejects_feature_records() -> None:
    issue, compact = managed_issue("feature")
    with pytest.raises(records.ItemError, match="only open problem records"):
        read_local(issue, compact)


@pytest.mark.parametrize(
    ("repository", "reference"),
    (
        ("Owner/Project", URL),
        ("owner/project", "https://github.com/Owner/Project/issues/1"),
    ),
)
def test_reader_accepts_equivalent_repository_casing(
    repository: str, reference: str
) -> None:
    issue, compact = managed_issue("bug")
    result = read_local(issue, compact, reference, repository)
    assert result["record"] == URL
    assert result["body"] == issue["body"]


def test_reader_rejects_closed_problem_records() -> None:
    issue, compact = managed_issue("bug", "closed")
    with pytest.raises(records.ItemError, match="only open problem records"):
        read_local(issue, compact)


@pytest.mark.parametrize(
    ("reference", "message"),
    (
        ("owner/project#1", "canonical GitHub issue URL"),
        (
            "HTTPS://github.com/owner/project/issues/1",
            "canonical GitHub issue URL",
        ),
        (
            "https://GITHUB.COM/owner/project/issues/1",
            "canonical GitHub issue URL",
        ),
        (
            "https://github.com/owner/project/ISSUES/1",
            "canonical GitHub issue URL",
        ),
        (
            "https://github.com/owner/project/issues/01",
            "canonical GitHub issue URL",
        ),
        (
            "https://github.com/owner/project/issues/1/",
            "matching the selected repository",
        ),
    ),
)
def test_reader_requires_the_canonical_issue_url(reference: str, message: str) -> None:
    issue, compact = managed_issue("bug")
    with pytest.raises(records.ItemError, match=message):
        read_local(issue, compact, reference)


def test_reader_rejects_render_hash_tampering() -> None:
    issue, compact = managed_issue("bug")
    issue["body"] = "Manual edit\n" + issue["body"]
    with pytest.raises(records.ItemError, match="manually edited"):
        read_local(issue, compact)


def test_reader_rejects_kind_hash_tampering() -> None:
    issue, compact = managed_issue("debt")
    info, _ = github.metadata(issue["body"])
    issue["body"] = issue["body"].replace(info["kind_hash"], "0" * 64, 1)
    with pytest.raises(records.ItemError, match="kind differs or is unbound"):
        read_local(issue, compact)
