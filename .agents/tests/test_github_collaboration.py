import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from github_collaboration import GhAPI, GitHubError, execute, main, validate

ROOT = "repos/example/project"
PULL = f"{ROOT}/pulls/42"
ISSUE = f"{ROOT}/issues/42"
HEAD = "a" * 40
ACTOR = "test-agent[bot]"


class FakeAPI:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {
            ISSUE: {
                "html_url": "https://github.com/example/project/pull/42",
                "title": "Example",
                "state": "open",
                "user": {"login": "author"},
                "pull_request": {"url": f"https://api.github.com/{PULL}"},
            },
            PULL: {
                "head": {"sha": HEAD},
                "base": {"sha": "b" * 40},
                "draft": False,
            },
            ISSUE + "/comments": [],
            PULL + "/comments": [],
            PULL + "/reviews": [],
            PULL + "/files": [
                {
                    "filename": "source/main.py",
                    "status": "modified",
                    "patch": "@@ -10,3 +10,3 @@\n context\n-old\n+new\n end",
                }
            ],
        }
        self.calls: list[tuple[str, str]] = []
        self.posts: list[dict[str, Any]] = []
        self.failure: GitHubError | None = None
        self.persist_on_failure = False

    def request(
        self,
        endpoint: str,
        *,
        method: str = "GET",
        payload: Mapping[str, Any] | None = None,
        paginate: bool = False,
    ) -> Any:
        endpoint = endpoint.split("?")[0]
        self.calls.append((method, endpoint))
        if method == "GET":
            return self.values[endpoint]
        assert payload is not None
        self.posts.append(dict(payload))
        if self.failure is not None and not self.persist_on_failure:
            raise self.failure
        resource = {
            "id": len(self.posts) + 100,
            "html_url": "https://github.com/example/project/pull/42#comment-101",
            "user": {"login": ACTOR},
            "body": payload["body"],
        }
        destination = endpoint
        if "/replies" in endpoint:
            resource["in_reply_to_id"] = int(endpoint.split("/")[-2])
            destination = PULL + "/comments"
        if "commit_id" in payload:
            resource["commit_id"] = payload["commit_id"]
        self.values[destination].append(resource)
        if self.failure is not None:
            raise self.failure
        return resource


@pytest.fixture
def api() -> FakeAPI:
    return FakeAPI()


@pytest.fixture
def request_data() -> dict[str, Any]:
    return {
        "operation": "comment-create",
        "host": "github.com",
        "repository": "example/project",
        "number": 42,
        "target_kind": "pull",
        "request_id": "comment-42-1",
        "expected_actor": ACTOR,
        "body": (
            "Unicode: \u2603; quotes: '\"; literal: $() and \u0060x\u0060\nnext line"
        ),
    }


def review_request() -> dict[str, Any]:
    return {
        "operation": "review-create",
        "host": "github.com",
        "repository": "example/project",
        "number": 42,
        "expected_head": HEAD,
        "request_id": "review-42-1",
        "expected_actor": ACTOR,
        "body": "One finding",
        "event": "COMMENT",
        "comments": [
            {"path": "source/main.py", "body": "Fix this", "side": "RIGHT", "line": 11}
        ],
    }


def test_comment_preserves_body_and_returns_only_receipt(
    api: FakeAPI,
    request_data: dict[str, Any],
) -> None:
    result = execute(api, request_data)
    assert result["status"] == "created"
    assert result["actor"] == ACTOR
    assert "body" not in result
    assert api.posts[0]["body"].startswith(request_data["body"] + "\n\n<!--")
    assert len(api.posts) == 1


def test_identical_request_reuses_remote_comment(
    api: FakeAPI,
    request_data: dict[str, Any],
) -> None:
    first = execute(api, request_data)
    second = execute(api, request_data)
    assert second["status"] == "reused"
    assert first["id"] == second["id"]
    assert len(api.posts) == 1


def test_changed_payload_under_request_id_conflicts(
    api: FakeAPI,
    request_data: dict[str, Any],
) -> None:
    execute(api, request_data)
    request_data["body"] = "Changed"
    with pytest.raises(GitHubError, match="another payload") as caught:
        execute(api, request_data)
    assert caught.value.code == "request-conflict"
    assert len(api.posts) == 1


def test_different_authors_marker_is_not_reused(
    api: FakeAPI,
    request_data: dict[str, Any],
) -> None:
    execute(api, request_data)
    api.values[ISSUE + "/comments"][0]["user"] = None
    execute(api, request_data)
    assert len(api.posts) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"number": True},
        {"repository": "../other"},
        {"host": "https://github.com"},
        {"operation": []},
        {"authorized": True},
        {"request_id": "bad-->"},
        {"body": ""},
        {"target_kind": "discussion"},
    ],
)
def test_invalid_input_never_calls_api(
    api: FakeAPI,
    request_data: dict[str, Any],
    change: dict[str, Any],
) -> None:
    request_data.update(change)
    with pytest.raises(ValueError):
        execute(api, request_data)
    assert api.calls == []


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/example/other/pull/42",
        "https://other.example/example/project/pull/42",
        "https://github.com/example/project/pull/43",
    ],
)
def test_wrong_target_never_posts(
    api: FakeAPI,
    request_data: dict[str, Any],
    url: str,
) -> None:
    api.values[ISSUE]["html_url"] = url
    with pytest.raises(GitHubError, match="identity"):
        execute(api, request_data)
    assert api.posts == []


def test_issue_comment_checks_distinct_target_kind(
    api: FakeAPI,
    request_data: dict[str, Any],
) -> None:
    request_data["target_kind"] = "issue"
    with pytest.raises(GitHubError, match="identity"):
        execute(api, request_data)
    api.values[ISSUE].pop("pull_request")
    api.values[ISSUE]["html_url"] = "https://github.com/example/project/issues/42"
    assert execute(api, request_data)["status"] == "created"


def test_stale_head_never_posts(api: FakeAPI) -> None:
    api.values[PULL]["head"]["sha"] = "c" * 40
    with pytest.raises(GitHubError, match="head changed"):
        execute(api, review_request())
    assert api.posts == []


def test_review_batches_exact_coordinates(api: FakeAPI) -> None:
    request = review_request()
    request["comments"].append(
        {
            "path": "source/main.py",
            "body": "Deleted line",
            "side": "LEFT",
            "line": 11,
        }
    )
    result = execute(api, request)
    assert result["commit"] == HEAD
    assert api.posts[0]["commit_id"] == HEAD
    assert api.posts[0]["comments"] == request["comments"]
    assert len(api.posts) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"line": 99},
        {"path": "source/missing.py"},
        {"line": 11, "side": "RIGHT", "start_line": 9, "start_side": "RIGHT"},
    ],
)
def test_unavailable_anchor_never_posts(api: FakeAPI, change: dict[str, Any]) -> None:
    request = review_request()
    request["comments"][0].update(change)
    with pytest.raises(GitHubError, match=r"patch|hunk"):
        execute(api, request)
    assert api.posts == []


def test_missing_patch_never_posts(api: FakeAPI) -> None:
    api.values[PULL + "/files"][0].pop("patch")
    with pytest.raises(GitHubError, match="patch"):
        execute(api, review_request())
    assert api.posts == []


def test_rename_uses_returned_filename(api: FakeAPI) -> None:
    api.values[PULL + "/files"][0].update(
        {
            "status": "renamed",
            "previous_filename": "source/old.py",
        }
    )
    assert execute(api, review_request())["status"] == "created"
    request = review_request()
    request["request_id"] = "review-old-name"
    request["comments"][0]["path"] = "source/old.py"
    with pytest.raises(GitHubError, match="patch"):
        execute(api, request)
    assert len(api.posts) == 1


def test_deleted_file_left_anchor_is_supported(api: FakeAPI) -> None:
    api.values[PULL + "/files"][0]["patch"] = "@@ -10,2 +0,0 @@\n-old\n-lines"
    request = review_request()
    request["comments"][0].update({"side": "LEFT", "line": 11})
    assert execute(api, request)["status"] == "created"


def test_valid_range_is_supported(api: FakeAPI) -> None:
    request = review_request()
    request["comments"][0].update({"start_line": 10, "start_side": "RIGHT", "line": 12})
    assert execute(api, request)["status"] == "created"


def test_timeout_reconciles_success_without_second_post(
    api: FakeAPI,
    request_data: dict[str, Any],
) -> None:
    api.failure = GitHubError("transport-timeout", "Timeout", uncertain=True)
    api.persist_on_failure = True
    assert execute(api, request_data)["status"] == "reused"
    assert len(api.posts) == 1


def test_uncertain_failure_without_visible_resource_is_explicit(
    api: FakeAPI,
    request_data: dict[str, Any],
) -> None:
    api.failure = GitHubError("transport-timeout", "Timeout", uncertain=True)
    with pytest.raises(GitHubError) as caught:
        execute(api, request_data)
    assert caught.value.code == "outcome-unknown"
    assert caught.value.uncertain
    assert len(api.posts) == 1


@pytest.mark.parametrize("code", ["permission-denied", "rate-limited"])
def test_definite_failure_is_not_retried(
    api: FakeAPI,
    request_data: dict[str, Any],
    code: str,
) -> None:
    api.failure = GitHubError(code, "Rejected")
    with pytest.raises(GitHubError, match="Rejected"):
        execute(api, request_data)
    assert api.calls.count(("GET", ISSUE + "/comments")) == 1
    assert len(api.posts) == 1


def test_reply_verifies_pr_and_root_comment(api: FakeAPI) -> None:
    request = review_request()
    request.update({"operation": "review-reply", "comment_id": 7})
    request.pop("comments")
    request.pop("event")
    api.values[ROOT + "/pulls/comments/7"] = {
        "pull_request_url": f"https://api.github.com/{PULL}",
    }
    assert execute(api, request)["status"] == "created"
    assert api.calls[-1] == ("POST", PULL + "/comments/7/replies")


@pytest.mark.parametrize(
    "parent",
    [
        {"pull_request_url": "https://api.github.com/repos/example/other/pulls/42"},
        {"pull_request_url": f"https://api.github.com/{PULL}", "in_reply_to_id": 5},
    ],
)
def test_wrong_review_thread_never_posts(api: FakeAPI, parent: dict[str, Any]) -> None:
    request = review_request()
    request.update({"operation": "review-reply", "comment_id": 7})
    request.pop("comments")
    request.pop("event")
    api.values[ROOT + "/pulls/comments/7"] = parent
    with pytest.raises(GitHubError, match=r"another pull|top-level"):
        execute(api, request)
    assert api.posts == []


def test_context_omits_discussion_by_default(api: FakeAPI) -> None:
    request = {
        "operation": "pr-context",
        "host": "github.com",
        "repository": "example/project",
        "number": 42,
    }
    result = execute(api, request)
    assert result["head"] == HEAD
    assert "discussion" not in result
    request["include_discussion"] = True
    assert execute(api, request)["discussion"]["reviews"] == []


def test_transport_uses_stdin_and_flattens_all_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []

    def run(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append(arguments)
        assert kwargs["input"] is None
        return subprocess.CompletedProcess(arguments, 0, '[[{"id":1}],[{"id":2}]]', "")

    monkeypatch.setattr("github_collaboration.shutil.which", lambda _: "gh")
    monkeypatch.setattr("github_collaboration.subprocess.run", run)
    assert GhAPI("github.com").request(ISSUE + "/comments", paginate=True) == [
        {"id": 1},
        {"id": 2},
    ]
    assert "--paginate" in seen[0] and "--slurp" in seen[0]


def test_transport_keeps_comment_body_out_of_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = "quotes '\" $(whoami)\nUnicode \u2603"

    def run(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert body not in arguments
        assert json.loads(kwargs["input"]) == {"body": body}
        assert arguments[-2:] == ["--input", "-"]
        return subprocess.CompletedProcess(arguments, 0, '{"id":1}', "")

    monkeypatch.setattr("github_collaboration.shutil.which", lambda _: "gh")
    monkeypatch.setattr("github_collaboration.subprocess.run", run)
    GhAPI("github.com").request(
        ISSUE + "/comments", method="POST", payload={"body": body}
    )


def test_invalid_file_produces_structured_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "request.json"
    path.write_text('{"operation":"not-an-operation"}', encoding="utf-8")
    assert main(["--input", str(path)]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "invalid-input"


def test_unknown_fields_fail_before_authority_can_be_implied() -> None:
    request = review_request()
    request["authorized"] = True
    with pytest.raises(ValueError, match="Unknown fields"):
        validate(request)


def test_head_movement_during_patch_fetch_never_posts() -> None:
    class MovingAPI(FakeAPI):
        def request(
            self,
            endpoint: str,
            *,
            method: str = "GET",
            payload: Mapping[str, Any] | None = None,
            paginate: bool = False,
        ) -> Any:
            result = super().request(
                endpoint,
                method=method,
                payload=payload,
                paginate=paginate,
            )
            if endpoint.startswith(PULL + "/files"):
                self.values[PULL]["head"]["sha"] = "c" * 40
            return result

    api = MovingAPI()
    with pytest.raises(GitHubError, match="during validation"):
        execute(api, review_request())
    assert api.posts == []


def test_existing_review_is_reconciled_after_head_moves(api: FakeAPI) -> None:
    request = review_request()
    first = execute(api, request)
    api.values[PULL]["head"]["sha"] = "c" * 40
    second = execute(api, request)
    assert second["id"] == first["id"]
    assert second["commit"] == HEAD
    assert len(api.posts) == 1


def test_ranges_cannot_cross_diff_hunks(api: FakeAPI) -> None:
    api.values[PULL + "/files"][0]["patch"] += "\n@@ -20 +20 @@\n+more"
    request = review_request()
    request["comments"][0].update({"line": 20, "start_line": 11, "start_side": "RIGHT"})
    with pytest.raises(GitHubError, match="hunk"):
        execute(api, request)
    assert api.posts == []


@pytest.mark.parametrize(
    ("status", "uncertain"),
    [
        ("403", False),
        ("429", False),
        ("503", True),
    ],
)
def test_transport_classifies_rejections_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    uncertain: bool,
) -> None:
    calls = []

    def run(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert "env" not in kwargs
        calls.append(arguments)
        return subprocess.CompletedProcess(
            arguments,
            1,
            json.dumps({"message": "Rejected", "status": status}),
            "",
        )

    monkeypatch.setattr("github_collaboration.shutil.which", lambda _: "gh")
    monkeypatch.setattr("github_collaboration.subprocess.run", run)
    with pytest.raises(GitHubError) as caught:
        GhAPI("github.com").request(
            ISSUE + "/comments", method="POST", payload={"body": "x"}
        )
    assert caught.value.uncertain is uncertain
    assert len(calls) == 1


def test_transport_timeout_marks_write_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(arguments, 60)

    monkeypatch.setattr("github_collaboration.shutil.which", lambda _: "gh")
    monkeypatch.setattr("github_collaboration.subprocess.run", run)
    with pytest.raises(GitHubError) as caught:
        GhAPI("github.com").request(
            ISSUE + "/comments", method="POST", payload={"body": "x"}
        )
    assert caught.value.uncertain


def test_missing_gh_is_a_clear_prerequisite_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("github_collaboration.shutil.which", lambda _: None)
    with pytest.raises(GitHubError, match="Install and authenticate"):
        GhAPI("github.com")


def test_actor_mismatch_reports_created_resource(
    api: FakeAPI,
    request_data: dict[str, Any],
) -> None:
    request_data["expected_actor"] = "different-author"
    with pytest.raises(GitHubError, match=r"Resource 101 at https://") as caught:
        execute(api, request_data)
    assert caught.value.code == "actor-mismatch"
    assert len(api.posts) == 1
