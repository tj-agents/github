import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".agents/github/utility/work-item/scripts/work_item.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("work_item", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
loaded = importlib.util.module_from_spec(SPEC)
assert isinstance(loaded, ModuleType)
SPEC.loader.exec_module(loaded)
work_item: Any = loaded


def example(kind: str = "bug") -> dict[str, Any]:
    item: dict[str, Any] = work_item.skeleton(kind)
    return {
        key: value if key == "kind" else f"Observed {key}: café"
        for key, value in item.items()
    }


def test_every_kind_requires_real_facts_and_renders_its_sections() -> None:
    for kind in work_item.KINDS:
        with pytest.raises(work_item.ItemError):
            work_item.validate(work_item.skeleton(kind))
        item = example(kind)
        assert work_item.validate(item) == item
        first = work_item.render(item)
        assert first == work_item.render(dict(reversed(list(item.items()))))
        for name, rule in work_item.schema_for(kind)["properties"].items():
            if "title" in rule and name in item:
                assert f"## {rule['title']}" in first


@pytest.mark.parametrize(
    "patch",
    [
        {"evidence": " "},
        {"kind": "unknown"},
        {"kind": []},
        {"priority": "urgent"},
        {"owner": None},
        {"links": "url"},
        {"links": [None]},
        {"title": "a\nb"},
        {"title": "a" * 257},
        {"extra": "ignored?"},
    ],
)
def test_validate_rejects_invalid_values(patch: dict[str, Any]) -> None:
    with pytest.raises(work_item.ItemError):
        work_item.validate({**example(), **patch})
    with pytest.raises(work_item.ItemError):
        work_item.validate([])
    with pytest.raises(work_item.ItemError):
        work_item.validate({"kind": "bug", "title": "Bug"})


def test_unknown_cause_is_valid_and_no_priority_is_invented() -> None:
    item = {**example("mistake"), "cause": "Unknown; investigation required"}
    rendered = work_item.render(item)
    assert "Priority:" not in rendered
    assert "Unknown; investigation required" in rendered


def test_story_round_trip_requires_user_outcome_facts() -> None:
    story = {
        key: value if key == "kind" else f"Story fact for {key}"
        for key, value in work_item.skeleton("story").items()
    }
    assert work_item.validate(story) == story
    body = work_item.render(story)
    for heading in ("User or role", "Goal", "Benefit", "Acceptance criteria"):
        assert f"## {heading}" in body
    with pytest.raises(work_item.ItemError):
        work_item.validate(
            {key: value for key, value in story.items() if key != "goal"}
        )
    template = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "template", "--kind", "story"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert template.returncode == 0, template.stderr
    assert json.loads(template.stdout)["kind"] == "story"
    preview = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "preview", "--input", "-"],
        input=json.dumps(story),
        text=True,
        capture_output=True,
        check=False,
    )
    assert preview.returncode == 0, preview.stderr
    assert json.loads(preview.stdout)["body"] == body


def test_legacy_ids_and_links_survive_preview() -> None:
    item = {
        **example(),
        "legacy_ids": ["BUGS.md#B013"],
        "links": ["https://example.test/evidence"],
    }
    body = work_item.render(item)
    assert "BUGS.md#B013" in body
    assert "https://example.test/evidence" in body


@pytest.mark.parametrize("command", ["validate", "preview"])
def test_cli_validates_and_previews_from_stdin_without_git_or_github(
    command: str,
) -> None:
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), command, "--input", "-"],
        input=json.dumps(example()),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert isinstance(json.loads(result.stdout), dict)


def test_cli_reports_invalid_json() -> None:
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "validate", "--input", "-"],
        input="broken json",
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "error" in json.loads(result.stderr)
