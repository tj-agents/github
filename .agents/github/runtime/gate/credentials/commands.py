from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from credentials.model import CredentialError
from credentials.repository_scope import RepositoryScope

_NAME = re.compile(r"[A-Za-z0-9_.-]{1,100}")


_SCALAR = re.compile(r"[^\x00\r\n]{1,4096}")


_KEY_VALUE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}=[^\x00\r\n]{0,4096}")


@dataclass(frozen=True, slots=True)
class Repository:
    organization: str
    name: str

    @property
    def slug(self) -> str:
        return f"{self.organization}/{self.name}"


@dataclass(frozen=True, slots=True)
class GhInvocation:
    repository: Repository
    arguments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OptionBindings:
    positionals: tuple[str, ...]
    options: tuple[str, ...]
    values: tuple[tuple[str, str], ...]
    switches: frozenset[str]

    def has_value(self, name: str) -> bool:
        return any(option == name for option, _ in self.values)

    def value(self, name: str) -> str | None:
        for option, value in self.values:
            if option == name:
                return value
        return None


def _invalid(
    message: str = "tool-invalid: unsupported employer GitHub operation",
) -> CredentialError:
    return CredentialError(message)


def _repository(profile: RepositoryScope, organization: str, name: str) -> Repository:
    if not profile.allows_repository(organization, name):
        raise _invalid("repository-denied: select an allowlisted employer repository")
    selected = (
        name
        if profile.repositories == ("*",)
        else next(
            item for item in profile.repositories if item.casefold() == name.casefold()
        )
    )
    return Repository(profile.organization, selected)


def parse_repository(profile: RepositoryScope, value: str) -> Repository:
    if not isinstance(value, str) or not value or "%" in value or "\\" in value:
        raise _invalid(
            "repository-invalid: use https://github.com/ORG/REPO or ORG/REPO"
        )
    if value.startswith("https://"):
        match = re.fullmatch(
            r"https://github\.com/([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,104})",
            value,
        )
        if match is None:
            raise _invalid(
                "repository-invalid: use a plain github.com HTTPS repository URL"
            )
        organization, name = match.groups()
    else:
        match = re.fullmatch(r"([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,104})", value)
        if match is None:
            raise _invalid(
                "repository-invalid: use https://github.com/ORG/REPO or ORG/REPO"
            )
        organization, name = match.groups()
    if name.endswith(".git"):
        name = name[:-4]
    if (
        not _NAME.fullmatch(organization)
        or not _NAME.fullmatch(name)
        or name in {".", ".."}
    ):
        raise _invalid("repository-invalid: invalid organization or repository name")
    return _repository(profile, organization, name)


def parse_https_repository(profile: RepositoryScope, value: str) -> Repository:
    if not value.startswith("https://github.com/"):
        raise _invalid(
            "repository-invalid: Git network commands require github.com HTTPS"
        )
    return parse_repository(profile, value)


def _split_repository_option(arguments: Sequence[str]) -> tuple[str | None, list[str]]:
    repository: str | None = None
    selected: list[str] = []
    index = 0
    while index < len(arguments):
        item = arguments[index]
        if item in {"-R", "--repo"}:
            if repository is not None or index + 1 == len(arguments):
                raise _invalid("repository-invalid: pass one complete --repo value")
            repository = arguments[index + 1]
            index += 2
            continue
        if item.startswith("--repo="):
            if repository is not None:
                raise _invalid("repository-invalid: pass one complete --repo value")
            repository = item.removeprefix("--repo=")
            index += 1
            continue
        selected.append(item)
        index += 1
    return repository, selected


def _resolve_repository(
    profile: RepositoryScope, explicit: str | None, default_repository: str | None
) -> Repository:
    selected = explicit if explicit is not None else default_repository
    if selected is None:
        raise _invalid(
            "repository-required: pass --repo ORG/REPO or select an employer origin"
        )
    return parse_repository(profile, selected)


def _valid_scalar(value: str) -> bool:
    return bool(_SCALAR.fullmatch(value))


_REPEATABLE_VALUE_OPTIONS = frozenset(
    {
        "--add-assignee",
        "--add-label",
        "--add-reviewer",
        "--assignee",
        "--label",
        "--remove-assignee",
        "--remove-label",
        "--remove-reviewer",
    }
)


def _parse_options(
    arguments: Sequence[str],
    allowed_values: set[str],
    allowed_switches: set[str],
    positional: int | tuple[int, int],
) -> OptionBindings:
    positionals: list[str] = []
    options: list[str] = []
    values: list[tuple[str, str]] = []
    switches: set[str] = set()
    index = 0
    while index < len(arguments):
        item = arguments[index]
        if item == "--":
            raise _invalid(
                "argument-invalid: -- is not supported for employer commands"
            )
        if item.startswith("-"):
            if item in allowed_switches:
                if item in switches:
                    raise _invalid("argument-invalid: repeated gh option")
                switches.add(item)
                options.append(item)
                index += 1
                continue
            option, separator, attached = item.partition("=")
            if option in allowed_values:
                if separator:
                    if not _valid_scalar(attached):
                        raise _invalid("argument-invalid: invalid option value")
                    value = attached
                    options.append(item)
                    index += 1
                else:
                    if index + 1 == len(arguments) or not _valid_scalar(
                        arguments[index + 1]
                    ):
                        raise _invalid("argument-invalid: missing option value")
                    value = arguments[index + 1]
                    options.extend((item, value))
                    index += 2
                if option not in _REPEATABLE_VALUE_OPTIONS and any(
                    name == option for name, _ in values
                ):
                    raise _invalid("argument-invalid: repeated gh option")
                values.append((option, value))
                continue
            raise _invalid("argument-invalid: unsupported gh option")
        if not _valid_scalar(item):
            raise _invalid("argument-invalid: invalid gh argument")
        positionals.append(item)
        index += 1
    minimum, maximum = (
        positional if isinstance(positional, tuple) else (positional, positional)
    )
    if not minimum <= len(positionals) <= maximum:
        raise _invalid("argument-invalid: unsupported gh command argument count")
    return OptionBindings(
        tuple(positionals), tuple(options), tuple(values), frozenset(switches)
    )


_READ_COMMANDS: dict[
    tuple[str, str], tuple[set[str], set[str], int | tuple[int, int]]
] = {
    ("repo", "view"): ({"--json", "--jq", "--template"}, set(), (0, 1)),
    ("pr", "list"): (
        {
            "--author",
            "--assignee",
            "--base",
            "--head",
            "--json",
            "--jq",
            "--limit",
            "--search",
            "--state",
        },
        {"--draft"},
        0,
    ),
    ("pr", "view"): ({"--json", "--jq", "--template"}, {"--comments"}, (0, 1)),
    ("pr", "checks"): (
        {"--interval"},
        {"--fail-fast", "--required", "--watch"},
        (0, 1),
    ),
    ("pr", "status"): (
        {"--json", "--jq", "--template"},
        {"--conflict-status"},
        0,
    ),
    ("issue", "list"): (
        {
            "--assignee",
            "--author",
            "--json",
            "--jq",
            "--label",
            "--limit",
            "--search",
            "--state",
        },
        set(),
        0,
    ),
    ("issue", "view"): (
        {"--json", "--jq", "--template"},
        {"--comments"},
        (0, 1),
    ),
    ("run", "list"): (
        {
            "--branch",
            "--commit",
            "--created",
            "--event",
            "--json",
            "--jq",
            "--limit",
            "--status",
            "--user",
            "--workflow",
        },
        {"--all"},
        0,
    ),
    ("run", "view"): (
        {"--attempt", "--job", "--json", "--jq"},
        {"--exit-status", "--log", "--log-failed", "--verbose"},
        (0, 1),
    ),
    ("workflow", "list"): ({"--json", "--jq", "--limit"}, {"--all"}, 0),
    ("workflow", "view"): (set(), {"--yaml"}, 1),
    ("release", "list"): (
        {
            "--json",
            "--jq",
            "--limit",
            "--order",
        },
        {"--exclude-drafts", "--exclude-pre-releases"},
        0,
    ),
    ("release", "view"): ({"--json", "--jq", "--template"}, set(), (0, 1)),
}


_WRITE_COMMANDS: dict[
    tuple[str, str], tuple[set[str], set[str], int | tuple[int, int]]
] = {
    ("pr", "create"): (
        {"--base", "--body", "--body-file", "--head", "--title"},
        {"--draft", "--fill", "--fill-first", "--fill-verbose"},
        0,
    ),
    ("pr", "edit"): (
        {
            "--add-assignee",
            "--add-label",
            "--add-reviewer",
            "--base",
            "--body",
            "--body-file",
            "--milestone",
            "--remove-assignee",
            "--remove-label",
            "--remove-reviewer",
            "--title",
        },
        {"--draft", "--ready"},
        (0, 1),
    ),
    ("pr", "comment"): (
        {"--body", "--body-file"},
        {"--create-if-none", "--edit-last"},
        (0, 1),
    ),
    ("pr", "ready"): (set(), {"--undo"}, 1),
    ("pr", "close"): (set(), {"--delete-branch"}, 1),
    ("pr", "reopen"): (set(), set(), 1),
    ("pr", "review"): (
        {"--body", "--body-file"},
        {"--approve", "--comment", "--request-changes"},
        1,
    ),
    ("pr", "merge"): (
        {"--match-head-commit"},
        {
            "--auto",
            "--delete-branch",
            "--disable-auto",
            "--merge",
            "--rebase",
            "--squash",
        },
        1,
    ),
    ("issue", "create"): (
        {
            "--assignee",
            "--body",
            "--body-file",
            "--label",
            "--milestone",
            "--project",
            "--title",
        },
        set(),
        0,
    ),
    ("issue", "comment"): (
        {"--body", "--body-file"},
        {"--create-if-none", "--edit-last"},
        1,
    ),
}


def _validate_pr_issue_reference(
    profile: RepositoryScope, value: str, repository: Repository, kind: str
) -> None:
    if value.isdecimal():
        return
    match = re.fullmatch(rf"https://github\.com/([^/]+)/([^/]+)/{kind}/(\d+)", value)
    if match is None:
        raise _invalid("argument-invalid: use a number or selected repository URL")
    if _repository(profile, match.group(1), match.group(2)) != repository:
        raise _invalid("repository-denied: URL names a different repository")


def _validate_positionals(
    profile: RepositoryScope,
    repository: Repository,
    group: str,
    command: str,
    positionals: Sequence[str],
) -> None:
    if group == "repo" and command == "view" and positionals:
        if parse_repository(profile, positionals[0]) != repository:
            raise _invalid(
                "repository-denied: repository argument disagrees with --repo"
            )
        return
    if (
        group == "pr"
        and command
        in {
            "checks",
            "close",
            "comment",
            "edit",
            "merge",
            "ready",
            "reopen",
            "review",
            "view",
        }
        and positionals
    ):
        _validate_pr_issue_reference(profile, positionals[0], repository, "pull")
        return
    if group == "issue" and command in {"comment", "view"} and positionals:
        _validate_pr_issue_reference(profile, positionals[0], repository, "issues")
        return
    if any(item.startswith("https://") for item in positionals):
        raise _invalid("argument-invalid: this command does not accept GitHub URLs")


def _validate_noninteractive(group: str, command: str, options: OptionBindings) -> None:
    body = options.has_value("--body") or options.has_value("--body-file")
    if group == "pr" and command == "create":
        fill = bool(options.switches & {"--fill", "--fill-first", "--fill-verbose"})
        if not options.has_value("--head"):
            raise _invalid("argument-invalid: pr create requires --head")
        if not options.has_value("--title") and not fill:
            raise _invalid("argument-invalid: pr create requires --title or --fill")
        if not body and not fill:
            raise _invalid("argument-invalid: pr create requires --body or --fill")
    if group == "pr" and command == "comment" and not body:
        raise _invalid("argument-invalid: pr comment requires --body or --body-file")
    if group == "issue" and command == "create":
        if not options.has_value("--title"):
            raise _invalid("argument-invalid: issue create requires --title")
        if not body:
            raise _invalid(
                "argument-invalid: issue create requires --body or --body-file"
            )
    if group == "issue" and command == "comment" and not body:
        raise _invalid("argument-invalid: issue comment requires --body or --body-file")
    if group == "pr" and command == "review":
        actions = {"--approve", "--comment", "--request-changes"}
        if len(options.switches & actions) != 1:
            raise _invalid("argument-invalid: pr review requires one review action")
        if options.switches & {"--comment", "--request-changes"} and not body:
            raise _invalid(
                "argument-invalid: review comment requires --body or --body-file"
            )
    if group == "pr" and command == "merge":
        actions = {"--auto", "--disable-auto", "--merge", "--rebase", "--squash"}
        if len(options.switches & actions) != 1:
            raise _invalid("argument-invalid: pr merge requires one merge action")


_API_FIELDS = {
    "base",
    "branch",
    "direction",
    "head",
    "labels",
    "page",
    "per_page",
    "ref",
    "sha",
    "since",
    "sort",
    "state",
}


def _validate_api(
    profile: RepositoryScope, repository: Repository, arguments: Sequence[str]
) -> tuple[str, ...]:
    endpoint: str | None = None
    output: list[str] = []
    method = "GET"
    input_stdin = False
    index = 0
    while index < len(arguments):
        item = arguments[index]
        if item in {"--paginate", "--slurp"}:
            output.append(item)
            index += 1
            continue
        if item in {"-X", "--method"}:
            if (
                index + 1 == len(arguments)
                or arguments[index + 1].upper() not in {"GET", "POST"}
                or method != "GET"
            ):
                raise _invalid("api-invalid: unsupported REST method")
            method = arguments[index + 1].upper()
            index += 2
            continue
        if item == "--input":
            if (
                input_stdin
                or index + 1 == len(arguments)
                or arguments[index + 1] != "-"
            ):
                raise _invalid("api-invalid: review input must be stdin")
            input_stdin = True
            index += 2
            continue
        if item in {"-f", "--raw-field", "-F", "--field", "--jq", "--cache"}:
            if index + 1 == len(arguments) or not _valid_scalar(arguments[index + 1]):
                raise _invalid("api-invalid: missing API option value")
            value = arguments[index + 1]
            if item in {"-f", "--raw-field", "-F", "--field"}:
                match = _KEY_VALUE.fullmatch(value)
                if match is None:
                    raise _invalid(
                        "api-invalid: API fields must be plain key=value values"
                    )
                key, field_value = value.split("=", 1)
                if key not in _API_FIELDS or field_value.startswith("@"):
                    raise _invalid("api-invalid: unsupported API query field")
            output.extend((item, value))
            index += 2
            continue
        if item.startswith("-"):
            raise _invalid("api-invalid: unsupported gh api option")
        if endpoint is not None:
            raise _invalid("api-invalid: pass one GitHub REST endpoint")
        endpoint = item
        index += 1
    if endpoint is not None and endpoint.startswith("repos/"):
        endpoint = "/" + endpoint
    if endpoint is None or "%" in endpoint or "?" in endpoint or "#" in endpoint:
        raise _invalid("api-invalid: use a plain /repos/ORG/REPO REST endpoint")
    match = re.fullmatch(
        r"/repos/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)((?:/[A-Za-z0-9_.-]+)*)",
        endpoint,
    )
    if (
        match is None
        or ".." in endpoint.split("/")
        or _repository(profile, match.group(1), match.group(2)) != repository
    ):
        raise _invalid("api-invalid: endpoint must stay inside the selected repository")
    canonical = f"/repos/{repository.slug}{match.group(3)}"
    if method == "POST":
        if (
            profile.permissions.get("pull_requests") != "write"
            or not input_stdin
            or output
            or not re.fullmatch(r"/pulls/[1-9][0-9]*/reviews", match.group(3))
        ):
            raise _invalid("api-invalid: only pending review creation is supported")
        return ("api", canonical, "-X", "POST", "--input", "-")
    if input_stdin:
        raise _invalid("api-invalid: GET does not accept input")
    return ("api", canonical, *output, "-X", "GET")


def validate_pending_review_input(value: str) -> str:
    try:
        payload = json.loads(value)
    except (ValueError, TypeError) as error:
        raise _invalid("api-invalid: pending review input must be JSON") from error
    if (
        not isinstance(payload, dict)
        or not payload
        or set(payload) - {"body", "commit_id", "comments"}
    ):
        raise _invalid("api-invalid: only pending review fields are allowed")
    if not isinstance(payload.get("commit_id"), str) or not re.fullmatch(
        r"[0-9a-fA-F]{40}", payload["commit_id"]
    ):
        raise _invalid("api-invalid: pending review requires a full commit id")
    comments = payload.get("comments")
    if not isinstance(comments, list) or not comments:
        raise _invalid("api-invalid: pending review requires inline comments")
    for comment in comments:
        if not isinstance(comment, dict) or set(comment) != {
            "path",
            "line",
            "side",
            "body",
        }:
            raise _invalid("api-invalid: invalid review comment fields")
        if (
            not isinstance(comment["path"], str)
            or not _valid_scalar(comment["path"])
            or not isinstance(comment["body"], str)
            or not comment["body"].strip()
            or "\x00" in comment["body"]
            or type(comment["line"]) is not int
            or comment["line"] < 1
            or comment["side"] not in ("LEFT", "RIGHT")
        ):
            raise _invalid("api-invalid: invalid review comment")
    if "body" in payload and not isinstance(payload["body"], str):
        raise _invalid("api-invalid: invalid review body")
    return json.dumps(payload)


def validate_gh_command(
    profile: RepositoryScope,
    arguments: Sequence[str],
    *,
    default_repository: str | None = None,
) -> GhInvocation:
    if not arguments or any(not isinstance(item, str) for item in arguments):
        raise _invalid("argument-invalid: pass a supported gh command")
    explicit, selected = _split_repository_option(arguments)
    if selected and selected[0] == "api":
        repository = _resolve_repository(profile, explicit, default_repository)
        return GhInvocation(
            repository, _validate_api(profile, repository, selected[1:])
        )
    if len(selected) < 2:
        raise _invalid("argument-invalid: pass a supported gh command and subcommand")
    group, command, trailing = selected[0], selected[1], selected[2:]
    rule = _READ_COMMANDS.get((group, command)) or _WRITE_COMMANDS.get((group, command))
    if rule is None:
        raise _invalid("command-denied: use a supported employer gh command")
    options = _parse_options(trailing, *rule)
    positional_repository = (
        options.positionals[0]
        if group == "repo" and command == "view" and options.positionals
        else None
    )
    repository = _resolve_repository(
        profile, explicit, default_repository or positional_repository
    )
    _validate_positionals(profile, repository, group, command, options.positionals)
    head = options.value("--head")
    if head is not None and ":" in head:
        raise _invalid(
            "argument-invalid: --head must name a branch in the selected repository"
        )
    _validate_noninteractive(group, command, options)
    if group == "repo" and command == "view":
        return GhInvocation(
            repository, (group, command, *options.options, repository.slug)
        )
    return GhInvocation(
        repository, (group, command, *trailing, "--repo", repository.slug)
    )
