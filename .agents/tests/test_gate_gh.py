import json
import os
import subprocess
from pathlib import Path

import pytest
from gate_access import Declarations
from gate_app import AppError, Apps
from gate_error import GateError
from gate_gh import ShimError, real_gh, run
from gate_sandbox import (
    AGENT_MARKERS,
    CURRENT_PLATFORM,
    NOW,
    FakeGitHub,
    RecordingPlatform,
    Sandbox,
    fake_gh,
    tool_directories,
    unused_handover,
)
from github_gate import main

APP = {"schema_version": 1, "access": {"method": "app", "app": "sample-bot"}}
KEY = b"-----BEGIN PRIVATE KEY-----\nsample\n-----END PRIVATE KEY-----\n"


def agent_environment(sandbox: Sandbox, **extra: str) -> dict[str, str]:
    path = os.pathsep.join([str(sandbox.root / "real"), *tool_directories()])
    return sandbox.environment(PATH=path, CLAUDECODE="1", **extra)


@pytest.fixture
def real(sandbox: Sandbox) -> Path:
    return fake_gh(sandbox.root / "real")


@pytest.fixture
def enrolled(apps: Apps, declarations: Declarations) -> Declarations:
    apps("sample-bot").enrol(KEY, NOW)
    return declarations


def test_a_human_reaches_the_real_gh_with_the_inherited_environment(
    sandbox: Sandbox, real: Path, enrolled: Declarations, github: FakeGitHub
) -> None:
    repository = sandbox.repository("declared", APP)
    platform = RecordingPlatform()
    environment = sandbox.environment(PATH=str(real.parent))
    run(
        ["pr", "list"],
        environment,
        repository,
        enrolled,
        platform,
        NOW,
        unused_handover(),
    )
    assert platform.calls == [(real, ("pr", "list"), None)]
    assert github.issued == 0


@pytest.mark.parametrize(
    "declaration", [None, {"schema_version": 1, "access": {"method": "human"}}]
)
def test_an_agent_outside_app_repositories_reaches_the_real_gh_untouched(
    sandbox: Sandbox,
    real: Path,
    enrolled: Declarations,
    github: FakeGitHub,
    declaration: dict[str, object] | None,
) -> None:
    repository = sandbox.repository("work", declaration)
    platform = RecordingPlatform()
    for cwd in (repository, sandbox.neutral):
        run(
            ["pr", "list"],
            agent_environment(sandbox),
            cwd,
            enrolled,
            platform,
            NOW,
            unused_handover(),
        )
    assert platform.calls == [(real, ("pr", "list"), None)] * 2
    reads = [] if declaration else [("sample-org", "ghs_issued" + "1".zfill(32))]
    assert github.owner_reads() == reads


@pytest.mark.parametrize("marker", AGENT_MARKERS)
def test_an_agent_in_an_app_repository_acts_as_the_app_for_its_origin(
    sandbox: Sandbox, real: Path, enrolled: Declarations, marker: str
) -> None:
    repository = sandbox.repository("declared", APP)
    platform = RecordingPlatform()
    path = os.pathsep.join([str(real.parent), *tool_directories()])
    environment = sandbox.environment(PATH=path, **{marker: "session"})
    run(
        ["pr", "list"],
        environment,
        repository,
        enrolled,
        platform,
        NOW,
        unused_handover(),
    )
    [(executable, arguments, scoped)] = platform.calls
    assert (executable, arguments) == (real, ("pr", "list"))
    assert scoped is not None
    assert scoped["GH_TOKEN"].startswith("ghs_issued")
    assert {key: value for key, value in scoped.items() if key != "GH_TOKEN"} == (
        environment
    )


@pytest.mark.parametrize(
    ("arguments", "origin"),
    [
        (("issue", "list", "-R", "other-org/cris"), "sample-org"),
        (("pr", "list"), "other-org"),
        (("api", "repos/other-org/cris"), "sample-org"),
    ],
)
def test_an_agent_targeting_an_account_the_app_does_not_cover_is_refused(
    sandbox: Sandbox,
    real: Path,
    enrolled: Declarations,
    github: FakeGitHub,
    arguments: tuple[str, ...],
    origin: str,
) -> None:
    repository = sandbox.repository(
        "declared", APP, origin=f"https://github.com/{origin}/core.git"
    )
    platform = RecordingPlatform()
    with pytest.raises(AppError, match="other-org is not covered by GitHub App"):
        run(
            arguments,
            agent_environment(sandbox),
            repository,
            enrolled,
            platform,
            NOW,
            unused_handover(),
        )
    assert platform.calls == []
    assert github.issued == 0


@pytest.mark.parametrize(
    ("arguments", "extra", "reason"),
    [
        (("auth", "token"), {}, "gh auth status"),
        (("auth", "status", "--show-token"), {}, "gh auth status"),
        (("auth", "git-credential", "get"), {}, "gh auth status"),
        (("auth", "login"), {}, "gh auth status"),
        (
            ("pr", "view", "https://github.com/sample-org/c/pull/1", "-R", "o/x"),
            {},
            "single GitHub account",
        ),
        (("api", "user", "--hostname", "ghe.example"), {}, "only on github.com"),
        (("api", "user"), {"GH_HOST": "ghe.example"}, "only on github.com"),
    ],
)
def test_agent_commands_that_could_reach_human_credentials_are_refused(
    sandbox: Sandbox,
    real: Path,
    enrolled: Declarations,
    arguments: tuple[str, ...],
    extra: dict[str, str],
    reason: str,
) -> None:
    repository = sandbox.repository("declared", APP)
    platform = RecordingPlatform()
    with pytest.raises(ShimError, match=reason):
        run(
            arguments,
            agent_environment(sandbox, **extra),
            repository,
            enrolled,
            platform,
            NOW,
            unused_handover(),
        )
    assert platform.calls == []


def test_an_agent_without_an_enrolled_app_is_refused(
    sandbox: Sandbox, real: Path, declarations: Declarations
) -> None:
    repository = sandbox.repository("declared", APP)
    platform = RecordingPlatform()
    with pytest.raises(GateError, match="not enrolled on this machine"):
        run(
            ["pr", "list"],
            agent_environment(sandbox),
            repository,
            declarations,
            platform,
            NOW,
            unused_handover(),
        )
    assert platform.calls == []


def test_real_gh_skips_the_gates_own_launchers(sandbox: Sandbox, real: Path) -> None:
    sandbox.bin.mkdir()
    launcher = sandbox.bin / CURRENT_PLATFORM.launcher_name()
    CURRENT_PLATFORM.write_launcher(
        launcher,
        CURRENT_PLATFORM.launcher_bytes(Path("python"), Path("github_gate.py")),
    )
    path = os.pathsep.join([str(sandbox.bin), str(real.parent)])
    assert real_gh({"PATH": path}) == real
    with pytest.raises(ShimError, match="real gh is not on PATH"):
        real_gh({"PATH": str(sandbox.bin)})


def dump(sandbox: Sandbox, command: list[str], cwd: Path) -> dict[str, object]:
    target = sandbox.root / "dump.json"
    target.unlink(missing_ok=True)
    subprocess.run(command, cwd=cwd, check=False)
    result: dict[str, object] = json.loads(target.read_text(encoding="utf-8"))
    return result


def test_the_installed_shim_passes_a_humans_environment_through_unchanged(
    sandbox: Sandbox, real: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert main(["setup"]) == 0
    launcher = sandbox.bin / CURRENT_PLATFORM.launcher_name()
    path = os.pathsep.join([str(sandbox.bin), str(real.parent), *tool_directories()])
    monkeypatch.setenv("PATH", path)
    monkeypatch.setenv("GATE_TEST_DUMP", str(sandbox.root / "dump.json"))
    repository = sandbox.repository("declared", APP)
    command = ["pr", "list", "--search", "is:open author:@me", ""]
    direct = dump(sandbox, [str(real), *command], repository)
    shimmed = dump(sandbox, [str(launcher), *command], repository)
    assert shimmed == direct
    monkeypatch.setenv("CLAUDECODE", "1")
    undeclared = sandbox.repository("undeclared", origin=None)
    direct = dump(sandbox, [str(real), *command], undeclared)
    assert dump(sandbox, [str(launcher), *command], undeclared) == direct


def test_the_installed_shim_returns_the_real_gh_status(
    sandbox: Sandbox, real: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert main(["setup"]) == 0
    path = os.pathsep.join([str(sandbox.bin), str(real.parent), *tool_directories()])
    monkeypatch.setenv("PATH", path)
    monkeypatch.setenv("GATE_TEST_DUMP", str(sandbox.root / "dump.json"))
    monkeypatch.setenv("GATE_TEST_STATUS", "3")
    launcher = sandbox.bin / CURRENT_PLATFORM.launcher_name()
    assert subprocess.run([str(launcher), "--version"], check=False).returncode == 3
