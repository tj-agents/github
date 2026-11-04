from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from access_core import AccessError, Profile, State, Vault, private_environment
from github_platform import Launcher, PlatformProtocol

_NAME = re.compile(r"[A-Za-z0-9_.-]{1,100}")
_SCALAR = re.compile(r"[^\x00\r\n]{1,4096}")
_KEY_VALUE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}=[^\x00\r\n]{0,4096}")
_CONFIG_ISOLATION = (
    "git-config-isolation: effective configuration changed after HOME isolation; "
    "use HOME-independent configuration"
)
_UNSAFE_REMOTE_CONFIG_SUFFIXES = frozenset(
    {
        "proxy",
        "proxyauthmethod",
        "vcs",
        "uploadpack",
        "receivepack",
        "serveroption",
        "promisor",
        "partialclonefilter",
    }
)


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
class GitInvocation:
    repository: Repository | None
    arguments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GitConfigEntry:
    scope: str
    origin: str
    key: str
    value: str | None


@dataclass(frozen=True, slots=True)
class GitConfigFile:
    source: Path
    relative: Path
    contents: bytes


@dataclass(frozen=True, slots=True)
class GitConfigSnapshot:
    entries: tuple[GitConfigEntry, ...]
    files: tuple[GitConfigFile, ...]
    hooks_path: str | None


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
) -> AccessError:
    return AccessError(message)


def _repository(profile: Profile, organization: str, name: str) -> Repository:
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


def parse_repository(profile: Profile, value: str) -> Repository:
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


def parse_https_repository(profile: Profile, value: str) -> Repository:
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
    profile: Profile, explicit: str | None, default_repository: str | None
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
    profile: Profile, value: str, repository: Repository, kind: str
) -> None:
    if value.isdecimal():
        return
    match = re.fullmatch(rf"https://github\.com/([^/]+)/([^/]+)/{kind}/(\d+)", value)
    if match is None:
        raise _invalid("argument-invalid: use a number or selected repository URL")
    if _repository(profile, match.group(1), match.group(2)) != repository:
        raise _invalid("repository-denied: URL names a different repository")


def _validate_positionals(
    profile: Profile,
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
    profile: Profile, repository: Repository, arguments: Sequence[str]
) -> tuple[str, ...]:
    endpoint: str | None = None
    output: list[str] = []
    index = 0
    while index < len(arguments):
        item = arguments[index]
        if item in {"--paginate"}:
            output.append(item)
            index += 1
            continue
        if item in {"-X", "--method"}:
            if index + 1 == len(arguments) or arguments[index + 1].upper() != "GET":
                raise _invalid("api-invalid: employer REST access permits GET only")
            output.extend(("-X", "GET"))
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
    return ("api", canonical, *output, "-X", "GET")


def validate_gh_command(
    profile: Profile,
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


def _active_token(state: State, vault: Vault) -> str:
    version = state.active()
    if version is None:
        raise AccessError(
            "credential-unavailable: onboard the employer credential first"
        )
    return vault.read(version)


def _child_environment(
    source: Mapping[str, str], token: str, config: str, platform: PlatformProtocol
) -> dict[str, str]:
    environment = private_environment(source)
    for key in tuple(environment):
        upper = key.upper()
        if (
            upper.startswith(("GH_", "GITHUB_"))
            or upper.endswith("_PROXY")
            or upper
            in {
                "ALL_PROXY",
                "BROWSER",
                "EDITOR",
                "NO_PROXY",
                "PAGER",
                "PYTHONPATH",
                "PYTHONSTARTUP",
                "VISUAL",
            }
        ):
            environment.pop(key)
    environment.update(
        {
            "GH_TOKEN": token,
            "GH_HOST": "github.com",
            "GH_CONFIG_DIR": config,
            "GH_PROMPT_DISABLED": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_COUNT": "0",
            "TEMP": config,
            "TMP": config,
        }
    )
    git = platform.real_git(environment)
    launchers = Path(config) / "git-child"
    launchers.mkdir()
    launcher = platform.launcher_path(launchers, Launcher.GIT)
    script = Path(__file__).resolve().with_name("github_git_child.py")
    platform.write_launcher(
        launcher, platform.command_launcher_contents(script, (str(git),))
    )
    path = environment.get("PATH", "")
    environment["PATH"] = str(launchers) + (os.pathsep + path if path else "")
    return environment


def _redact(value: str, token: str) -> str:
    if token:
        return value.replace(token, "[redacted]")
    return value


def run_gh(
    profile: Profile,
    state: State,
    vault: Vault,
    executable: Path,
    arguments: Sequence[str],
    *,
    platform: PlatformProtocol,
    cwd: Path,
    default_repository: str | None = None,
    environment: Mapping[str, str] | None = None,
    timeout: float = 60,
) -> subprocess.CompletedProcess[str]:
    invocation = validate_gh_command(
        profile, arguments, default_repository=default_repository
    )
    if not cwd.is_absolute() or not cwd.is_dir():
        raise _invalid("cwd-invalid: pass an existing absolute gh working directory")
    if not executable.is_absolute() or not executable.is_file():
        raise _invalid("launcher-invalid: pass the real absolute gh executable")
    token = _active_token(state, vault)
    source = environment if environment is not None else os.environ
    with tempfile.TemporaryDirectory(prefix="github-access-gh-") as directory:
        try:
            result = subprocess.run(
                [str(executable), *invocation.arguments],
                text=True,
                capture_output=True,
                check=False,
                cwd=cwd,
                env=_child_environment(source, token, directory, platform),
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AccessError("tool-unavailable: gh did not complete safely") from error
    return subprocess.CompletedProcess(
        result.args,
        result.returncode,
        _redact(result.stdout, token),
        _redact(result.stderr, token),
    )


def parse_git_credential(profile: Profile, request: str) -> Repository:
    fields: dict[str, str] = {}
    for line in request.splitlines():
        if not line:
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or "\x00" in line:
            raise _invalid("credential-invalid: malformed Git credential request")
        if key not in {"protocol", "host", "path", "username"}:
            continue
        if key in fields:
            raise _invalid("credential-invalid: malformed Git credential request")
        fields[key] = value
    if fields.get("protocol") != "https" or fields.get("host") != "github.com":
        raise _invalid(
            "credential-denied: Git credential requests must target github.com HTTPS"
        )
    username = fields.get("username")
    if username is not None and username.casefold() != profile.login.casefold():
        raise _invalid(
            "credential-denied: Git username does not match the employer profile"
        )
    return parse_repository(profile, fields.get("path", ""))


def git_credential_response(
    profile: Profile,
    state: State,
    vault: Vault,
    operation: str,
    request: str,
) -> str:
    if operation != "get":
        return ""
    parse_git_credential(profile, request)
    token = _active_token(state, vault)
    return f"username={profile.login}\npassword={token}\n\n"


def validate_git_command(
    profile: Profile,
    arguments: Sequence[str],
    *,
    remotes: Mapping[str, str],
) -> GitInvocation:
    if not arguments or any(not isinstance(item, str) for item in arguments):
        raise _invalid("git-invalid: pass a supported git command")
    if arguments[0] in {"-c", "-C", "--config-env", "--exec-path"} or any(
        item.startswith(
            (
                "-c",
                "-C",
                "--config-env",
                "--exec-path",
                "--upload-pack",
                "--receive-pack",
                "--git-dir",
                "--work-tree",
            )
        )
        for item in arguments
    ):
        raise _invalid(
            "git-invalid: inherited Git configuration and external helpers are denied"
        )
    command, rest = arguments[0], tuple(arguments[1:])
    if command in {"status", "diff", "log", "show", "branch"}:
        status_options = {
            "--branch",
            "--porcelain",
            "--porcelain=v1",
            "--porcelain=v2",
            "--short",
            "-b",
            "-s",
        }
        if any(item.startswith("-") or not _valid_scalar(item) for item in rest) and (
            command != "status" or any(item not in status_options for item in rest)
        ):
            raise _invalid(
                "git-invalid: local commands accept only approved status flags"
            )
        return GitInvocation(None, tuple(arguments))
    if command == "clone":
        if not 1 <= len(rest) <= 2 or rest[0].startswith("-"):
            raise _invalid("git-invalid: clone requires one employer HTTPS URL")
        repository = parse_https_repository(profile, rest[0])
        if len(rest) == 2 and (not _valid_scalar(rest[1]) or rest[1].startswith("-")):
            raise _invalid("git-invalid: invalid clone directory")
        return GitInvocation(repository, tuple(arguments))
    if command not in {"fetch", "pull", "push", "ls-remote"} or not rest:
        raise _invalid(
            "git-invalid: use clone, fetch, pull, push, ls-remote or a local command"
        )
    remote = rest[0]
    if remote.startswith("-"):
        raise _invalid("git-invalid: network commands require an employer remote")
    if remote in remotes:
        repository = parse_https_repository(profile, remotes[remote])
    elif command in {"fetch", "push", "ls-remote"}:
        repository = parse_https_repository(profile, remote)
    else:
        raise _invalid("git-invalid: network commands require a declared named remote")
    if any(item.startswith("-") or not _valid_scalar(item) for item in rest[1:]):
        raise _invalid("git-invalid: network commands accept only plain ref arguments")
    return GitInvocation(repository, tuple(arguments))


def git_environment(
    source: Mapping[str, str], helper_command: str, home: Path
) -> dict[str, str]:
    if not _valid_scalar(helper_command):
        raise _invalid("helper-invalid: pass one exact credential helper command")
    environment = private_environment(source)
    backend = source.get("GCM_CREDENTIAL_STORE")
    if backend in {"gpg", "secretservice"}:
        environment["GCM_CREDENTIAL_STORE"] = backend
    if backend == "gpg":
        original_home = Path(source.get("HOME") or Path.home()).expanduser()
        environment.setdefault("GNUPGHOME", str(original_home / ".gnupg"))
        environment.setdefault(
            "PASSWORD_STORE_DIR", str(original_home / ".password-store")
        )
    entries = (
        ("credential.helper", ""),
        ("credential.https://github.com.helper", ""),
        ("http.extraHeader", ""),
        ("http.https://github.com/.extraHeader", ""),
        ("http.proxy", ""),
        ("http.https://github.com.proxy", ""),
        ("http.followRedirects", "false"),
        ("http.sslVerify", "true"),
        ("credential.useHttpPath", "true"),
        ("credential.https://github.com.helper", helper_command),
    )
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_COUNT": str(len(entries)),
            "HOME": str(home),
            "NETRC": os.devnull,
            "GIT_ALLOW_PROTOCOL": "https",
        }
    )
    for index, (key, value) in enumerate(entries):
        environment[f"GIT_CONFIG_KEY_{index}"] = key
        environment[f"GIT_CONFIG_VALUE_{index}"] = value
    return environment


def _git_config_environment(source: Mapping[str, str]) -> dict[str, str]:
    environment = private_environment(source)
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_COUNT": "0",
        }
    )
    return environment


def _unsafe_git_config_key(key: str) -> bool:
    normalized = key.casefold()
    if normalized.startswith(("http.", "credential.", "url.")):
        return True
    if normalized in {"core.gitproxy", "extensions.partialclone"}:
        return True
    parts = normalized.split(".")
    return (
        len(parts) > 2
        and parts[0] == "remote"
        and parts[-1] in _UNSAFE_REMOTE_CONFIG_SUFFIXES
    )


def _config_entries(
    executable: Path, cwd: Path, source: Mapping[str, str], timeout: float
) -> tuple[GitConfigEntry, ...]:
    try:
        result = subprocess.run(
            [
                str(executable),
                "config",
                "--includes",
                "--null",
                "--show-scope",
                "--show-origin",
                "--list",
            ],
            text=True,
            capture_output=True,
            check=False,
            cwd=cwd,
            env=_git_config_environment(source),
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AccessError("git-config-unsafe") from error
    if result.returncode:
        raise AccessError("git-config-unsafe")
    fields = result.stdout.split("\0")
    entries: list[GitConfigEntry] = []
    if fields[-1:] != [""] or (len(fields) - 1) % 3:
        raise AccessError("git-config-unsafe")
    for index in range(0, len(fields) - 1, 3):
        scope, origin, item = fields[index : index + 3]
        key, separator, value = item.partition("\n")
        if not key or _unsafe_git_config_key(key):
            raise AccessError("git-config-unsafe")
        entries.append(GitConfigEntry(scope, origin, key, value if separator else None))
    return tuple(entries)


def _config_home(
    executable: Path, cwd: Path, source: Mapping[str, str], timeout: float
) -> Path:
    try:
        result = subprocess.run(
            [
                str(executable),
                "config",
                "--file",
                os.devnull,
                "--no-includes",
                "--null",
                "--type=path",
                "--default=~/",
                "--get",
                "wrapper.originalHome",
            ],
            text=True,
            capture_output=True,
            check=False,
            cwd=cwd,
            env=_git_config_environment(source),
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AccessError("git-config-unsafe") from error
    values = [value for value in result.stdout.split("\0") if value]
    if result.returncode or len(values) != 1:
        raise AccessError("git-config-unsafe")
    home = Path(values[0])
    if not home.is_absolute():
        raise AccessError("git-config-unsafe")
    return home.resolve(strict=False)


def _repository_config_paths(
    executable: Path, cwd: Path, source: Mapping[str, str], timeout: float
) -> tuple[Path, Path]:
    try:
        common = subprocess.run(
            [str(executable), "rev-parse", "--git-common-dir"],
            text=True,
            capture_output=True,
            check=False,
            cwd=cwd,
            env=_git_config_environment(source),
            timeout=timeout,
        )
        git_dir = subprocess.run(
            [str(executable), "rev-parse", "--git-dir"],
            text=True,
            capture_output=True,
            check=False,
            cwd=cwd,
            env=_git_config_environment(source),
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AccessError("git-config-unsafe") from error
    if common.returncode or git_dir.returncode:
        raise AccessError("git-config-unsafe")
    common_directory = Path(common.stdout.strip())
    directory = Path(git_dir.stdout.strip())
    if not common_directory.is_absolute():
        common_directory = cwd / common_directory
    if not directory.is_absolute():
        directory = cwd / directory
    return (
        (common_directory / "config").resolve(strict=False),
        (directory / "config.worktree").resolve(strict=False),
    )


def _config_origin_path(origin: str, cwd: Path) -> Path | None:
    if not origin.startswith("file:"):
        return None
    path = Path(origin.removeprefix("file:"))
    return path if path.is_absolute() else cwd / path


def _validate_config_contents(
    executable: Path, contents: bytes, source: Mapping[str, str], timeout: float
) -> None:
    try:
        result = subprocess.run(
            [
                str(executable),
                "config",
                "--file=-",
                "--no-includes",
                "--null",
                "--list",
            ],
            input=contents,
            text=False,
            capture_output=True,
            check=False,
            env=_git_config_environment(source),
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AccessError("git-config-unsafe") from error
    if result.returncode:
        raise AccessError("git-config-unsafe")
    try:
        items = result.stdout.decode().split("\0")
    except UnicodeDecodeError as error:
        raise AccessError("git-config-unsafe") from error
    for item in items:
        if not item:
            continue
        key, _, _ = item.partition("\n")
        if not key or _unsafe_git_config_key(key):
            raise AccessError("git-config-unsafe")


def _config_file(
    executable: Path,
    origin: Path,
    home: Path,
    excluded: Sequence[Path],
    source: Mapping[str, str],
    timeout: float,
) -> GitConfigFile | None:
    if origin.resolve(strict=False) in excluded:
        return None
    try:
        relative = origin.relative_to(home)
    except ValueError:
        return None
    if any(part in {"", ".", ".."} for part in relative.parts) or not relative.parts:
        raise AccessError("git-config-unsafe")
    if relative.parts[0].split(":", 1)[0].rstrip(" .").casefold() in {
        ".netrc",
        "_netrc",
    }:
        raise AccessError("git-config-unsafe")
    cursor = home
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise AccessError("git-config-unsafe")
    if not origin.is_file() or origin.is_symlink():
        raise AccessError("git-config-unsafe")
    resolved_home = home.resolve(strict=False)
    resolved_origin = origin.resolve()
    try:
        resolved_relative = resolved_origin.relative_to(resolved_home)
    except ValueError as error:
        raise AccessError("git-config-unsafe") from error
    if resolved_relative.name.split(":", 1)[0].rstrip(" .").casefold() in {
        ".netrc",
        "_netrc",
    }:
        raise AccessError("git-config-unsafe")
    contents = origin.read_bytes()
    _validate_config_contents(executable, contents, source, timeout)
    return GitConfigFile(origin, relative, contents)


def _hooks_path(
    executable: Path, cwd: Path, source: Mapping[str, str], timeout: float
) -> str | None:
    try:
        result = subprocess.run(
            [
                str(executable),
                "config",
                "--includes",
                "--null",
                "--type=path",
                "--get",
                "core.hooksPath",
            ],
            text=True,
            capture_output=True,
            check=False,
            cwd=cwd,
            env=_git_config_environment(source),
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AccessError("git-config-unsafe") from error
    if result.returncode == 1:
        return None
    values = result.stdout.split("\0")
    if values[-1:] != [""]:
        raise AccessError("git-config-unsafe")
    values = values[:-1]
    if (
        result.returncode
        or len(values) != 1
        or (values[0] and not _valid_scalar(values[0]))
    ):
        raise AccessError("git-config-unsafe")
    return values[0]


def _effective_git_config(
    executable: Path, cwd: Path, source: Mapping[str, str], timeout: float
) -> GitConfigSnapshot:
    entries = _config_entries(executable, cwd, source, timeout)
    home = _config_home(executable, cwd, source, timeout)
    origins = {
        path
        for entry in entries
        if (path := _config_origin_path(entry.origin, cwd)) is not None
    }
    excluded = (
        _repository_config_paths(executable, cwd, source, timeout) if origins else ()
    )
    files = tuple(
        file
        for origin in origins
        if (file := _config_file(executable, origin, home, excluded, source, timeout))
        is not None
    )
    return GitConfigSnapshot(
        entries, files, _hooks_path(executable, cwd, source, timeout)
    )


def _config_signature(
    entries: Sequence[GitConfigEntry],
) -> tuple[tuple[str, str, str | None], ...]:
    return tuple((entry.scope, entry.key, entry.value) for entry in entries)


def _mirror_config(snapshot: GitConfigSnapshot, home: Path) -> None:
    for file in snapshot.files:
        destination = home / file.relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(file.contents)


def _verify_config_snapshot(
    executable: Path,
    cwd: Path,
    source: Mapping[str, str],
    snapshot: GitConfigSnapshot,
    timeout: float,
) -> None:
    if _config_signature(
        _config_entries(executable, cwd, source, timeout)
    ) != _config_signature(snapshot.entries):
        raise AccessError(_CONFIG_ISOLATION)
    for file in snapshot.files:
        try:
            unchanged = file.source.read_bytes() == file.contents
        except OSError as error:
            raise AccessError(_CONFIG_ISOLATION) from error
        if not unchanged:
            raise AccessError(_CONFIG_ISOLATION)


def _normalized_git_arguments(
    invocation: GitInvocation, hooks_path: str | None
) -> tuple[str, ...]:
    command, *rest = invocation.arguments
    hooks = ("-c", f"core.hooksPath={hooks_path}") if hooks_path is not None else ()
    if command == "clone":
        return (*hooks, command, "--template=", "--no-recurse-submodules", *rest)
    if command in {"fetch", "pull", "push"}:
        return (*hooks, command, "--recurse-submodules=no", *rest)
    return (*hooks, *invocation.arguments)


def _local_git_environment(source: Mapping[str, str]) -> dict[str, str]:
    environment = private_environment(source)
    environment["GIT_NO_LAZY_FETCH"] = "1"
    return environment


def run_git(
    profile: Profile,
    executable: Path,
    arguments: Sequence[str],
    *,
    remotes: Mapping[str, str],
    helper_command: str,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
    timeout: float = 60,
) -> subprocess.CompletedProcess[str]:
    invocation = validate_git_command(profile, arguments, remotes=remotes)
    if (
        not executable.is_absolute()
        or not executable.is_file()
        or not cwd.is_absolute()
        or not cwd.is_dir()
    ):
        raise _invalid(
            "launcher-invalid: pass an absolute git executable and working directory"
        )
    source = environment if environment is not None else os.environ
    try:
        if invocation.repository is None:
            return subprocess.run(
                [str(executable), *invocation.arguments],
                text=True,
                capture_output=True,
                check=False,
                cwd=cwd,
                env=_local_git_environment(source),
                timeout=timeout,
            )
        with tempfile.TemporaryDirectory(prefix="github-access-") as home:
            snapshot = (
                None
                if invocation.arguments[0] == "clone"
                else _effective_git_config(executable, cwd, source, timeout)
            )
            if snapshot is None:
                _config_entries(executable, cwd, source, timeout)
            else:
                _mirror_config(snapshot, Path(home))
                isolated = _config_entries(
                    executable,
                    cwd,
                    {**source, "HOME": str(home)},
                    timeout,
                )
                if _config_signature(isolated) != _config_signature(snapshot.entries):
                    raise AccessError(_CONFIG_ISOLATION)
                _verify_config_snapshot(executable, cwd, source, snapshot, timeout)
            return subprocess.run(
                [
                    str(executable),
                    *_normalized_git_arguments(
                        invocation, snapshot.hooks_path if snapshot else None
                    ),
                ],
                text=True,
                capture_output=True,
                check=False,
                cwd=cwd,
                env=git_environment(source, helper_command, Path(home)),
                timeout=timeout,
            )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AccessError("tool-unavailable: git did not complete safely") from error
