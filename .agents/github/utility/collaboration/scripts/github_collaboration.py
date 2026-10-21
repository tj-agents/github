import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlsplit

if sys.version_info < (3, 12):
    raise SystemExit("Python 3.12 or newer is required")


class GitHubError(Exception):
    def __init__(self, code: str, message: str, *, uncertain: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.uncertain = uncertain


class API(Protocol):
    def request(
        self,
        endpoint: str,
        *,
        method: str = "GET",
        payload: Mapping[str, Any] | None = None,
        paginate: bool = False,
    ) -> Any: ...


class GhAPI:
    def __init__(self, host: str) -> None:
        executable = shutil.which("gh")
        if executable is None:
            raise GitHubError(
                "missing-prerequisite", "Install and authenticate gh first"
            )
        self.executable = executable
        self.host = host

    def request(
        self,
        endpoint: str,
        *,
        method: str = "GET",
        payload: Mapping[str, Any] | None = None,
        paginate: bool = False,
    ) -> Any:
        arguments = [
            self.executable,
            "api",
            "--hostname",
            self.host,
            "--method",
            method,
            endpoint,
        ]
        if paginate:
            arguments.extend(["--paginate", "--slurp"])
        if payload is not None:
            arguments.extend(["--input", "-"])
        try:
            result = subprocess.run(
                arguments,
                input=json.dumps(payload) if payload is not None else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise GitHubError(
                "transport-timeout",
                "GitHub request timed out",
                uncertain=method != "GET",
            ) from error
        except OSError as error:
            raise GitHubError("transport-failed", "Could not execute gh") from error
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise GitHubError(
                "invalid-response",
                "gh did not return JSON",
                uncertain=method != "GET",
            ) from error
        if result.returncode:
            status = str(value.get("status", "")) if isinstance(value, dict) else ""
            uncertain = method != "GET" and (not status or status.startswith("5"))
            raise GitHubError(
                "github-rejected",
                f"GitHub request failed (HTTP {status or 'unknown'})",
                uncertain=uncertain,
            )
        if paginate:
            if not isinstance(value, list) or any(
                not isinstance(page, list) for page in value
            ):
                raise GitHubError(
                    "invalid-response", "Expected paginated GitHub arrays"
                )
            return [item for page in value for item in page]
        if not isinstance(value, dict):
            raise GitHubError(
                "invalid-response",
                "Expected a GitHub resource object",
                uncertain=method != "GET",
            )
        return value


def nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value


def positive(value: Any, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def rows(api: API, endpoint: str) -> list[dict[str, Any]]:
    result = api.request(endpoint + "?per_page=100", paginate=True)
    if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
        raise GitHubError("invalid-response", "Expected a GitHub resource list")
    return result


def validate(request: Mapping[str, Any]) -> None:
    operations = {
        "pr-context",
        "issue-context",
        "comment-create",
        "review-create",
        "review-reply",
    }
    operation = request.get("operation")
    if not isinstance(operation, str) or operation not in operations:
        raise ValueError("Unknown operation")
    common = {"operation", "host", "repository", "number"}
    fields = {
        "pr-context": {"include_discussion"},
        "issue-context": {"include_discussion"},
        "comment-create": {"target_kind", "request_id", "expected_actor", "body"},
        "review-create": {
            "request_id",
            "expected_actor",
            "body",
            "expected_head",
            "event",
            "comments",
        },
        "review-reply": {
            "request_id",
            "expected_actor",
            "body",
            "expected_head",
            "comment_id",
        },
    }
    unknown = request.keys() - common - fields[operation]
    if unknown:
        raise ValueError(f"Unknown fields: {', '.join(sorted(unknown))}")
    host = nonempty(request.get("host"), "host")
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host):
        raise ValueError("host must be a GitHub hostname, without a URL or port")
    repository = nonempty(request.get("repository"), "repository")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", repository):
        raise ValueError("repository must be owner/name")
    if repository.split("/")[1] in {".", ".."}:
        raise ValueError("Invalid repository name")
    positive(request.get("number"), "number")
    if operation.endswith("context"):
        if type(request.get("include_discussion", False)) is not bool:
            raise ValueError("include_discussion must be a boolean")
        return
    identifier = nonempty(request.get("request_id"), "request_id")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identifier):
        raise ValueError(
            "request_id must contain 1-100 letters, digits, hyphens or underscores"
        )
    actor = nonempty(request.get("expected_actor"), "expected_actor")
    if not re.fullmatch(r"[A-Za-z0-9-]+(?:\[bot\])?", actor):
        raise ValueError("expected_actor must be a GitHub login")
    nonempty(request.get("body"), "body")
    if operation == "comment-create":
        if request.get("target_kind") not in {"issue", "pull"}:
            raise ValueError("target_kind must be issue or pull")
        return
    if not re.fullmatch(r"[a-fA-F0-9]{40}", str(request.get("expected_head", ""))):
        raise ValueError("expected_head must be a full commit SHA")
    if operation == "review-reply":
        positive(request.get("comment_id"), "comment_id")
        return
    if request.get("event") not in {"COMMENT", "APPROVE", "REQUEST_CHANGES"}:
        raise ValueError("event must be COMMENT, APPROVE or REQUEST_CHANGES")
    comments = request.get("comments", [])
    if not isinstance(comments, list) or len(comments) > 100:
        raise ValueError("comments must be an array of at most 100 inline comments")
    for comment in comments:
        validate_comment(comment)


def validate_comment(comment: Any) -> None:
    if not isinstance(comment, dict):
        raise ValueError("Each inline comment must be an object")
    unknown = comment.keys() - {
        "path",
        "body",
        "line",
        "side",
        "start_line",
        "start_side",
    }
    if unknown:
        raise ValueError("Unsupported inline comment fields")
    path = nonempty(comment.get("path"), "path")
    parsed = PurePosixPath(path)
    if (
        parsed.is_absolute()
        or ".." in parsed.parts
        or "\\" in path
        or str(parsed) != path
    ):
        raise ValueError("path must be a canonical repository-relative path")
    nonempty(comment.get("body"), "inline body")
    positive(comment.get("line"), "line")
    if comment.get("side") not in {"LEFT", "RIGHT"}:
        raise ValueError("side must be LEFT or RIGHT")
    if ("start_line" in comment) != ("start_side" in comment):
        raise ValueError("A range requires both start_line and start_side")
    if "start_line" in comment:
        start = positive(comment["start_line"], "start_line")
        if start >= comment["line"] or comment["start_side"] != comment["side"]:
            raise ValueError("A range must increase on one diff side")


def anchors(patch: str) -> dict[tuple[str, int], int]:
    result = {}
    left = right = group = 0
    for line in patch.splitlines():
        match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if match:
            left, right = map(int, match.groups())
            group += 1
        elif group and line.startswith(" "):
            result["LEFT", left] = result["RIGHT", right] = group
            left += 1
            right += 1
        elif group and line.startswith("-"):
            result["LEFT", left] = group
            left += 1
        elif group and line.startswith("+"):
            result["RIGHT", right] = group
            right += 1
    return result


def validate_anchors(
    api: API, prefix: str, comments: Sequence[Mapping[str, Any]]
) -> None:
    if not comments:
        return
    files = {item["filename"]: item for item in rows(api, prefix + "/files")}
    for comment in comments:
        file = files.get(comment["path"])
        if file is None or not isinstance(file.get("patch"), str):
            raise GitHubError("invalid-anchor", "Changed-file patch is unavailable")
        locations = anchors(file["patch"])
        group = locations.get((comment["side"], comment["line"]))
        start = comment.get("start_line", comment["line"])
        if group is None or any(
            locations.get((comment["side"], line)) != group
            for line in range(start, comment["line"] + 1)
        ):
            raise GitHubError(
                "invalid-anchor", "Inline range is outside one available diff hunk"
            )


def marker(request: Mapping[str, Any]) -> tuple[str, str]:
    payload = {key: value for key, value in request.items() if key != "request_id"}
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    prefix = f"<!-- tj-agents-github:{request['request_id']}:"
    return prefix, f"{prefix}{fingerprint} -->"


def receipt(
    request: Mapping[str, Any],
    resource: Mapping[str, Any],
    status: str,
) -> dict[str, Any]:
    actor = (resource.get("user") or {}).get("login")
    result = {
        "status": status,
        "operation": request["operation"],
        "host": request["host"],
        "repository": request["repository"],
        "number": request["number"],
        "id": resource.get("id"),
        "url": resource.get("html_url"),
        "actor": actor,
    }
    if "expected_head" in request:
        result["commit"] = resource.get("commit_id", request["expected_head"])
    if actor != request["expected_actor"]:
        raise GitHubError(
            "actor-mismatch",
            f"Resource {result['id']} at {result['url']} "
            f"was written by {actor}, not the expected actor",
        )
    if not result["id"] or not result["url"]:
        raise GitHubError(
            "invalid-response", "GitHub omitted the resource identity", uncertain=True
        )
    return result


def existing(
    api: API,
    request: Mapping[str, Any],
    endpoint: str,
    marker_prefix: str,
    full_marker: str,
) -> dict[str, Any] | None:
    for resource in rows(api, endpoint):
        if (resource.get("user") or {}).get("login") != request["expected_actor"]:
            continue
        body = resource.get("body") or ""
        if marker_prefix not in body:
            continue
        if full_marker not in body:
            raise GitHubError(
                "request-conflict", "request_id already identifies another payload"
            )
        if (
            request["operation"] == "review-reply"
            and resource.get("in_reply_to_id") != request["comment_id"]
        ):
            raise GitHubError(
                "request-conflict", "request_id belongs to another review thread"
            )
        return receipt(request, resource, "reused")
    return None


def context(
    api: API,
    request: Mapping[str, Any],
    target: Mapping[str, Any],
    root: str,
) -> dict[str, Any]:
    result = {
        "status": "ok",
        "host": request["host"],
        "repository": request["repository"],
        "number": request["number"],
        "url": target["html_url"],
        "title": target["title"],
        "state": target["state"],
        "author": (target.get("user") or {}).get("login"),
        "labels": [label["name"] for label in target.get("labels", [])],
    }
    if request["operation"] == "pr-context":
        pull = api.request(f"{root}/pulls/{request['number']}")
        result.update(
            {
                "head": pull["head"]["sha"],
                "base": pull["base"]["sha"],
                "draft": pull.get("draft", False),
                "files": [
                    {
                        key: item.get(key)
                        for key in ("filename", "previous_filename", "status")
                    }
                    for item in rows(api, f"{root}/pulls/{request['number']}/files")
                ],
            }
        )
    if request.get("include_discussion", False):
        endpoints = {"conversation": f"{root}/issues/{request['number']}/comments"}
        if request["operation"] == "pr-context":
            endpoints.update(
                {
                    "reviews": f"{root}/pulls/{request['number']}/reviews",
                    "inline": f"{root}/pulls/{request['number']}/comments",
                }
            )
        result["discussion"] = {
            name: [
                {
                    "id": item["id"],
                    "url": item["html_url"],
                    "actor": item["user"]["login"],
                    "body": item.get("body"),
                    **{
                        key: item[key]
                        for key in (
                            "state",
                            "path",
                            "line",
                            "side",
                            "commit_id",
                            "in_reply_to_id",
                        )
                        if key in item
                    },
                }
                for item in rows(api, endpoint)
            ]
            for name, endpoint in endpoints.items()
        }
    return result


def execute(api: API, request: Mapping[str, Any]) -> dict[str, Any]:
    validate(request)
    root = f"repos/{request['repository']}"
    number = request["number"]
    operation = request["operation"]
    target = api.request(f"{root}/issues/{number}")
    kind = "pull" if "pull_request" in target else "issue"
    expected_kind = request.get(
        "target_kind", "issue" if operation == "issue-context" else "pull"
    )
    segment = "pull" if expected_kind == "pull" else "issues"
    expected_url = f"/{request['repository']}/{segment}/{number}"
    url = urlsplit(target.get("html_url", ""))
    if (
        kind != expected_kind
        or url.hostname != request["host"].lower()
        or url.path.lower() != expected_url.lower()
    ):
        raise GitHubError(
            "wrong-target", "GitHub target identity differs from the request"
        )
    if operation.endswith("context"):
        return context(api, request, target, root)
    prefix, full_marker = marker(request)
    body = request["body"] + "\n\n" + full_marker
    pull_prefix = f"{root}/pulls/{number}"
    endpoint = f"{root}/issues/{number}/comments"
    payload = {"body": body}
    search_endpoint = endpoint
    if operation == "review-create":
        endpoint = pull_prefix + "/reviews"
        search_endpoint = endpoint
        payload = {
            "body": body,
            "commit_id": request["expected_head"],
            "event": request["event"],
            "comments": request.get("comments", []),
        }
    elif operation == "review-reply":
        search_endpoint = pull_prefix + "/comments"
        parent = api.request(f"{root}/pulls/comments/{request['comment_id']}")
        if parent.get("pull_request_url") != target["pull_request"]["url"]:
            raise GitHubError(
                "wrong-thread", "Review comment belongs to another pull request"
            )
        if parent.get("in_reply_to_id") is not None:
            raise GitHubError(
                "wrong-thread", "Reply requires the thread's top-level comment"
            )
        endpoint = f"{pull_prefix}/comments/{request['comment_id']}/replies"
    prior = existing(api, request, search_endpoint, prefix, full_marker)
    if prior is not None:
        return prior
    if "expected_head" in request:
        pull = api.request(pull_prefix)
        if pull["head"]["sha"].lower() != request["expected_head"].lower():
            raise GitHubError(
                "stale-head", "Pull request head changed; refresh the review"
            )
        if operation == "review-create":
            validate_anchors(api, pull_prefix, request.get("comments", []))
            current = api.request(pull_prefix)
            if current["head"]["sha"].lower() != request["expected_head"].lower():
                raise GitHubError(
                    "stale-head", "Pull request head changed during validation"
                )
    try:
        resource = api.request(endpoint, method="POST", payload=payload)
    except GitHubError as error:
        if not error.uncertain:
            raise
        try:
            recovered = existing(api, request, search_endpoint, prefix, full_marker)
        except GitHubError:
            recovered = None
        if recovered is not None:
            return recovered
        raise GitHubError(
            "outcome-unknown",
            "Posting outcome is unknown; retain request_id "
            "and reconcile before retrying",
            uncertain=True,
        ) from error
    return receipt(request, resource, "created")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input", required=True, help="UTF-8 JSON file, or - for stdin"
    )
    arguments = parser.parse_args(argv)
    try:
        raw = (
            sys.stdin.read()
            if arguments.input == "-"
            else Path(arguments.input).read_text(encoding="utf-8")
        )
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise ValueError("Input must be a JSON object")
        validate(request)
        result = execute(GhAPI(request["host"]), request)
    except (ValueError, OSError) as error:
        print(
            json.dumps(
                {"status": "error", "code": "invalid-input", "message": str(error)}
            )
        )
        return 2
    except GitHubError as error:
        print(
            json.dumps(
                {
                    "status": "error",
                    "code": error.code,
                    "message": str(error),
                    "outcome_unknown": error.uncertain,
                }
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
