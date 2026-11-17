"""Approve, as yourself, the GitHub App's pull requests for an agent session's work.

Only the human runs `tj approve`; agents are denied it by their hosts' permissions. It
approves only open pull requests, linked to the session's work items, that the GitHub
App their repository declares opened and that await your approval at their current
head, then arms squash auto-merge, printing each pull request it approved.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import assert_never

from gate_access import OWNER_REPOSITORY, decoded
from gate_agent import AgentHost
from gate_declaration import DECLARATION, AppAccess, HumanAccess, parse
from gate_error import GateError
from gate_gh import real_gh
from gate_process import Run, reason, run_captured
from gate_session import AgentStore, current_session, descendant_items

TIMEOUT_SECONDS = 30
ITEM_TIMEOUT_SECONDS = 120
PULL_URL = re.compile(r"https://github\.com/([A-Za-z0-9-]+)/([\w.-]+)/pull/(\d+)")
PR_FIELDS = "url,title,state,author,headRefOid,baseRefName,reviews,reviewRequests"
REMOVED = frozenset(
    {
        "TJ_AGENT",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
        "GH_CONFIG_DIR",
        "GH_HOST",
        "GH_REPO",
    }
)


class ApproveError(GateError):
    pass


def human_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """The environment with every agent marker, agent token and Git override removed."""
    markers: set[str] = set()
    for host in AgentHost:
        markers.update(host.markers)
    result = {
        key: value
        for key, value in environment.items()
        if key.upper() not in REMOVED
        and key.upper() not in markers
        and not key.upper().startswith("GIT_CONFIG")
    }
    result["GH_HOST"] = "github.com"
    return result


@dataclass
class Approval:
    """One `tj approve` run: the tools it uses and what it prints."""

    gh: str
    tj: str
    agent_store: str
    environment: Mapping[str, str]
    run: Run
    out: Callable[[str], None]
    apps: dict[tuple[str, str, str], str | None] = field(default_factory=dict)
    owners: dict[str, str | None] = field(default_factory=dict)

    @property
    def sessions(self) -> AgentStore:
        return AgentStore(self.agent_store, self.environment, self.run)

    def output(
        self, command: Sequence[str], timeout: float = TIMEOUT_SECONDS
    ) -> subprocess.CompletedProcess[str]:
        return self.run(command, self.environment, timeout)

    def json(self, command: Sequence[str], timeout: float = TIMEOUT_SECONDS) -> object:
        result = self.output(command, timeout)
        label = " ".join([Path(command[0]).stem, *command[1:3]])
        if result.returncode:
            raise ApproveError(f"{label} failed: {reason(result)}")
        try:
            return json.loads(result.stdout)
        except ValueError as error:
            raise ApproveError(f"{label} returned invalid JSON") from error

    def reviewer(self) -> str:
        result = self.output([self.gh, "api", "user", "--jq", ".login"])
        login = result.stdout.strip()
        if result.returncode or not re.fullmatch(r"[A-Za-z0-9-]+", login):
            raise ApproveError(
                f"gh could not tell who you are ({reason(result)}); sign in with "
                "`gh auth login` and retry; nothing approved"
            )
        return login

    def declared_app(self, owner: str, repository: str, ref: str) -> str | None:
        """The App slug the base branch, else the owner's default, declares."""
        key = (owner.lower(), repository.lower(), ref)
        if key not in self.apps:
            path = (
                f"repos/{owner}/{repository}/contents/{DECLARATION}?ref="
                + urllib.parse.quote(ref, safe="")
            )
            text = self.contents(path, f"{owner}/{repository}@{ref}/{DECLARATION}")
            if text is None:
                self.apps[key] = self.owner_app(owner)
            else:
                self.apps[key] = app_slug(text)
        return self.apps[key]

    def owner_app(self, owner: str) -> str | None:
        if owner.lower() not in self.owners:
            path = f"repos/{owner}/{OWNER_REPOSITORY}/contents/{DECLARATION}"
            where = f"{owner}/{OWNER_REPOSITORY}/{DECLARATION}"
            text = self.contents(path, where)
            self.owners[owner.lower()] = None if text is None else app_slug(text)
        return self.owners[owner.lower()]

    def contents(self, path: str, where: str) -> str | None:
        result = self.output([self.gh, "api", path])
        if result.returncode:
            if "HTTP 404" in result.stderr:
                return None
            raise ApproveError(f"could not read {where}: {reason(result)}")
        try:
            response = json.loads(result.stdout)
        except ValueError as error:
            raise ApproveError(f"GitHub returned invalid JSON for {where}") from error
        return decoded(response, where)


def app_slug(text: str) -> str | None:
    access = parse(text)
    match access:
        case HumanAccess():
            return None
        case AppAccess(slug=slug):
            return slug
        case _:
            assert_never(access)


def app_authored(details: Mapping[str, object], slug: str) -> bool:
    author = details.get("author") or {}
    assert isinstance(author, dict)
    if (
        author.get("type") == "User"
        or author.get("__typename") == "User"
        or author.get("is_bot") is False
    ):
        return False
    return author.get("login") in (f"app/{slug}", slug, f"{slug}[bot]")


def awaits(details: Mapping[str, object], reviewer: str) -> bool:
    head = details["headRefOid"]
    requests = details.get("reviewRequests") or []
    reviews = details.get("reviews") or []
    assert isinstance(requests, list)
    assert isinstance(reviews, list)
    if any(
        str(request.get("login", "")).casefold() == reviewer.casefold()
        for request in requests
    ):
        return True
    return not any(
        review.get("state") == "APPROVED"
        and str((review.get("author") or {}).get("login", "")).casefold()
        == reviewer.casefold()
        and (review.get("commit") or {}).get("oid") == head
        for review in reviews
    )


@dataclass(frozen=True, slots=True)
class Waiting:
    name: str
    title: str
    url: str
    head: str


def pending(approval: Approval, items: Sequence[str], reviewer: str) -> list[Waiting]:
    urls: list[str] = []
    for item in dict.fromkeys(items):
        found = approval.json(
            [approval.tj, "item", "--profile", "github", "prs", item],
            ITEM_TIMEOUT_SECONDS,
        )
        assert isinstance(found, dict)
        urls.extend(pull["url"] for pull in found["prs"] if pull.get("state") == "open")
    waiting: list[Waiting] = []
    for url in dict.fromkeys(urls):
        match = PULL_URL.fullmatch(url)
        if match is None:
            continue
        owner, repository, number = match.groups()
        details = approval.json([approval.gh, "pr", "view", url, "--json", PR_FIELDS])
        assert isinstance(details, dict)
        if details.get("state") != "OPEN":
            continue
        slug = approval.declared_app(owner, repository, str(details["baseRefName"]))
        if slug is None or not app_authored(details, slug):
            continue
        if awaits(details, reviewer):
            name = f"{owner}/{repository}#{number}"
            title = str(details.get("title", ""))
            waiting.append(Waiting(name, title, url, str(details["headRefOid"])))
    return waiting


def approve(
    approval: Approval, environment: Mapping[str, str], handle: str | None
) -> int:
    session = current_session(approval.sessions, environment, handle)
    if session is None:
        approval.out("No agent session found here; nothing approved.")
        return 0
    origin, items = session
    if origin:
        items = [*items, *descendant_items(approval.sessions, origin)]
    if not items:
        approval.out("No work items are linked to this session; nothing approved.")
        return 0
    reviewer = approval.reviewer()
    waiting = pending(approval, items, reviewer)
    if not waiting:
        approval.out("No pull requests await your approval.")
        return 0
    for item in waiting:
        approval.out(f"{item.name}  {item.title}")
    failed = False
    for item in waiting:
        result = approval.output([approval.gh, "pr", "review", "--approve", item.url])
        if result.returncode:
            failed = True
            approval.out(f"failed {item.name}: {reason(result)}")
            continue
        approval.out(f"approved {item.name}")
        merge = approval.output(
            [
                approval.gh,
                "pr",
                "merge",
                "--auto",
                "--squash",
                "--match-head-commit",
                item.head,
                item.url,
            ]
        )
        if merge.returncode:
            failed = True
            approval.out(f"not merging {item.name}: {reason(merge)}")
        else:
            approval.out(f"merging {item.name} when its checks pass")
    return 1 if failed else 0


def tool(name: str, environment: Mapping[str, str]) -> str:
    found = shutil.which(name, path=environment.get("PATH"))
    if not found:
        raise ApproveError(f"{name} is not on PATH")
    return found


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="tj approve", description=__doc__)
    result.add_argument(
        "--session", metavar="HOST:ID", help="the agent session whose work to approve"
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    inherited = dict(os.environ)
    environment = human_environment(inherited)
    try:
        approval = Approval(
            gh=str(real_gh(environment)),
            tj=tool("tj", environment),
            agent_store=tool("agent-store", environment),
            environment=environment,
            run=run_captured,
            out=print,
        )
        return approve(approval, inherited, arguments.session)
    except (GateError, OSError) as error:
        print(f"tj approve: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
