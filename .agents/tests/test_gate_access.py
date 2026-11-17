import json
import os
from pathlib import Path

import pytest
from gate_access import CACHE_SECONDS, Declarations, OwnerDefaultError
from gate_app import GitHubError
from gate_credential import QUIT, CredentialRequest, answer
from gate_declaration import AppAccess, HumanAccess
from gate_gh import run
from gate_sandbox import (
    CACHED_TOKEN,
    COVERED,
    NOW,
    FakeGitHub,
    RecordingPlatform,
    Sandbox,
    fake_gh,
    tool_directories,
    unused_handover,
)

APP = json.dumps({"schema_version": 1, "method": "app", "app": "sample-bot"})
HUMAN = json.dumps({"schema_version": 1, "method": "human"})
TRANSPORT = CredentialRequest.parse(
    "capability[]=authtype\nprotocol=https\nhost=github.com\n"
)


def agent(sandbox: Sandbox) -> dict[str, str]:
    path = os.pathsep.join([str(sandbox.root / "real"), *tool_directories()])
    return sandbox.environment(PATH=path, CLAUDECODE="1")


def human(sandbox: Sandbox) -> dict[str, str]:
    path = os.pathsep.join([str(sandbox.root / "real"), *tool_directories()])
    return sandbox.environment(PATH=path)


@pytest.fixture
def real(sandbox: Sandbox) -> Path:
    return fake_gh(sandbox.root / "real")


@pytest.mark.parametrize(
    ("committed", "owner_default", "expected"),
    [
        (None, APP, AppAccess("sample-bot")),
        (None, HUMAN, HumanAccess()),
        (None, None, HumanAccess()),
        (json.loads(HUMAN), APP, HumanAccess()),
        (json.loads(APP), HUMAN, AppAccess("sample-bot")),
    ],
)
def test_resolve_prefers_the_repository_then_its_owners_default(
    sandbox: Sandbox,
    declarations: Declarations,
    github: FakeGitHub,
    committed: dict[str, object] | None,
    owner_default: str | None,
    expected: HumanAccess | AppAccess,
) -> None:
    sandbox.enrol_cached()
    if owner_default is not None:
        github.owner_defaults[COVERED] = owner_default
    repository = sandbox.repository("repository", committed)
    assert declarations.resolve(repository, agent(sandbox), NOW) == expected
    reads = github.owner_reads()
    assert reads == ([] if committed is not None else [(COVERED, CACHED_TOKEN)])


@pytest.mark.parametrize(
    "origin", [None, "https://example.test/sample-org/core.git", "../elsewhere"]
)
def test_a_repository_without_a_github_origin_is_human_without_a_lookup(
    sandbox: Sandbox,
    declarations: Declarations,
    github: FakeGitHub,
    origin: str | None,
) -> None:
    github.owner_defaults[COVERED] = APP
    repository = sandbox.repository("repository", origin=origin)
    assert declarations.resolve(repository, agent(sandbox), NOW) == HumanAccess()
    assert declarations.resolve(sandbox.neutral, agent(sandbox), NOW) == (HumanAccess())
    assert github.calls == []


def test_the_owner_default_is_cached_for_its_time_to_live(
    sandbox: Sandbox, declarations: Declarations, github: FakeGitHub
) -> None:
    sandbox.enrol_cached()
    github.owner_defaults[COVERED] = APP
    repository = sandbox.repository("repository")
    for moment in (NOW, NOW + CACHE_SECONDS - 1):
        assert declarations.resolve(repository, agent(sandbox), moment) == (
            AppAccess("sample-bot")
        )
    assert len(github.owner_reads()) == 1
    github.owner_defaults[COVERED] = HUMAN
    later = NOW + CACHE_SECONDS
    assert declarations.resolve(repository, agent(sandbox), later) == HumanAccess()
    assert len(github.owner_reads()) == 2
    assert declarations.resolve(repository, agent(sandbox), NOW - 1) == HumanAccess()
    assert len(github.owner_reads()) == 3


def test_only_owners_an_enrolled_app_covers_are_looked_up(
    sandbox: Sandbox, declarations: Declarations, github: FakeGitHub
) -> None:
    sandbox.enrol_cached()
    github.owner_defaults[COVERED] = APP
    github.owner_defaults["other-org"] = APP
    covered = sandbox.repository("covered")
    other = sandbox.repository("other", origin="https://github.com/other-org/cris.git")
    assert declarations.resolve(covered, agent(sandbox), NOW) == AppAccess("sample-bot")
    assert declarations.resolve(other, agent(sandbox), NOW) == HumanAccess()
    assert github.owner_reads() == [(COVERED, CACHED_TOKEN)]


@pytest.mark.parametrize("status", [403, 500])
def test_an_owner_no_enrolled_app_covers_keeps_its_normal_access_without_a_lookup(
    sandbox: Sandbox, declarations: Declarations, github: FakeGitHub, status: int
) -> None:
    github.owner_failure = GitHubError("GitHub GET failed", status)
    repository = sandbox.repository(
        "work", origin="https://github.com/other-org/cris.git"
    )
    assert declarations.resolve(repository, agent(sandbox), NOW) == HumanAccess()
    assert github.calls == []


@pytest.mark.parametrize("status", [500, 403, None])
def test_an_unreadable_owner_default_refuses_agents_and_caches_nothing(
    sandbox: Sandbox,
    declarations: Declarations,
    github: FakeGitHub,
    status: int | None,
) -> None:
    sandbox.enrol_cached()
    github.owner_failure = GitHubError("GitHub GET failed", status)
    repository = sandbox.repository("repository")
    with pytest.raises(OwnerDefaultError, match="cannot read sample-org's default"):
        declarations.resolve(repository, agent(sandbox), NOW)
    with pytest.raises(OwnerDefaultError, match="cannot read sample-org's default"):
        declarations.resolve(repository, agent(sandbox), NOW)
    assert len(github.owner_reads()) == 2


def test_an_invalid_owner_default_refuses_agents(
    sandbox: Sandbox, declarations: Declarations, github: FakeGitHub
) -> None:
    sandbox.enrol_cached()
    github.owner_defaults[COVERED] = '{"schema_version": 1, "method": "token"}'
    repository = sandbox.repository("repository")
    with pytest.raises(OwnerDefaultError, match=r"sample-org/\.github: .*human or app"):
        declarations.resolve(repository, agent(sandbox), NOW)


def test_the_shim_acts_as_the_app_an_owner_default_names(
    sandbox: Sandbox, real: Path, declarations: Declarations, github: FakeGitHub
) -> None:
    sandbox.enrol_cached()
    github.owner_defaults[COVERED] = APP
    repository = sandbox.repository("repository")
    platform = RecordingPlatform()
    run(
        ["pr", "list"],
        agent(sandbox),
        repository,
        declarations,
        platform,
        NOW,
        unused_handover(),
    )
    [(_, _, scoped)] = platform.calls
    assert scoped is not None
    assert scoped["GH_TOKEN"] == CACHED_TOKEN


def test_without_an_enrolled_app_owner_defaults_are_never_read(
    sandbox: Sandbox, real: Path, declarations: Declarations, github: FakeGitHub
) -> None:
    github.owner_defaults[COVERED] = APP
    repository = sandbox.repository("repository")
    assert declarations.resolve(repository, agent(sandbox), NOW) == HumanAccess()
    assert github.owner_reads() == []


def test_an_unreadable_owner_default_stops_agents_and_never_humans(
    sandbox: Sandbox, real: Path, declarations: Declarations, github: FakeGitHub
) -> None:
    sandbox.enrol_cached()
    github.owner_failure = GitHubError("GitHub GET failed", 502)
    repository = sandbox.repository("repository")
    platform = RecordingPlatform()
    with pytest.raises(OwnerDefaultError, match="agents in sample-org repositories"):
        run(
            ["pr", "list"],
            agent(sandbox),
            repository,
            declarations,
            platform,
            NOW,
            unused_handover(),
        )
    reply = answer("get", TRANSPORT, agent(sandbox), repository, declarations, NOW)
    assert reply.text == QUIT
    assert reply.message is not None
    assert "cannot read sample-org's default" in reply.message
    assert platform.calls == []
    reads = len(github.owner_reads())
    run(
        ["pr", "list"],
        human(sandbox),
        repository,
        declarations,
        platform,
        NOW,
        unused_handover(),
    )
    reply = answer("get", TRANSPORT, human(sandbox), repository, declarations, NOW)
    assert platform.calls == [(real, ("pr", "list"), None)]
    assert reply.text == ""
    assert len(github.owner_reads()) == reads


def test_humans_never_look_up_an_owner_default(
    sandbox: Sandbox, real: Path, declarations: Declarations, github: FakeGitHub
) -> None:
    github.owner_defaults[COVERED] = APP
    repository = sandbox.repository("repository")
    platform = RecordingPlatform()
    run(
        ["pr", "list"],
        human(sandbox),
        repository,
        declarations,
        platform,
        NOW,
        unused_handover(),
    )
    reply = answer("get", TRANSPORT, human(sandbox), repository, declarations, NOW)
    assert reply.text == ""
    assert github.calls == []


def test_the_credential_helper_answers_as_the_owner_defaults_app(
    sandbox: Sandbox, declarations: Declarations, github: FakeGitHub
) -> None:
    sandbox.enrol_cached()
    github.owner_defaults[COVERED] = APP
    repository = sandbox.repository("repository")
    reply = answer("get", TRANSPORT, agent(sandbox), repository, declarations, NOW)
    assert "authtype=Basic" in reply.text
    assert "username=x-access-token" in reply.text
