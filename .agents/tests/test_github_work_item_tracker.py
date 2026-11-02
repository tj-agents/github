from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".agents/github/utility/work-item/scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / "runtime/src"))
github: Any = import_module("github_tracker")
records: Any = import_module("tj_github_work_items.records")
RequestJournal: Any = import_module(
    "tj_github_work_items.request_journal"
).RequestJournal

CONFIG: dict[str, Any] = {
    "provider": "github:work-item",
    "repository": "owner/project",
    "issue_types": {},
}


def select_personal(root: Path) -> None:
    agents = root / ".agents"
    agents.mkdir(exist_ok=True)
    (agents / "plugins.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "marketplaces": {"tj-agents": {"repository": "tj-agents/marketplace"}},
                "plugins": ["personal@tj-agents"],
            }
        ),
        encoding="utf-8",
    )


def item(kind: str = "bug") -> dict[str, Any]:
    result: dict[str, Any] = records.skeleton(kind)
    return {
        key: value if key == "kind" else f"Observed {key}"
        for key, value in result.items()
    }


class FakeAPI:
    def __init__(self) -> None:
        self.issues: list[dict[str, Any]] = []
        self.comments: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str, Any]] = []
        self.fail_after_create = False
        self.fail_before_create = False
        self.fail_after_comment = False
        self.projects: list[dict[str, Any]] = []
        self.placements: list[tuple[str, str, str]] = []
        self.status: dict[str, str] = {}
        self.kind: dict[str, str] = {}
        self.fail_graphql = False
        self.readback_status: str | None = None
        self.readback_kind: str | None = None
        self.comment_readback: dict[int, dict[str, Any]] = {}
        self.request_override: (
            Callable[[str, str, dict[str, Any] | None, bool], Any] | None
        ) = None

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        if self.fail_graphql:
            raise github.APIError("missing project scope", 403)
        if query.startswith("query($owner:String!){repositoryOwner"):
            return {
                "repositoryOwner": {
                    "id": "owner-id",
                    "projectsV2": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": copy.deepcopy(self.projects),
                    },
                }
            }
        if query.startswith("query($owner:String!,$name:String!){repository("):
            return {"repository": {"id": "repository-id"}}
        if "createProjectV2(" in query:
            status_field = {
                "id": "status",
                "name": "Status",
                "options": [
                    {"id": "todo", "name": "Todo"},
                    {"id": "done", "name": "Done"},
                ],
            }
            project = {
                "id": f"board-{variables['title']}",
                "title": variables["title"],
                "closed": False,
                "fields": {"nodes": [status_field, {}]},
            }
            self.projects.append(project)
            return {"createProjectV2": {"projectV2": copy.deepcopy(project)}}
        if "createProjectV2Field" in query:
            project = next(
                project
                for project in self.projects
                if project["id"] == variables["project"]
            )
            options = [{"id": f"kind-{name}", "name": name} for name in records.KINDS]
            field = {"id": "kind", "name": "Kind", "options": options}
            project["fields"]["nodes"].append(field)
            return {"createProjectV2Field": {"projectV2Field": copy.deepcopy(field)}}
        if "addProjectV2ItemById" in query:
            board_item = f"{variables['project']}:{variables['content']}"
            existing_status = self.status.get(board_item)
            return {
                "addProjectV2ItemById": {
                    "item": {
                        "id": board_item,
                        "status": existing_status and {"name": existing_status},
                    }
                }
            }
        if "updateProjectV2ItemFieldValue" in query:
            self.placements.append(
                (variables["project"], variables["field"], variables["option"])
            )
            value = next(
                option["name"]
                for project in self.projects
                if project["id"] == variables["project"]
                for field in project["fields"]["nodes"]
                if field and field["id"] == variables["field"]
                for option in field["options"]
                if option["id"] == variables["option"]
            )
            if variables["field"] == "status":
                self.status[variables["item"]] = value
            if variables["field"] == "kind":
                self.kind[variables["item"]] = value
            return {
                "updateProjectV2ItemFieldValue": {
                    "projectV2Item": {
                        "id": variables["item"],
                        "status": {
                            "name": self.readback_status
                            or self.status.get(variables["item"])
                        },
                        "kind": {
                            "name": self.readback_kind
                            or self.kind.get(variables["item"])
                        },
                    }
                }
            }
        raise AssertionError(query)

    def request(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        paginate: bool = False,
    ) -> Any:
        if self.request_override is not None:
            return self.request_override(method, endpoint, payload, paginate)
        self.calls.append((method, endpoint, copy.deepcopy(payload)))
        if endpoint == "graphql":
            assert payload is not None
            return {"data": self.graphql(payload["query"], payload["variables"])}
        base = "repos/owner/project/issues"
        if method == "GET" and endpoint == f"{base}?state=all&per_page=100":
            return copy.deepcopy(self.issues)
        if method == "GET" and endpoint == "repos/owner/project/issue-types":
            return [{"name": name} for name in ("Task", "Feature", "Story", "Bug")]
        if method == "GET" and endpoint.startswith(
            "repos/owner/project/issues/comments/"
        ):
            comment_id = int(endpoint.rsplit("/", maxsplit=1)[1])
            return copy.deepcopy(
                self.comment_readback.get(
                    comment_id,
                    next(
                        comment
                        for comment in self.comments
                        if comment["id"] == comment_id
                    ),
                )
            )
        if endpoint.startswith(f"{base}/1/comments"):
            if method == "GET":
                return copy.deepcopy(self.comments)
            assert payload is not None
            value = {
                "id": len(self.comments) + 1,
                "body": payload["body"],
                "html_url": "https://github.com/owner/project/issues/1#issuecomment-1",
            }
            self.comments.append(value)
            if self.fail_after_comment:
                raise github.APIError("lost response")
            return copy.deepcopy(value)
        if method == "POST" and endpoint == base:
            if self.fail_before_create:
                raise github.APIError("rejected", 403)
            assert payload is not None
            number = len(self.issues) + 1
            value = {
                "number": number,
                "state": "open",
                "html_url": f"https://github.com/owner/project/issues/{number}",
                "node_id": f"issue-{number}",
                **payload,
            }
            self.issues.append(value)
            if self.fail_after_create:
                raise github.APIError("lost response")
            return copy.deepcopy(value)
        if endpoint == f"{base}/1":
            if method == "PATCH":
                assert payload is not None
                self.issues[0].update(payload)
            return copy.deepcopy(self.issues[0])
        raise AssertionError((method, endpoint, payload, paginate))


@pytest.fixture
def api() -> FakeAPI:
    return FakeAPI()


@pytest.fixture
def tracker(api: FakeAPI) -> Any:
    return github.Tracker(CONFIG, api)


def test_create_retries_reconcile_the_original_issue(
    tracker: Any, api: FakeAPI
) -> None:
    first = tracker.create(item(), "request-0001")
    second = tracker.create(item(), "request-0001")
    assert first["identity"] == second["identity"]
    assert second["reconciled"] is True
    assert len(api.issues) == 1
    assert (
        sum(
            method == "POST" and endpoint != "graphql"
            for method, endpoint, _ in api.calls
        )
        == 1
    )


def test_lost_create_response_is_reconciled_without_reposting(
    tracker: Any, api: FakeAPI
) -> None:
    api.fail_after_create = True
    assert tracker.create(item(), "request-0001")["reconciled"] is True
    assert len(api.issues) == 1


def test_rejected_create_does_not_retry_post(tracker: Any, api: FakeAPI) -> None:
    api.fail_before_create = True
    with pytest.raises(records.ItemError, match="uncertain or rejected"):
        tracker.create(item(), "request-0001")
    assert sum(method == "POST" for method, _, _ in api.calls) == 1


def test_collision_and_duplicates_remain_visible(tracker: Any, api: FakeAPI) -> None:
    tracker.create(item(), "request-0001")
    with pytest.raises(records.ItemError, match="different creation facts"):
        tracker.create({**item(), "title": "Different"}, "request-0001")
    api.issues.append(copy.deepcopy(api.issues[0]))
    with pytest.raises(records.ItemError, match="multiple issues"):
        tracker.create(item(), "request-0001")


def test_native_type_is_set_only_when_the_repository_offers_it(api: FakeAPI) -> None:
    tracker = github.Tracker({**CONFIG, "issue_types": {"feature": "Feature"}}, api)
    tracker.create(item("feature"), "request-0001")
    assert api.issues[0]["type"] == "Feature"
    tracker = github.Tracker({**CONFIG, "issue_types": {"bug": "Missing"}}, api)
    tracker.create(item(), "request-0002")
    assert "type" not in api.issues[1]


def test_created_item_lands_on_repository_and_owner_boards(
    tracker: Any, api: FakeAPI
) -> None:
    created = tracker.create(item("debt"), "request-0001")
    assert created["boards"] == {"placed": ["project", "owner"]}
    assert [project["title"] for project in api.projects] == ["project", "owner"]
    for board in ("board-project", "board-owner"):
        assert (board, "status", "todo") in api.placements
        assert (board, "kind", "kind-debt") in api.placements
    tracker.create(item("debt"), "request-0001")
    assert len(api.projects) == 2
    assert sum(field == "status" for _, field, _ in api.placements) == 2


def test_board_placement_uses_its_own_client(api: FakeAPI) -> None:
    boards = FakeAPI()
    tracker = github.Tracker(CONFIG, api, board_api=boards)
    created = tracker.create(item("debt"), "request-0001")
    assert created["boards"] == {"placed": ["project", "owner"]}
    assert len(api.issues) == 1
    assert api.projects == []
    assert [project["title"] for project in boards.projects] == ["project", "owner"]


def test_project_token_uses_an_inactive_login_with_project_scope() -> None:
    hosts: dict[str, Any] = {
        "hosts": {
            "github.com": [
                {
                    "login": "work",
                    "active": True,
                    "state": "success",
                    "scopes": "repo, read:org",
                },
                {
                    "login": "personal",
                    "active": False,
                    "state": "success",
                    "scopes": "project, repo",
                },
            ]
        }
    }
    calls: list[list[str]] = []

    def run(command: list[str], **_: Any) -> SimpleNamespace:
        calls.append(command)
        output = json.dumps(hosts) if command[2] == "status" else "secret\n"
        return SimpleNamespace(stdout=output)

    assert github.project_token(run) == "secret"
    assert "personal" in calls[-1]
    hosts["hosts"]["github.com"][0]["scopes"] = "project, repo"
    assert github.project_token(run) is None
    assert (
        github.project_token(lambda *_, **__: SimpleNamespace(stdout="not json"))
        is None
    )


def test_issue_urls_match_the_origin_case_insensitively(
    tracker: Any, api: FakeAPI
) -> None:
    tracker.create(item(), "request-0001")
    alternate = github.Tracker({**CONFIG, "repository": "Owner/Project"}, api)
    assert alternate.compact(api.issues[0])["identity"] == "Owner/Project#1"
    assert (
        github.issue_number(
            "https://github.com/owner/project/issues/1", "Owner/Project"
        )
        == 1
    )


def test_lost_create_response_still_sets_the_initial_board_status(
    tracker: Any, api: FakeAPI
) -> None:
    api.fail_after_create = True
    created = tracker.create(item(), "request-0001")
    assert created["reconciled"] is True
    assert ("board-project", "status", "todo") in api.placements


def test_board_failure_reports_without_losing_the_created_issue(
    tracker: Any, api: FakeAPI
) -> None:
    api.fail_graphql = True
    created = tracker.create(item(), "request-0001")
    assert created["reconciled"] is False
    assert created["boards"]["placed"] == []
    assert "board placement incomplete" in created["boards"]["error"]
    assert len(api.issues) == 1


def test_story_uses_native_type_and_round_trips_its_managed_kind(api: FakeAPI) -> None:
    tracker = github.Tracker({**CONFIG, "issue_types": {"story": "Story"}}, api)
    created = tracker.create(item("story"), "request-0001")
    assert api.issues[0]["type"] == "Story"
    assert created["kind"] == "story"
    assert tracker.compact(api.issues[0])["kind"] == "story"


def test_update_refuses_stale_hash_and_manual_body_edits(
    tracker: Any, api: FakeAPI
) -> None:
    created = tracker.create(item(), "request-0001")
    updated = {**item(), "evidence": "New evidence"}
    with pytest.raises(records.ItemError, match="changed"):
        tracker.update(1, updated, "stale")
    tracker.update(1, updated, created["body_hash"])
    assert "New evidence" in api.issues[0]["body"]
    api.issues[0]["body"] = f"Manual text\n{api.issues[0]['body']}"
    with pytest.raises(records.ItemError, match="changed"):
        tracker.update(1, updated, github.digest(api.issues[0]["body"]))


def test_update_preserves_creation_identity_and_rejects_kind_changes(
    tracker: Any, api: FakeAPI
) -> None:
    created = tracker.create(item(), "request-0001")
    tracker.update(1, {**item(), "title": "Updated"}, created["body_hash"])
    assert tracker.create(item(), "request-0001")["reconciled"] is True
    current = tracker.compact(api.issues[0])
    with pytest.raises(records.ItemError, match="cannot change"):
        tracker.update(1, item("task"), current["body_hash"])


def test_uncertain_comment_reconciles_and_changed_comment_refuses_retry(
    tracker: Any, api: FakeAPI
) -> None:
    tracker.create(item(), "request-0001")
    api.fail_after_comment = True
    first = tracker.comment(1, "Actual verification", "comment-0001", "close")
    second = tracker.comment(1, "Actual verification", "comment-0001", "close")
    assert first == second
    assert len(api.comments) == 1
    api.comments[0]["body"] = f"Changed\n{api.comments[0]['body']}"
    with pytest.raises(records.ItemError, match="different facts"):
        tracker.comment(1, "Actual verification", "comment-0001", "close")


def test_pull_requests_and_oversized_bodies_cannot_be_mutated(
    tracker: Any, api: FakeAPI
) -> None:
    tracker.create(item(), "request-0001")
    api.issues[0]["pull_request"] = {}
    assert tracker.all() == []
    with pytest.raises(records.ItemError, match="expected issue"):
        tracker.get(1)
    with pytest.raises(records.ItemError, match="exceeds"):
        tracker.create({**item(), "evidence": "a" * 65536}, "request-0002")
    with pytest.raises(records.ItemError):
        tracker.comment(1, " ", "comment-0001")


def test_close_records_verification_before_closing_and_is_repeatable(
    tracker: Any, api: FakeAPI, tmp_path: Path
) -> None:
    tracker.create(item(), "request-0001")
    text = tmp_path / "verification.txt"
    text.write_text("Observed tests passed", encoding="utf-8")
    args = argparse.Namespace(
        command="close",
        root=str(tmp_path),
        item="owner/project#1",
        request_id="close-0001",
        text_file=str(text),
    )
    with (
        patch.object(github, "GitHub", return_value=api),
        patch.object(github, "selection", return_value=CONFIG),
        patch.object(github, "RequestJournal", return_value=None),
    ):
        result = github.execute(args)
        assert result["state"] == "closed"
        assert "Observed tests passed" in api.comments[0]["body"]
        repeated = github.execute(args)
        assert repeated["already_closed"] is True
        assert len(api.comments) == 1
    writes = [
        (method, endpoint)
        for method, endpoint, _ in api.calls
        if method in ("POST", "PATCH")
    ]
    first_close = next(
        index
        for index, (_, endpoint) in enumerate(writes)
        if endpoint.endswith("/comments")
    )
    assert writes[first_close + 1][0] == "PATCH"
    assert api.status["board-project:issue-1"] == "Done"
    assert api.status["board-owner:issue-1"] == "Done"


def test_progress_records_one_evidence_comment_and_updates_both_boards(
    tracker: Any, api: FakeAPI
) -> None:
    tracker.create(item("debt"), "request-0001")
    issue = tracker.get(1)
    first = tracker.progress(issue, "Todo", "Work is ready", "progress-0001")
    second = tracker.progress(issue, "Todo", "Work is ready", "progress-0001")
    assert first["status"] == "Todo"
    assert first == second
    assert len(api.comments) == 1
    assert '"operation":"progress"' in api.comments[0]["body"]
    assert '"status":"Todo"' in api.comments[0]["body"]
    assert api.status["board-project:issue-1"] == "Todo"
    assert api.status["board-owner:issue-1"] == "Todo"


def test_progress_uses_the_requested_status_when_boards_have_no_todo(
    tracker: Any, api: FakeAPI
) -> None:
    tracker.create(item("debt"), "request-0001")
    for project in api.projects:
        project["fields"]["nodes"][0]["options"] = [{"id": "review", "name": "Review"}]
    result = tracker.progress(
        tracker.get(1), "Review", "Awaiting review", "progress-0001"
    )
    assert result["status"] == "Review"
    assert api.status["board-project:issue-1"] == "Review"
    assert api.status["board-owner:issue-1"] == "Review"


def test_progress_replays_partial_board_work_without_a_duplicate_comment(
    tracker: Any, api: FakeAPI
) -> None:
    tracker.create(item("debt"), "request-0001")
    api.projects[0]["fields"]["nodes"][0]["options"] = [{"id": "todo", "name": "Todo"}]
    issue = tracker.get(1)
    with pytest.raises(records.ItemError, match="available: Todo"):
        tracker.progress(issue, "Review", "Awaiting review", "progress-0001")
    assert len(api.comments) == 1
    api.projects[0]["fields"]["nodes"][0]["options"].append(
        {"id": "review", "name": "Review"}
    )
    api.projects[1]["fields"]["nodes"][0]["options"].append(
        {"id": "review", "name": "Review"}
    )
    tracker.progress(issue, "Review", "Awaiting review", "progress-0001")
    assert len(api.comments) == 1


def test_progress_requires_kind_readback_before_completing_placement(
    tracker: Any, api: FakeAPI
) -> None:
    tracker.create(item("debt"), "request-0001")
    for project in api.projects:
        project["fields"]["nodes"][0]["options"].append(
            {"id": "review", "name": "Review"}
        )
    api.readback_kind = "task"
    with pytest.raises(records.ItemError, match="did not record Kind 'debt'"):
        tracker.progress(tracker.get(1), "Review", "Awaiting review", "progress-0001")
    assert api.status["board-project:issue-1"] == "Review"
    assert api.status["board-owner:issue-1"] == "Todo"


def test_progress_rejects_unverified_board_readback_and_done_open_issue(
    tracker: Any, api: FakeAPI
) -> None:
    tracker.create(item(), "request-0001")
    issue = tracker.get(1)
    with pytest.raises(records.ItemError, match="Done requires a closed issue"):
        tracker.progress(issue, "Done", "Finished", "progress-0001")
    api.readback_status = "Done"
    with pytest.raises(records.ItemError, match="did not record Status"):
        tracker.progress(issue, "Todo", "Awaiting review", "progress-0002")


def test_mismatched_comment_readback_prevents_progress_and_close(
    tracker: Any, api: FakeAPI, tmp_path: Path
) -> None:
    tracker.create(item(), "request-0001")
    for project in api.projects:
        project["fields"]["nodes"][0]["options"].append(
            {"id": "review", "name": "Review"}
        )
    api.comment_readback[1] = {"id": 1, "body": "Changed"}
    with pytest.raises(records.ItemError, match="changed since successful POST"):
        tracker.progress(tracker.get(1), "Review", "Awaiting review", "progress-0001")
    assert api.status["board-project:issue-1"] == "Todo"
    assert api.status["board-owner:issue-1"] == "Todo"

    text = tmp_path / "verification.txt"
    text.write_text("Observed tests passed", encoding="utf-8")
    api.comment_readback[2] = {"id": 2, "body": "Changed"}
    args = argparse.Namespace(
        command="close",
        root=str(tmp_path),
        item="owner/project#1",
        request_id="close-0001",
        text_file=str(text),
        reconcile_only=False,
    )
    with (
        patch.object(github, "GitHub", return_value=api),
        patch.object(github, "selection", return_value=CONFIG),
        patch.object(github, "RequestJournal", return_value=None),
        pytest.raises(records.ItemError, match="changed since successful POST"),
    ):
        github.execute(args)
    assert api.issues[0]["state"] == "open"
    assert api.status["board-project:issue-1"] == "Todo"
    assert api.status["board-owner:issue-1"] == "Todo"


@pytest.mark.parametrize(
    "value",
    [
        "1",
        "other/project#1",
        "owner/project#0",
        "https://github.com/owner/project/pull/1",
        "owner/project#" + chr(0x661),
    ],
)
def test_issue_identity_never_crosses_selected_repository(value: str) -> None:
    assert github.issue_number("owner/project#1", "owner/project") == 1
    assert (
        github.issue_number(
            "https://github.com/owner/project/issues/1", "owner/project"
        )
        == 1
    )
    with pytest.raises(records.ItemError):
        github.issue_number(value, "owner/project")


def stale_lists(api: FakeAPI) -> None:
    def request(
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        paginate: bool = False,
    ) -> Any:
        if (
            method == "GET"
            and endpoint == "repos/owner/project/issues?state=all&per_page=100"
        ):
            return []
        if method == "GET" and endpoint.endswith("/comments?per_page=100"):
            return []
        override = api.request_override
        api.request_override = None
        try:
            return api.request(method, endpoint, payload, paginate)
        finally:
            api.request_override = override

    api.request_override = request


def journal_tracker(tmp_path: Path) -> tuple[FakeAPI, Any, Any]:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    api = FakeAPI()
    journal = RequestJournal(tmp_path)
    return api, journal, github.Tracker(CONFIG, api, journal)


def test_repeat_uses_returned_identity_even_when_list_omits_new_issue(
    tmp_path: Path,
) -> None:
    api, _, tracker = journal_tracker(tmp_path)
    created = tracker.create(item(), "request-0001")
    stale_lists(api)
    reopened = github.Tracker(CONFIG, api, RequestJournal(tmp_path))
    repeated = reopened.create(item(), "request-0001")
    assert created["identity"] == repeated["identity"]
    assert repeated["reconciled"] is True
    assert len(api.issues) == 1
    with pytest.raises(records.ItemError, match="differs"):
        reopened.create({**item(), "title": "Different"}, "request-0001")


def test_uncertain_post_and_stale_list_never_repost_on_retry(tmp_path: Path) -> None:
    api, _, tracker = journal_tracker(tmp_path)
    stale_lists(api)
    api.fail_after_create = True
    for _ in range(2):
        with pytest.raises(records.ItemError, match="uncertain"):
            tracker.create(item(), "request-0001")
    assert len(api.issues) == 1
    with pytest.raises(records.ItemError, match="uncertain"):
        tracker.create(item(), "cross-machine-request", reconcile_only=True)
    assert len(api.issues) == 1


def test_comment_receipts_reconcile_by_identity_when_list_is_stale(
    tmp_path: Path,
) -> None:
    api, _, tracker = journal_tracker(tmp_path)
    tracker.create(item(), "request-0001")
    first = tracker.comment(1, "Verified", "comment-0001")
    stale_lists(api)
    assert first == tracker.comment(1, "Verified", "comment-0001")
    assert len(api.comments) == 1
    with pytest.raises(records.ItemError, match="uncertain"):
        tracker.comment(
            1, "Uncertain on another computer", "comment-0002", reconcile_only=True
        )
    assert len(api.comments) == 1
    api.comments[0]["body"] = "Manual edit"
    with pytest.raises(records.ItemError, match="changed"):
        tracker.comment(1, "Verified", "comment-0001")


def test_reserved_and_corrupt_receipts_fail_closed(tmp_path: Path) -> None:
    _, journal, _ = journal_tracker(tmp_path)
    journal.reserve("request", "fingerprint")
    with pytest.raises(records.ItemError, match="already reserved"):
        journal.reserve("request", "fingerprint")
    journal.path("request").write_text("invalid", encoding="utf-8")
    with pytest.raises(records.ItemError, match="unreadable"):
        journal.read("request", "fingerprint")


@pytest.mark.parametrize(
    "selection",
    [
        {
            "schema_version": 1,
            "plugins": "personal@tj-agents",
            "marketplaces": {"tj-agents": {"repository": "tj-agents/marketplace"}},
        },
        {
            "schema_version": 1,
            "plugins": ["personal@tj-agents", 1],
            "marketplaces": {"tj-agents": {"repository": "tj-agents/marketplace"}},
        },
        {
            "schema_version": 1,
            "plugins": ["personal@tj-agents"],
            "marketplaces": "tj-agents",
        },
        {
            "schema_version": 1,
            "plugins": ["personal@tj-agents"],
            "marketplaces": {"tj-agents": "marketplace"},
        },
        {
            "schema_version": 1,
            "plugins": ["personal@tj-agents"],
            "marketplaces": {"tj-agents": {"repository": ["tj-agents/marketplace"]}},
        },
    ],
)
def test_malformed_personal_adoption_selections_are_refused(
    tmp_path: Path, selection: dict[str, Any]
) -> None:
    agents = tmp_path / ".agents"
    agents.mkdir()
    path = agents / "plugins.json"
    path.write_text(json.dumps(selection), encoding="utf-8")
    with pytest.raises(records.ItemError, match="explicit personal"):
        github.personal_adoption(tmp_path)


def test_personal_selection_alone_selects_the_origin_tracker(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    with pytest.raises(records.ItemError, match="explicit personal"):
        github.selection(tmp_path)
    select_personal(tmp_path)
    with patch.object(github, "source_repository", return_value="owner/project"):
        config = github.selection(tmp_path)
    assert config["repository"] == "owner/project"
    assert config["provider"] == "github:work-item"
    assert config["issue_types"] == github.ISSUE_TYPES
    (tmp_path / ".problem-records.json").write_text(
        json.dumps({"schema_version": 1, "provider": "infonetica:work-item"}),
        encoding="utf-8",
    )
    with pytest.raises(records.ItemError, match="another problem-record provider"):
        github.selection(tmp_path)


def test_explicit_github_provider_does_not_require_personal_adoption(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".problem-records.json").write_text(
        json.dumps({"schema_version": 1, "provider": "github:work-item"}),
        encoding="utf-8",
    )
    with patch.object(github, "source_repository", return_value="owner/project"):
        config = github.selection(tmp_path)
    assert config["provider"] == "github:work-item"


def test_gh_transport_uses_argument_arrays_and_flattens_all_pages() -> None:
    response = subprocess.CompletedProcess(
        [], 0, stdout='[[{"number":1}],[{"number":2}]]', stderr=""
    )
    with (
        patch.object(github.shutil, "which", return_value="gh.exe"),
        patch.object(github.subprocess, "run", return_value=response) as run,
    ):
        result = github.GitHub().request(
            "GET", "repos/owner/project/issues", paginate=True
        )
    assert [value["number"] for value in result] == [1, 2]
    args, kwargs = run.call_args
    assert "--slurp" in args[0]
    assert "shell" not in kwargs
    assert kwargs["encoding"] == "utf-8"


@pytest.mark.skipif(os.name != "nt", reason="Windows executable selection")
def test_gh_transport_uses_the_front_path_bot_identity_shim(tmp_path: Path) -> None:
    shim = tmp_path / "bot-identity"
    real = tmp_path / "real-gh"
    shim.mkdir()
    real.mkdir()
    recorder = shim / "dummy.py"
    recorder.write_text(
        "import json\n"
        "import os\n"
        "from pathlib import Path\n"
        "import sys\n"
        "record = {'argv': sys.argv[1:], 'agent': os.environ.get('TJ_AGENT')}\n"
        "output = Path(__file__).with_name('arguments.json')\n"
        "output.write_text(json.dumps(record))\n"
        "print('[[{\\\"number\\\":1}]]')\n",
        encoding="utf-8",
    )
    (shim / "gh.cmd").write_text(
        f'@"{sys.executable}" -B "{recorder}" gh %*\r\n', encoding="utf-8"
    )
    (real / "gh.exe").touch()
    path = os.pathsep.join((str(shim), str(real)))
    previous = {
        name: github.os.environ.get(name) for name in ("PATH", "PATHEXT", "TJ_AGENT")
    }
    try:
        github.os.environ.update(
            {"PATH": path, "PATHEXT": ".EXE;.CMD", "TJ_AGENT": "1"}
        )
        result = github.GitHub().request(
            "GET", "repos/owner/project/issues?state=all&per_page=100", paginate=True
        )
    finally:
        for name, value in previous.items():
            if value is None:
                github.os.environ.pop(name, None)
            else:
                github.os.environ[name] = value
    recorded = json.loads((shim / "arguments.json").read_text(encoding="utf-8"))
    assert result == [{"number": 1}]
    assert recorded["agent"] == "1"
    assert recorded["argv"][-1] == "repos/owner/project/issues"
    assert "?" not in recorded["argv"][-1]
    assert [
        recorded["argv"][index + 1]
        for index, value in enumerate(recorded["argv"])
        if value == "--raw-field"
    ] == ["state=all", "per_page=100"]
    assert "--paginate" in recorded["argv"]
    assert "--slurp" in recorded["argv"]
