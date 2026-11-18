import json
import os
import subprocess
from pathlib import Path

import pytest
from gate_access import Declarations
from gate_gh import run
from gate_handover import APPROVE_LINE, awaits_approval
from gate_sandbox import (
    CACHED_TOKEN,
    FakeHandover,
    RecordingPlatform,
    Sandbox,
    fake_gh,
    tool_directories,
)

APP = {"schema_version": 1, "access": {"method": "app", "app": "sample-bot"}}
URL = "https://github.com/sample-org/core/pull/5"
ITEM = "github:github.com/sample-org/core:10"
NOW = 1_800_000_000.0


def done(
    stdout: str = "", returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def session(*items: str) -> subprocess.CompletedProcess[str]:
    links = [{"item": {"ref": item}} for item in items]
    data = {"session": {"session": "claude:me", "links": links}}
    return done(json.dumps({"ok": True, "data": data}))


def decision(value: str) -> tuple[tuple[str, ...], subprocess.CompletedProcess[str]]:
    gh = "gh.exe" if os.name == "nt" else "gh"
    key = (gh, "pr", "view", URL, "--json", "reviewDecision", "--jq", ".reviewDecision")
    return key, done(value + "\n")


def link_key(item: str = ITEM) -> tuple[str, ...]:
    return ("tj", "item", "link", item, URL)


@pytest.fixture
def repository(sandbox: Sandbox) -> Path:
    sandbox.enrol_cached()
    return sandbox.repository("declared", APP)


@pytest.fixture
def agent(sandbox: Sandbox) -> dict[str, str]:
    fake_gh(sandbox.root / "real")
    path = os.pathsep.join([str(sandbox.root / "real"), *tool_directories()])
    return sandbox.environment(PATH=path, CLAUDECODE="1", CLAUDE_CODE_SESSION_ID="me")


def handed(
    arguments: list[str],
    fake: FakeHandover,
    repository: Path,
    agent: dict[str, str],
    declarations: Declarations,
) -> tuple[int, RecordingPlatform]:
    platform = RecordingPlatform()
    status = run(
        arguments, agent, repository, declarations, platform, NOW, fake.handover()
    )
    return status, platform


def review_required(item: str = ITEM) -> FakeHandover:
    key, reply = decision("REVIEW_REQUIRED")
    return FakeHandover(
        launched=(0, URL + "\n"),
        replies={
            key: reply,
            ("agent-store", "show", "claude:me"): session(item),
            link_key(item): done(),
        },
    )


@pytest.mark.parametrize(
    "item",
    [
        ITEM,
        "azure-devops:dev.azure.com/sample-org:10",
        "jira:example.atlassian.net:DEMO-10",
    ],
)
def test_a_pr_that_needs_review_is_linked_copied_and_reported(
    repository: Path, agent: dict[str, str], declarations: Declarations, item: str
) -> None:
    fake = review_required(item)
    status, platform = handed(
        ["pr", "create", "--title", "Change", "--body", "Why"],
        fake,
        repository,
        agent,
        declarations,
    )
    assert status == 0
    assert platform.calls == []
    [(_, arguments, scoped, capture)] = fake.launches
    assert arguments == ("pr", "create", "--title", "Change", "--body", "Why")
    assert (scoped["GH_TOKEN"], capture) == (CACHED_TOKEN, True)
    [(_, decision_environment), _, (link, link_environment)] = fake.runs
    assert decision_environment["GH_TOKEN"] == CACHED_TOKEN
    assert link == link_key(item)
    assert link_environment == agent
    assert fake.copied == [APPROVE_LINE]
    assert fake.reports == [
        f"{URL} needs your review: linked it to {item}, and `! tj approve` is on "
        "your clipboard; paste it into this session to approve"
    ]


@pytest.mark.parametrize("value", ["APPROVED", "", "CHANGES_REQUESTED"])
def test_a_pr_that_needs_no_review_is_left_alone(
    repository: Path, agent: dict[str, str], declarations: Declarations, value: str
) -> None:
    key, reply = decision(value)
    fake = FakeHandover(launched=(0, URL + "\n"), replies={key: reply})
    status, _ = handed(["pr", "create"], fake, repository, agent, declarations)
    assert status == 0
    assert [command for command, _ in fake.runs] == [key]
    assert (fake.copied, fake.reports) == ([], [])


def test_a_failed_link_is_reported_without_changing_gh_status(
    repository: Path, agent: dict[str, str], declarations: Declarations
) -> None:
    fake = review_required()
    fake.replies[link_key()] = done(returncode=1, stderr="item not found")
    status, _ = handed(["pr", "create"], fake, repository, agent, declarations)
    assert status == 0
    assert fake.copied == []
    [report] = fake.reports
    assert f"`tj item link {ITEM}` failed (item not found)" in report
    assert f"`tj item link <ref> {URL}`" in report
    assert "`agent-store link --session claude:me --item <ref>`" in report


def test_a_failed_gh_command_keeps_its_status_and_hands_nothing_over(
    repository: Path, agent: dict[str, str], declarations: Declarations
) -> None:
    fake = review_required()
    fake.launched = (4, "")
    status, _ = handed(["pr", "create"], fake, repository, agent, declarations)
    assert status == 4
    assert (fake.runs, fake.copied, fake.reports) == ([], [], [])


@pytest.mark.parametrize(
    "arguments",
    [
        ["pr", "create", "--draft"],
        ["pr", "new", "-d"],
        ["pr", "create", "--web"],
        ["pr", "create", "--dry-run"],
        ["pr", "ready", "5", "--undo"],
        ["pr", "view", "5"],
    ],
)
def test_drafts_previews_and_other_commands_run_gh_with_no_handover(
    repository: Path,
    agent: dict[str, str],
    declarations: Declarations,
    arguments: list[str],
) -> None:
    fake = FakeHandover(launched=None)
    status, platform = handed(arguments, fake, repository, agent, declarations)
    assert status == 0
    [(_, ran, scoped)] = platform.calls
    assert ran == tuple(arguments)
    assert scoped is not None
    assert scoped["GH_TOKEN"] == CACHED_TOKEN
    assert (fake.launches, fake.runs, fake.reports) == ([], [], [])


def test_a_readied_pr_is_found_by_its_selector_and_handed_over(
    repository: Path, agent: dict[str, str], declarations: Declarations
) -> None:
    fake = review_required()
    fake.launched = (0, "")
    gh = "gh.exe" if os.name == "nt" else "gh"
    view = (gh, "pr", "view", "5", "-R", "sample-org/core", "--json", "url")
    fake.replies[view] = done(json.dumps({"url": URL}))
    status, _ = handed(
        ["pr", "ready", "5", "-R", "sample-org/core"],
        fake,
        repository,
        agent,
        declarations,
    )
    assert status == 0
    assert fake.launches[0][3] is False
    assert fake.copied == [APPROVE_LINE]


@pytest.mark.parametrize(
    ("store", "reason"),
    [
        (done(json.dumps({"ok": False, "error": {"code": "not-found"}})), "no agent"),
        (session(), "no linked work item"),
        (session(ITEM, "github:github.com/sample-org/core:11"), "2 work items"),
    ],
)
def test_a_session_without_exactly_one_item_is_reported(
    repository: Path,
    agent: dict[str, str],
    declarations: Declarations,
    store: subprocess.CompletedProcess[str],
    reason: str,
) -> None:
    fake = review_required()
    fake.replies[("agent-store", "show", "claude:me")] = store
    status, _ = handed(["pr", "create"], fake, repository, agent, declarations)
    assert status == 0
    assert fake.copied == []
    [report] = fake.reports
    assert reason in report
    assert link_key() not in [command for command, _ in fake.runs]


def test_an_unavailable_clipboard_is_reported_after_linking(
    repository: Path, agent: dict[str, str], declarations: Declarations
) -> None:
    fake = review_required()
    fake.copy_failure = "No clipboard tool was found on PATH"
    status, _ = handed(["pr", "create"], fake, repository, agent, declarations)
    assert status == 0
    [report] = fake.reports
    assert f"is linked to {ITEM}" in report
    assert "clipboard is unavailable (No clipboard tool was found on PATH)" in report
    assert "run `! tj approve` in this session" in report


@pytest.mark.parametrize(
    ("arguments", "awaits"),
    [
        (["pr", "create"], True),
        (["pr", "create", "--title", "--draft"], True),
        (["pr", "create", "--draft"], False),
        (["pr", "ready"], True),
        (["pr", "ready", "--undo"], False),
        (["pr", "merge", "5"], False),
        (["issue", "create"], False),
    ],
)
def test_awaits_approval_only_after_reviewable_pr_commands(
    arguments: list[str], awaits: bool
) -> None:
    assert awaits_approval(arguments) is awaits


def test_a_malformed_session_reply_is_reported_without_changing_gh_status(
    repository: Path, agent: dict[str, str], declarations: Declarations
) -> None:
    fake = review_required()
    malformed = {"ok": True, "data": {"session": {"links": [{}]}}}
    fake.replies[("agent-store", "show", "claude:me")] = done(json.dumps(malformed))
    status, _ = handed(["pr", "create"], fake, repository, agent, declarations)
    assert status == 0
    [report] = fake.reports
    assert report.startswith("the pull request was not handed over (KeyError")
    assert fake.copied == []
