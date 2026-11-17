"""Hand an App's pull request to the human when the repository requires their review.

After an agent's `gh pr create` or `gh pr ready` succeeds in an `app` repository, the
gate asks GitHub, as the App, for the pull request's review decision. When it is
REVIEW_REQUIRED it links the pull request to the session's one work item, puts
`! tj approve` on the human's clipboard and reports both. A handover failure is
reported with what the human should do; it never changes the gh command's result.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from gate_agent import AgentHost
from gate_error import GateError
from gate_owner import PR_VALUE_FLAGS, normalized
from gate_process import Launch, Run, reason
from gate_session import AgentStore, current_session

APPROVE_LINE = "! tj approve"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
UNREVIEWABLE_CREATE = frozenset({"-d", "--draft", "-w", "--web", "--dry-run"})
PULL_REQUEST = re.compile(r"https://github\.com/[A-Za-z0-9-]+/[\w.-]+/pull/\d+")
TIMEOUT_SECONDS = 30
ITEM_TIMEOUT_SECONDS = 120


class HandoverError(GateError):
    pass


def copy_to_clipboard(text: str) -> None:
    """Put `text` on the clipboard through tj-cli, which verifies it by reading back."""
    from tj_cli.clipboard import ClipboardError, copy  # only agents' PRs need it

    try:
        copy(text)
    except ClipboardError as error:
        raise HandoverError(str(error)) from error


def option_names(arguments: Sequence[str]) -> set[str]:
    names: set[str] = set()
    remaining = iter(arguments)
    for argument in remaining:
        if argument in PR_VALUE_FLAGS:
            next(remaining, None)
        elif argument.startswith("-"):
            names.add(argument.partition("=")[0])
    return names


def awaits_approval(arguments: Sequence[str]) -> bool:
    """Whether a successful run of this gh command leaves a PR that may need review."""
    command = normalized(arguments)
    flags = option_names(command[2:])
    if command[:2] == ["pr", "create"]:
        return not flags & UNREVIEWABLE_CREATE
    return command[:2] == ["pr", "ready"] and "--undo" not in flags


def ready_selector(arguments: Sequence[str]) -> list[str]:
    """The pull request a `gh pr ready` command names, for `gh pr view`."""
    selector: list[str] = []
    remaining = iter(normalized(arguments)[2:])
    for argument in remaining:
        if argument in ("-R", "--repo"):
            selector += [argument, next(remaining, "")]
        elif argument.startswith(("-R", "--repo=")) or not argument.startswith("-"):
            selector.append(argument)
    return selector


@dataclass(frozen=True, slots=True)
class Handover:
    """The programs a handover runs, and where it reports."""

    launch: Launch
    run: Run
    copy: Callable[[str], object]
    report: Callable[[str], None]
    tj: str | None
    agent_store: str | None


def run_pull_request(
    gh: Path,
    arguments: Sequence[str],
    scoped: Mapping[str, str],
    inherited: Mapping[str, str],
    handover: Handover,
) -> int:
    """Run the agent's `gh pr create` or `gh pr ready`, then hand the PR over."""
    create = normalized(arguments)[1] == "create"
    status, output = handover.launch(gh, arguments, scoped, create)
    if status:
        return status
    try:
        if not create:
            view = [gh, "pr", "view", *ready_selector(arguments), "--json", "url"]
            result = handover.run([str(part) for part in view], scoped, TIMEOUT_SECONDS)
            output = result.stdout if not result.returncode else ""
        hand_over(gh, output, scoped, inherited, handover)
    except (GateError, OSError) as error:
        handover.report(str(error))
    except Exception as error:  # noqa: BLE001 - a handover never fails the gh command
        handover.report(
            f"the pull request was not handed over ({type(error).__name__}: {error}); "
            "if it needs your review, link it to the work item and run "
            f"`{APPROVE_LINE}`"
        )
    return status


def hand_over(
    gh: Path,
    output: str,
    scoped: Mapping[str, str],
    inherited: Mapping[str, str],
    handover: Handover,
) -> None:
    match = PULL_REQUEST.search(output)
    if match is None:
        raise HandoverError(
            "gh did not report the pull request's URL, so it was not handed over; if "
            f"it needs your review, link it to the work item and run `{APPROVE_LINE}`"
        )
    url = match.group(0)
    decision = handover.run(
        [
            str(gh),
            "pr",
            "view",
            url,
            "--json",
            "reviewDecision",
            "--jq",
            ".reviewDecision",
        ],
        scoped,
        TIMEOUT_SECONDS,
    )
    if decision.returncode:
        raise HandoverError(
            f"could not tell whether {url} needs your review ({reason(decision)}); if "
            f"it does, link it to the work item and run `{APPROVE_LINE}`"
        )
    if decision.stdout.strip() != REVIEW_REQUIRED:
        return
    item = link(url, inherited, handover)
    try:
        handover.copy(APPROVE_LINE)
    except GateError as error:
        raise HandoverError(
            f"{url} needs your review and is linked to {item}, but the clipboard is "
            f"unavailable ({error}); run `{APPROVE_LINE}` in this session"
        ) from error
    handover.report(
        f"{url} needs your review: linked it to {item}, and `{APPROVE_LINE}` is on "
        "your clipboard; paste it into this session to approve"
    )


def link(url: str, inherited: Mapping[str, str], handover: Handover) -> str:
    """Link `url` to the session's one work item and return the item."""
    label = next(
        (host.session(inherited) for host in AgentHost if host.session(inherited)),
        "<host>:<session-id>",
    )
    remedy = (
        f"link this session with `agent-store link --session {label} --item <ref>` and "
        f"the pull request with `tj item link <ref> {url}`, then run "
        f"`{APPROVE_LINE}`"
    )
    if handover.agent_store is None or handover.tj is None:
        missing = "agent-store" if handover.agent_store is None else "tj"
        raise HandoverError(
            f"{url} needs your review but {missing} is not on PATH, so it was not "
            f"linked; {remedy}"
        )
    store = AgentStore(handover.agent_store, inherited, handover.run)
    session = current_session(store, inherited, None)
    if session is None:
        raise HandoverError(
            f"{url} needs your review but no agent-store session is registered for "
            f"this agent; {remedy}"
        )
    items = list(dict.fromkeys(session[1]))
    if len(items) != 1:
        found = "no linked work item" if not items else f"{len(items)} work items"
        listed = f" ({', '.join(items)})" if items else ""
        raise HandoverError(
            f"{url} needs your review but this session has {found}{listed}, so it "
            f"was not linked; {remedy}"
        )
    linked = handover.run(
        [handover.tj, "item", "link", items[0], url],
        inherited,
        ITEM_TIMEOUT_SECONDS,
    )
    if linked.returncode:
        raise HandoverError(
            f"{url} needs your review but `tj item link {items[0]}` failed "
            f"({reason(linked)}); {remedy}"
        )
    return items[0]
