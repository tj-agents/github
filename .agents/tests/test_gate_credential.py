import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest
from gate_sandbox import AGENT_MARKERS, CACHED_TOKEN, Sandbox
from github_gate import main

APP = {"schema_version": 1, "method": "app", "app": "sample-bot"}
GITHUB = "protocol=https\nhost=github.com\n"
TRANSPORT = "capability[]=authtype\ncapability[]=state\n" + GITHUB
BOT = base64.b64encode(f"x-access-token:{CACHED_TOKEN}".encode()).decode()
HUMAN_HELPER = """\
import json, os, sys
from pathlib import Path

with Path(os.environ["GATE_TEST_HELPER_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps({"operation": sys.argv[1], "input": sys.stdin.read()}) + "\\n")
if sys.argv[1] == "get":
    sys.stdout.write("username=human\\npassword=human-secret\\n")
"""


class Machine:
    """A sandbox with a human credential helper, the gate set up and an App."""

    def __init__(self, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch) -> None:
        self.sandbox = sandbox
        self.log = sandbox.root / "helper.log"
        helper = sandbox.root / "human_helper.py"
        helper.write_text(HUMAN_HELPER, encoding="utf-8")
        python = Path(sys.executable).resolve().as_posix()
        sandbox.git(
            sandbox.root,
            "config",
            "--global",
            "--add",
            "credential.helper",
            f"!'{python}' -B '{helper.as_posix()}'",
        )
        monkeypatch.setenv("GATE_TEST_HELPER_LOG", str(self.log))
        assert main(["setup"]) == 0
        sandbox.enrol_cached()
        self.declared = sandbox.repository("declared", APP)
        self.undeclared = sandbox.repository("undeclared")

    def operations(self) -> list[dict[str, str]]:
        if not self.log.is_file():
            return []
        lines = self.log.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]


def fill(
    cwd: Path, request: str = TRANSPORT, *options: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *options, "credential", "fill"],
        cwd=cwd,
        input=request + "\n",
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def machine(sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch) -> Machine:
    return Machine(sandbox, monkeypatch)


def test_a_human_in_an_app_repository_gets_their_own_credential(
    machine: Machine,
) -> None:
    result = fill(machine.declared)
    assert "password=human-secret" in result.stdout
    assert [entry["operation"] for entry in machine.operations()] == ["get"]


def test_an_agent_in_an_undeclared_repository_gets_the_humans_credential(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    for cwd in (machine.undeclared, machine.sandbox.neutral):
        assert "password=human-secret" in fill(cwd).stdout


@pytest.mark.parametrize("marker", AGENT_MARKERS)
def test_an_agent_in_an_app_repository_gets_only_the_app_credential(
    machine: Machine, monkeypatch: pytest.MonkeyPatch, marker: str
) -> None:
    monkeypatch.setenv(marker, "session")
    nested = machine.declared / "src" / "deep"
    nested.mkdir(parents=True)
    for cwd in (machine.declared, nested):
        result = fill(cwd)
        assert result.returncode == 0
        assert f"credential={BOT}" in result.stdout
        assert "authtype=Basic" in result.stdout
        assert "password=" not in result.stdout
    assert machine.operations() == []


def test_linked_worktrees_read_their_own_committed_declaration(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = machine.sandbox
    sandbox.git(machine.declared, "branch", "feature")
    sandbox.git(machine.declared, "checkout", "-q", "--orphan", "bare")
    sandbox.git(machine.declared, "rm", "-q", "-r", "--cached", ".")
    sandbox.git(machine.declared, "commit", "-q", "--allow-empty", "-m", "bare")
    sandbox.git(machine.declared, "checkout", "-q", "-f", "main")
    feature = sandbox.root / "feature"
    bare = sandbox.root / "bare"
    sandbox.git(machine.declared, "worktree", "add", "-q", str(feature), "feature")
    sandbox.git(machine.declared, "worktree", "add", "-q", str(bare), "bare")
    (feature / "nested").mkdir()
    monkeypatch.setenv("CLAUDECODE", "1")
    assert f"credential={BOT}" in fill(feature / "nested").stdout
    assert "password=human-secret" in fill(bare).stdout


@pytest.mark.parametrize(
    ("request_text", "options", "origin", "reason"),
    [
        (GITHUB, (), "sample-org", "Git 2.46"),
        (TRANSPORT, (), "other-org", "github-gate"),
        (
            TRANSPORT + "path=other-org/cris.git\n",
            ("-c", "credential.useHttpPath=true"),
            "sample-org",
            "github-gate",
        ),
    ],
)
def test_an_agent_the_app_cannot_serve_is_stopped_not_given_the_humans(
    machine: Machine,
    monkeypatch: pytest.MonkeyPatch,
    request_text: str,
    options: tuple[str, ...],
    origin: str,
    reason: str,
) -> None:
    repository = machine.sandbox.repository(
        "target", APP, origin=f"https://github.com/{origin}/core.git"
    )
    monkeypatch.setenv("CLAUDECODE", "1")
    result = fill(repository, request_text, *options)
    assert result.returncode != 0
    assert "told us to quit" in result.stderr
    assert reason in result.stderr
    assert "human-secret" not in result.stdout
    assert machine.operations() == []


def test_storing_the_app_credential_gives_the_humans_helpers_no_password(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    filled = fill(machine.declared).stdout
    for operation in ("approve", "reject"):
        subprocess.run(
            ["git", "credential", operation],
            cwd=machine.declared,
            input=filled + "\n",
            text=True,
            check=True,
        )
    seen = machine.operations()
    assert [entry["operation"] for entry in seen] == ["store", "erase"]
    for entry in seen:
        assert "password=" not in entry["input"]
        assert "username=x-access-token" in entry["input"]
