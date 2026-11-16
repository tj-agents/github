"""Which GitHub account a gh command or Git request targets."""

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from gate_git import git

GITHUB_URL = re.compile(r"(?<![\w.-])github\.com[:/]([A-Za-z0-9-]+)")
API_PATH = re.compile(r"(?:^|/)(?:repos|orgs|users)/([A-Za-z0-9-]+)")
REPOSITORY = re.compile(r"([A-Za-z0-9-]+)/[\w.-]+")
LOGIN = re.compile(r"[A-Za-z0-9-]+")
OWNER_FLAGS = ("-R", "--repo", "--owner")
ORG_FLAG_COMMANDS = ("secret", "variable", "ruleset", "rs")
ALIASES: Mapping[tuple[str, ...], str] = {
    ("pr", "new"): "create",
    ("issue", "new"): "create",
    ("pr", "co"): "checkout",
    ("pr", "ls"): "list",
    ("issue", "ls"): "list",
    ("repo", "ls"): "list",
}
PR_VALUE_FLAGS = (
    "-t",
    "--title",
    "-b",
    "--body",
    "-F",
    "--body-file",
    "-B",
    "--base",
    "-H",
    "--head",
    "-a",
    "--assignee",
    "-l",
    "--label",
    "-m",
    "--milestone",
    "-p",
    "--project",
    "-r",
    "--reviewer",
    "-R",
    "--repo",
    "-T",
    "--template",
    "--recover",
)
PAYLOAD_FLAGS = (
    *(flag for flag in PR_VALUE_FLAGS if flag.startswith("--")),
    "--field",
    "--raw-field",
    "--input",
    "--header",
    "--jq",
    "--search",
    "--method",
    "--cache",
    "--json",
    "--limit",
    "-q",
    "-t",
    "--preview",
    "--branch",
    "--subject",
    "--author-email",
    "--match-head-commit",
    "--description",
    "--homepage",
    "--gitignore",
    "--license",
    "--source",
    "--team",
    "--color",
    "--reason",
    "--add-assignee",
    "--add-label",
    "--add-project",
    "--add-reviewer",
    "--remove-assignee",
    "--remove-label",
    "--remove-project",
    "--remove-reviewer",
)
COMMAND_PAYLOAD_FLAGS: Mapping[tuple[str, ...], tuple[str, ...]] = {
    ("api",): ("-F", "-f", "-H", "-q", "-X", "-p", "-t"),
    ("pr", "create"): (
        "-a",
        "-B",
        "-b",
        "-F",
        "-H",
        "-l",
        "-m",
        "-p",
        "-r",
        "-T",
        "-t",
    ),
    ("pr", "edit"): ("-B", "-b", "-F", "-m", "-t"),
    ("pr", "merge"): ("-A", "-b", "-F", "-t"),
    ("pr", "review"): ("-b", "-F"),
    ("pr", "checkout"): ("-b",),
    ("pr", "comment"): ("-b", "-F"),
    ("pr", "close"): ("-c", "--comment"),
    ("pr", "reopen"): ("-c", "--comment"),
    ("pr", "view"): ("-q", "-t"),
    ("pr", "list"): ("-a", "-A", "-B", "-H", "-l", "-L", "-S", "-s", "-q", "-t"),
    ("pr", "checks"): ("-q", "-t"),
    ("issue", "create"): ("-a", "-b", "-F", "-l", "-m", "-p", "-T", "-t"),
    ("issue", "edit"): ("-b", "-F", "-m", "-t"),
    ("issue", "comment"): ("-b", "-F"),
    ("issue", "close"): ("-c", "-r", "--comment"),
    ("issue", "reopen"): ("-c", "--comment"),
    ("issue", "view"): ("-q", "-t"),
    ("issue", "list"): ("-a", "-A", "-l", "-L", "-S", "-s", "-m", "-q", "-t"),
    ("repo", "create"): ("-d", "-g", "-h", "-l", "-r", "-s", "-t", "-p", "--remote"),
    ("repo", "list"): ("-l", "-L", "--language", "--topic", "--visibility"),
    ("repo", "clone"): ("-u", "--upstream-remote-name"),
    ("repo", "fork"): ("--remote-name", "--org", "--fork-name"),
    ("repo", "edit"): ("-d", "-h"),
    ("repo", "view"): ("-q", "-t"),
}


@dataclass(frozen=True, slots=True)
class Target:
    """The accounts and host a gh command names explicitly."""

    owners: tuple[str, ...]
    hostname: str | None


def normalized(arguments: Sequence[str]) -> list[str]:
    replacement = ALIASES.get(tuple(arguments[:2]))
    if replacement is None:
        return list(arguments)
    return [arguments[0], replacement, *arguments[2:]]


def owners_in(value: str) -> list[str]:
    match = GITHUB_URL.search(value)
    if match:
        return [match.group(1)]
    return [owner.split("/")[0] for owner in value.split(",")]


def url_owners(values: Sequence[str]) -> Iterator[str]:
    for value in values:
        for match in GITHUB_URL.finditer(value):
            yield match.group(1)


def api_owners(values: Sequence[str]) -> Iterator[str]:
    for value in values:
        for match in API_PATH.finditer(value):
            yield match.group(1)


def gh_target(arguments: Sequence[str]) -> Target:
    """Name the accounts a gh command targets, ignoring flag values that are payload."""
    arguments = normalized(arguments)
    owners: list[str] = []
    positional: list[str] = []
    hostname: str | None = None
    command = tuple(arguments[:1] if arguments[:1] == ["api"] else arguments[:2])
    payload_flags = (*PAYLOAD_FLAGS, *COMMAND_PAYLOAD_FLAGS.get(command, ()))
    org_flags = bool(arguments) and arguments[0] in ORG_FLAG_COMMANDS
    remaining = iter(arguments)
    for argument in remaining:
        flag, equals, inline = argument.partition("=")
        if flag == "--hostname":
            hostname = inline if equals else next(remaining, "")
            continue
        if flag in OWNER_FLAGS or (org_flags and flag in ("-o", "--org")):
            value = inline if equals else next(remaining, "")
        elif argument.startswith("-R") and len(argument) > 2:
            value = argument[2:]
        elif flag in payload_flags:
            if not equals:
                next(remaining, None)
            continue
        else:
            if not argument.startswith("-"):
                positional.append(argument)
            continue
        if value:
            owners.extend(owners_in(value))
    if arguments[:1] == ["api"]:
        targets = positional[1:2]
        owners.extend(api_owners(targets))
    elif arguments[:1] == ["repo"]:
        targets = positional[2:3]
    elif arguments[:1] in (["pr"], ["issue"]) and arguments[1:2] != ["create"]:
        targets = positional[2:]
    else:
        targets = []
    owners.extend(url_owners(targets))
    if arguments[:1] == ["repo"]:
        owners.extend(
            target.split("/")[0] for target in targets if REPOSITORY.fullmatch(target)
        )
        if arguments[1:2] == ["list"]:
            owners.extend(target for target in targets if LOGIN.fullmatch(target))
    return Target(tuple(owners), hostname)


def remote_owner(url: str) -> str | None:
    match = GITHUB_URL.search(url.strip())
    return match.group(1) if match else None


def origin_owner(cwd: Path, environment: Mapping[str, str]) -> str | None:
    result = git(["remote", "get-url", "origin"], cwd, environment)
    if result.returncode:
        return None
    return remote_owner(result.stdout.decode("utf-8", "replace"))
