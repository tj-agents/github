import json
from pathlib import Path
from typing import Any

import pytest

RENOVATE_APP_ID = 2740
DECLARATION = (
    Path(__file__).parents[2] / "configuration/tj-agents/github-configuration.json"
)


def rulesets() -> list[tuple[str, str, dict[str, Any]]]:
    document = json.loads(DECLARATION.read_text(encoding="utf-8"))
    found = []
    for repository, settings in document["repositories"].items():
        found += [
            (repository, name, ruleset)
            for name, ruleset in settings.get("rulesets", {}).items()
        ]
    return found


def renovate_bypasses(ruleset: dict[str, Any]) -> bool:
    return any(
        actor.get("actor_type") == "Integration"
        and actor.get("actor_id") == RENOVATE_APP_ID
        for actor in ruleset.get("bypass_actors", [])
    )


def required_approvals(ruleset: dict[str, Any]) -> int:
    return max(
        (
            rule.get("parameters", {}).get("required_approving_review_count", 0)
            for rule in ruleset.get("rules", [])
            if rule.get("type") == "pull_request"
        ),
        default=0,
    )


@pytest.mark.parametrize(
    ("repository", "name", "ruleset"),
    [entry for entry in rulesets() if required_approvals(entry[2])],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_renovate_never_needs_approval(
    repository: str, name: str, ruleset: dict[str, Any]
) -> None:
    assert renovate_bypasses(ruleset), (
        f"{repository}/{name} requires approval without exempting Renovate"
    )
    assert all(rule.get("type") == "pull_request" for rule in ruleset["rules"]), (
        f"{repository}/{name} exempts Renovate from rules beyond approval"
    )


@pytest.mark.parametrize(
    ("repository", "name", "ruleset"),
    [entry for entry in rulesets() if entry[2].get("target") == "branch"],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_renovate_never_bypasses_required_checks(
    repository: str, name: str, ruleset: dict[str, Any]
) -> None:
    has_checks = any(
        rule.get("type") == "required_status_checks" for rule in ruleset["rules"]
    )
    assert not (has_checks and renovate_bypasses(ruleset)), (
        f"{repository}/{name} lets Renovate skip required checks"
    )
