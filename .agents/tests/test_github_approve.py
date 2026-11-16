import base64
import io
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import github_approve
import pytest
from gate_sandbox import CURRENT_PLATFORM, Sandbox, fake_gh, tool_directories
from github_approve import (
    Approval,
    ApproveError,
    app_authored,
    approve,
    descendant_items,
    human_environment,
)
from github_gate import main as gate_main

HEAD = "a" * 40
APP = json.dumps({"schema_version": 1, "method": "app", "app": "sample-bot"})
HUMAN = json.dumps({"schema_version": 1, "method": "human"})
BOT = "app/sample-bot"
AGENT = {
    "CLAUDECODE": "1",
    "CLAUDE_CODE_SESSION_ID": "me",
    "CODEX_THREAD_ID": "thread",
    "GH_TOKEN": "ghs_bot",
    "GITHUB_TOKEN": "ghs_bot",
    "GH_CONFIG_DIR": "/agent/gh",
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "credential.helper",
    "GIT_CONFIG_VALUE_0": "",
    "GIT_CONFIG_GLOBAL": "/agent/gitconfig",
    "TJ_AGENT": "1",
}


def ref(number: int) -> dict[str, str]:
    return {"ref": f"github:github.com/sample-org/core:{number}"}


def session(handle: str, *items: int, parent: str | None = None) -> dict[str, Any]:
    links = [{"item": ref(item), "role": "owner"} for item in items]
    return {"session": handle, "parent": parent, "links": links}


def launch(
    launch_id: str,
    parent: str | None = None,
    child: str | None = None,
    item: int | None = None,
) -> dict[str, Any]:
    return {
        "launch_id": launch_id,
        "parent": parent,
        "session": child,
        "item": ref(item) if item else None,
    }


def url(repository: str, number: int) -> str:
    return f"https://github.com/{repository}/pull/{number}"


def review(login: str, oid: str = HEAD) -> dict[str, object]:
    return {"author": {"login": login}, "state": "APPROVED", "commit": {"oid": oid}}


def details(
    address: str,
    author: str = BOT,
    state: str = "OPEN",
    reviews: Sequence[dict[str, object]] = (),
    requests: Sequence[str] = (),
) -> dict[str, object]:
    return {
        "url": address,
        "title": f"Change {address.rsplit('/', 1)[1]}",
        "state": state,
        "author": {"login": author},
        "headRefOid": HEAD,
        "baseRefName": "main",
        "reviews": list(reviews),
        "reviewRequests": [{"login": login} for login in requests],
    }


def completed(
    command: Sequence[str], stdout: str = "", returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(list(command), returncode, stdout, stderr)


def contents(text: str) -> str:
    encoded = base64.b64encode(text.encode()).decode()
    return json.dumps({"type": "file", "encoding": "base64", "content": encoded})


def store_reply(
    command: Sequence[str], data: object
) -> subprocess.CompletedProcess[str]:
    if data is None:
        document = {"ok": False, "error": {"code": "not-found", "message": "no"}}
        return completed(command, json.dumps(document), 3)
    return completed(command, json.dumps({"ok": True, "data": data}))


@dataclass
class FakeTools:
    """agent-store, tj item and the human's gh, as `tj approve` uses them."""

    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    launches: list[dict[str, Any]] = field(default_factory=list)
    item_prs: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    pulls: dict[str, dict[str, object]] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    declaration_failure: str | None = None
    failing: tuple[str, ...] = ()
    unmergeable: tuple[str, ...] = ()
    login: str = "sample-human"
    calls: list[tuple[list[str], dict[str, str], float]] = field(default_factory=list)

    def store(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        arguments = list(command[1:])
        if arguments == ["launches"]:
            return store_reply(command, {"launches": self.launches})
        if arguments == ["items"]:
            items: dict[str, dict[str, Any]] = {}
            for record in self.sessions.values():
                for link in record["links"]:
                    entry = items.setdefault(
                        link["item"]["ref"], {"item": link["item"], "sessions": []}
                    )
                    entry["sessions"].append({"session": record["session"]})
            return store_reply(command, {"items": list(items.values())})
        if arguments[:2] == ["show", "--launch-id"]:
            match = next(
                (item for item in self.launches if item["launch_id"] == arguments[2]),
                None,
            )
            if match is None:
                return store_reply(command, None)
            bound = self.sessions.get(str(match["session"]))
            return store_reply(command, {"launch": match, "session": bound})
        shown = self.sessions.get(arguments[1])
        return store_reply(command, shown and {"session": shown})

    def gh(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        arguments = list(command[1:])
        if arguments == ["api", "user", "--jq", ".login"]:
            return completed(command, self.login + "\n")
        if arguments[0] == "api":
            path = arguments[1]
            if self.declaration_failure is not None:
                return completed(command, returncode=1, stderr=self.declaration_failure)
            if path in self.files:
                return completed(command, contents(self.files[path]))
            return completed(command, returncode=1, stderr="gh: Not Found (HTTP 404)")
        if arguments[:2] == ["pr", "view"]:
            return completed(command, json.dumps(self.pulls[arguments[2]]))
        if arguments[:3] == ["pr", "review", "--approve"]:
            if arguments[3] in self.failing:
                return completed(command, returncode=1, stderr="Review rejected\n")
            return completed(command)
        if arguments[:3] == ["pr", "merge", "--auto"]:
            if arguments[-1] in self.unmergeable:
                return completed(command, returncode=1, stderr="Auto merge off\n")
            return completed(command)
        raise AssertionError(command)

    def __call__(
        self, command: Sequence[str], environment: Mapping[str, str], timeout: float
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(command), dict(environment), timeout))
        if command[0] == "agent-store":
            return self.store(command)
        if command[0] == "tj":
            assert list(command[1:5]) == ["item", "--profile", "github", "prs"]
            pulls = [
                {"url": address, "state": state}
                for address, state in self.item_prs.get(command[5], [])
            ]
            return completed(command, json.dumps({"prs": pulls}))
        return self.gh(command)

    def commands(self, *prefix: str) -> list[list[str]]:
        return [
            command
            for command, _, _ in self.calls
            if command[: len(prefix)] == list(prefix)
        ]

    def approvals(self) -> list[str]:
        return [command[4] for command in self.commands("gh", "pr", "review")]


FIRST = url("sample-org/core", 1)
SECOND = url("sample-org/notes", 2)
APPROVED = url("sample-org/core", 3)
STALE = url("sample-org/core", 4)


def tools_with_work() -> FakeTools:
    return FakeTools(
        sessions={"claude:me": session("claude:me", 10, 11)},
        item_prs={
            ref(10)["ref"]: [(FIRST, "open"), (APPROVED, "open")],
            ref(11)["ref"]: [
                (FIRST, "open"),
                (SECOND, "open"),
                (STALE, "open"),
                (url("sample-org/core", 5), "merged"),
            ],
        },
        pulls={
            FIRST: details(FIRST),
            SECOND: details(SECOND),
            APPROVED: details(APPROVED, reviews=[review("sample-human")]),
            STALE: details(STALE, reviews=[review("sample-human", oid="b" * 40)]),
        },
        files={
            "repos/sample-org/core/contents/.agents/github-access.json?ref=main": APP,
            "repos/sample-org/.github/contents/.agents/github-access.json": APP,
        },
    )


def run_approval(
    tools: FakeTools,
    inherited: Mapping[str, str] | None = None,
    handle: str | None = None,
    answer: str = "yes",
) -> tuple[int, list[str], list[str]]:
    lines: list[str] = []
    questions: list[str] = []

    def ask(question: str) -> str:
        questions.append(question)
        return answer

    approval = Approval(
        gh="gh",
        tj="tj",
        agent_store="agent-store",
        environment=human_environment(inherited or AGENT),
        run=tools,
        out=lines.append,
        ask=ask,
    )
    code = approve(approval, inherited or AGENT, handle)
    return code, lines, questions


def test_eligible_prs_are_listed_confirmed_approved_and_auto_merged() -> None:
    tools = tools_with_work()
    code, lines, questions = run_approval(tools)
    assert code == 0
    assert lines == [
        "sample-org/core#1  Change 1",
        "sample-org/notes#2  Change 2",
        "sample-org/core#4  Change 4",
        "approved sample-org/core#1",
        "merging sample-org/core#1 when its checks pass",
        "approved sample-org/notes#2",
        "merging sample-org/notes#2 when its checks pass",
        "approved sample-org/core#4",
        "merging sample-org/core#4 when its checks pass",
    ]
    assert questions == [
        "Approve 3 pull request(s) as sample-human? Type yes to approve: "
    ]
    assert tools.approvals() == [FIRST, SECOND, STALE]
    assert tools.commands("gh", "pr", "merge") == [
        ["gh", "pr", "merge", "--auto", "--squash", "--match-head-commit", HEAD, pr]
        for pr in (FIRST, SECOND, STALE)
    ]


def test_every_tool_runs_without_agent_markers_tokens_or_git_overrides() -> None:
    tools = tools_with_work()
    run_approval(tools)
    for _, environment, _ in tools.calls:
        assert not set(environment) & set(AGENT)
        assert environment["GH_HOST"] == "github.com"
    assert human_environment({**AGENT, "PATH": "tools"}) == {
        "PATH": "tools",
        "GH_HOST": "github.com",
    }


def test_each_item_pr_and_declaration_is_read_once() -> None:
    tools = tools_with_work()
    tools.sessions["claude:me"] = session("claude:me", 10, 11, 10)
    run_approval(tools)
    assert [command[5] for command in tools.commands("tj")] == [
        ref(10)["ref"],
        ref(11)["ref"],
    ]
    assert [command[3] for command in tools.commands("gh", "pr", "view")] == [
        FIRST,
        APPROVED,
        SECOND,
        STALE,
    ]
    assert tools.commands("gh", "api", "user") == [
        ["gh", "api", "user", "--jq", ".login"]
    ]
    reads = [
        command[2] for command in tools.commands("gh", "api") if command[2] != "user"
    ]
    assert reads == [
        "repos/sample-org/core/contents/.agents/github-access.json?ref=main",
        "repos/sample-org/notes/contents/.agents/github-access.json?ref=main",
        "repos/sample-org/.github/contents/.agents/github-access.json",
    ]


def test_only_open_prs_by_the_declared_app_awaiting_you_are_kept() -> None:
    human_pr = url("sample-org/core", 6)
    closed = url("sample-org/core", 7)
    requested = url("sample-org/core", 8)
    other_app = url("sample-org/core", 9)
    opted_out = url("sample-org/sandbox", 10)
    spoofed = url("sample-org/core", 11)
    pulls = {
        human_pr: details(human_pr, author="sample-human"),
        closed: details(closed, state="CLOSED"),
        requested: details(
            requested, reviews=[review("sample-human")], requests=["sample-human"]
        ),
        other_app: details(other_app, author="app/other-bot"),
        opted_out: details(opted_out),
        spoofed: details(spoofed, author="sample-bot"),
    }
    spoofed_author = pulls[spoofed]["author"]
    assert isinstance(spoofed_author, dict)
    spoofed_author["type"] = "User"
    tools = FakeTools(
        sessions={"claude:me": session("claude:me", 10)},
        item_prs={ref(10)["ref"]: [(address, "open") for address in pulls]},
        pulls=pulls,
        files={
            "repos/sample-org/core/contents/.agents/github-access.json?ref=main": APP,
            "repos/sample-org/sandbox/contents/.agents/github-access.json?ref=main": (
                HUMAN
            ),
            "repos/sample-org/.github/contents/.agents/github-access.json": APP,
        },
    )
    run_approval(tools)
    assert tools.approvals() == [requested]


def test_a_repository_and_owner_without_declarations_have_nothing_to_approve() -> None:
    tools = tools_with_work()
    tools.files = {}
    code, lines, _ = run_approval(tools)
    assert (code, lines) == (0, ["No pull requests await your approval."])
    assert tools.approvals() == []


def test_declining_the_confirmation_approves_nothing() -> None:
    tools = tools_with_work()
    code, lines, _ = run_approval(tools, answer="y")
    assert code == 0
    assert lines[-1] == "Nothing approved."
    assert tools.commands("gh", "pr", "review") == []
    assert tools.commands("gh", "pr", "merge") == []


def test_failed_approvals_and_auto_merges_fail_the_run_after_every_pr() -> None:
    tools = tools_with_work()
    tools.failing = (SECOND,)
    tools.unmergeable = (FIRST,)
    code, lines, _ = run_approval(tools)
    assert code == 1
    assert tools.approvals() == [FIRST, SECOND, STALE]
    assert "not merging sample-org/core#1: Auto merge off" in lines
    assert "failed sample-org/notes#2: Review rejected" in lines
    assert "merging sample-org/core#4 when its checks pass" in lines


def test_an_unreadable_declaration_stops_before_any_approval() -> None:
    tools = tools_with_work()
    tools.declaration_failure = "HTTP 502: Bad Gateway"
    with pytest.raises(ApproveError, match="could not read sample-org/core@main"):
        run_approval(tools)
    assert tools.approvals() == []


def test_an_unknown_signed_in_human_stops_before_reading_prs() -> None:
    tools = tools_with_work()
    tools.login = ""
    with pytest.raises(ApproveError, match="gh could not tell who you are"):
        run_approval(tools)
    assert tools.commands("gh", "pr") == []
    assert tools.commands("tj") == []


def test_a_launch_id_resolves_the_launch_item_and_its_bound_session() -> None:
    tools = tools_with_work()
    tools.launches = [launch("L1", child="claude:bound", item=11)]
    tools.sessions = {"claude:bound": session("claude:bound", 10)}
    run_approval(tools, {**AGENT, "TJ_RUN_LAUNCH_ID": "L1"})
    assert tools.commands("agent-store", "show") == [
        ["agent-store", "show", "--launch-id", "L1"]
    ]
    assert [command[5] for command in tools.commands("tj")] == [
        ref(11)["ref"],
        ref(10)["ref"],
    ]


def test_a_codex_thread_resolves_its_native_session() -> None:
    tools = tools_with_work()
    tools.sessions = {"codex:thread": session("codex:thread", 10)}
    inherited = {key: value for key, value in AGENT.items() if "CLAUDE" not in key}
    run_approval(tools, inherited)
    assert tools.commands("agent-store", "show") == [
        ["agent-store", "show", "codex:thread"]
    ]
    assert tools.approvals() == [FIRST]


def test_an_explicit_session_overrides_inherited_ones() -> None:
    tools = tools_with_work()
    tools.sessions = {
        "codex:chosen": session("codex:chosen", 10),
        "claude:me": session("claude:me", 11),
    }
    run_approval(tools, {**AGENT, "TJ_RUN_LAUNCH_ID": "unrelated"}, "codex:chosen")
    assert [command[5] for command in tools.commands("tj")] == [ref(10)["ref"]]
    assert tools.approvals() == [FIRST]


def test_an_unknown_explicit_session_fails_without_reading_anything_else() -> None:
    tools = tools_with_work()
    with pytest.raises(ApproveError, match="unknown agent session codex:missing"):
        run_approval(tools, handle="codex:missing")
    assert [command for command, _, _ in tools.calls] == [
        ["agent-store", "show", "codex:missing"]
    ]


def test_children_are_collected_through_launches_and_session_parents() -> None:
    tools = tools_with_work()
    tools.sessions = {
        "claude:me": session("claude:me"),
        "claude:child": session("claude:child", 10),
        "codex:grandchild": session("codex:grandchild", 11, parent="claude:child"),
        "claude:stranger": session("claude:stranger", 12, parent="claude:other"),
    }
    tools.launches = [
        launch("L1", parent="claude:me", child="claude:child"),
        launch("L4", parent="claude:other", item=13),
    ]
    run_approval(tools)
    assert [command[5] for command in tools.commands("tj")] == [
        ref(10)["ref"],
        ref(11)["ref"],
    ]
    assert tools.approvals() == [FIRST, SECOND, STALE]


def test_descendants_walk_unlinked_ancestors_and_ignore_unrelated_cycles() -> None:
    tools = FakeTools(
        sessions={
            "codex:root": session("codex:root"),
            "codex:middle": session("codex:middle", parent="codex:root"),
            "claude:child": session("claude:child", 11, parent="codex:middle"),
            "codex:cycle-a": session("codex:cycle-a", 12, parent="codex:cycle-b"),
            "codex:cycle-b": session("codex:cycle-b", parent="codex:cycle-a"),
        },
        launches=[
            launch("unbound", parent="codex:middle", item=10),
            launch("unrelated", parent="codex:stranger", item=14),
        ],
    )
    approval = Approval("gh", "tj", "agent-store", {}, tools, print, input)
    assert set(descendant_items(approval, "codex:root")) == {
        ref(10)["ref"],
        ref(11)["ref"],
    }
    queried = tools.commands("agent-store", "show")
    assert len(queried) == len({command[2] for command in queried})


@pytest.mark.parametrize(
    ("sessions", "inherited", "message"),
    [
        ({}, AGENT, "No agent session found here; nothing approved."),
        (
            {"claude:me": session("claude:me")},
            AGENT,
            "No work items are linked to this session; nothing approved.",
        ),
        ({}, {"PATH": "tools"}, "No agent session found here; nothing approved."),
    ],
)
def test_no_session_or_nothing_linked_approves_nothing(
    sessions: dict[str, dict[str, Any]],
    inherited: dict[str, str],
    message: str,
) -> None:
    tools = tools_with_work()
    tools.sessions = sessions
    code, lines, _ = run_approval(tools, inherited)
    assert (code, lines) == (0, [message])
    assert not [command for command, _, _ in tools.calls if command[0] != "agent-store"]


def test_item_discovery_may_outlast_one_github_request() -> None:
    tools = tools_with_work()
    run_approval(tools)
    timeouts = {command[0]: timeout for command, _, timeout in tools.calls}
    assert timeouts["tj"] == github_approve.ITEM_TIMEOUT_SECONDS
    assert timeouts["gh"] == github_approve.TIMEOUT_SECONDS


@pytest.mark.parametrize(
    "author", [{"type": "User"}, {"__typename": "User"}, {"is_bot": False}]
)
def test_a_human_cannot_impersonate_the_app_slug(author: dict[str, object]) -> None:
    pull = details(FIRST, author="sample-bot")
    pull_author = pull["author"]
    assert isinstance(pull_author, dict)
    pull_author.update(author)
    assert not app_authored(pull, "sample-bot")
    assert app_authored(details(FIRST), "sample-bot")


@pytest.mark.parametrize("redirect", ["null", "pipe"])
def test_redirected_stdin_is_not_a_terminal(redirect: str) -> None:
    if redirect == "null":
        with Path(os.devnull).open(encoding="utf-8") as null:
            assert not github_approve.interactive(null, CURRENT_PLATFORM)
        return
    read, write = os.pipe()
    try:
        with os.fdopen(read, encoding="utf-8") as stream:
            assert not github_approve.interactive(stream, CURRENT_PLATFORM)
    finally:
        os.close(write)


def test_main_refuses_without_a_terminal_before_running_anything(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(environment: Mapping[str, str]) -> Path:
        raise AssertionError("no tool may run without a terminal")

    monkeypatch.setattr(sys, "stdin", io.StringIO("yes\n"))
    monkeypatch.setattr(github_approve, "real_gh", forbidden)
    assert github_approve.main([]) == 2
    assert "runs only in your own terminal" in capsys.readouterr().err


def test_main_uses_the_real_gh_after_the_gate_and_a_human_environment(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert gate_main(["setup"]) == 0
    real = fake_gh(sandbox.root / "real")
    for name in ("tj", "agent-store"):
        fake_gh(sandbox.root / name).rename(
            sandbox.root / name / real.name.replace("gh", name)
        )
    path = os.pathsep.join(
        [
            str(sandbox.bin),
            str(real.parent),
            str(sandbox.root / "tj"),
            str(sandbox.root / "agent-store"),
            *tool_directories(),
        ]
    )
    monkeypatch.setenv("PATH", path)
    for name, value in AGENT.items():
        monkeypatch.setenv(name, value)
    seen: list[tuple[Approval, Mapping[str, str], str | None]] = []

    def captured(
        approval: Approval, inherited: Mapping[str, str], handle: str | None
    ) -> int:
        seen.append((approval, inherited, handle))
        return 0

    monkeypatch.setattr(github_approve, "interactive", lambda stream, platform: True)
    monkeypatch.setattr(github_approve, "approve", captured)
    assert github_approve.main(["--session", "codex:chosen"]) == 0
    [(approval, inherited, handle)] = seen
    assert Path(approval.gh) == real
    assert not set(approval.environment) & set(AGENT)
    assert inherited["CLAUDE_CODE_SESSION_ID"] == "me"
    assert handle == "codex:chosen"


def test_setup_installs_a_tj_approve_launcher_that_refuses_agents(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    assert gate_main(["setup"]) == 0
    launcher = sandbox.bin / CURRENT_PLATFORM.approve_launcher_name()
    shown = subprocess.run(
        [str(launcher), "--help"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert shown.returncode == 0, shown.stderr
    assert "usage: tj approve" in shown.stdout
    refused = subprocess.run(
        [str(launcher)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert refused.returncode == 2
    assert "runs only in your own terminal" in refused.stderr
