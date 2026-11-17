import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from gate_app import Apps
from gate_install import BLOCK_BEGIN, BLOCK_END, check, setup
from gate_sandbox import CURRENT_PLATFORM, GATE_SOURCE, FakeGitHub, Sandbox, fake_sign
from github_gate import machine_for, main

GH = "!'C:\\\\Program Files\\\\GitHub CLI\\\\gh.exe' auth git-credential"
GH_HELPER = f'helper = "{GH}"'
HUMAN_CONFIG = """\
[user]
\tname = Sample Human
[credential "https://github.com"]
\tuseHttpPath = true
\thelper =
\thelper = !'C:\\\\Program Files\\\\GitHub CLI\\\\gh.exe' auth git-credential
[credential]
\thelper = store
[credential "https://dev.azure.com"]
\thelper = azure-only
"""


def block(sandbox: Sandbox) -> list[str]:
    content = sandbox.global_config.read_text(encoding="utf-8")
    start, stop = content.index(BLOCK_BEGIN), content.index(BLOCK_END)
    return [
        line.strip()
        for line in content[start:stop].splitlines()
        if line.strip().startswith("helper")
    ]


def launcher(sandbox: Sandbox) -> Path:
    return sandbox.bin / CURRENT_PLATFORM.launcher_name()


@pytest.fixture
def human(sandbox: Sandbox) -> Sandbox:
    sandbox.global_config.write_text(HUMAN_CONFIG, encoding="utf-8")
    return sandbox


def test_setup_installs_each_piece_once_and_check_passes(
    human: Sandbox, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["setup"]) == 0
    first = capsys.readouterr().out
    assert f"installed {launcher(human)}" in first
    assert "registered the gate's Git credential helper" in first
    content = human.global_config.read_text(encoding="utf-8")
    assert content.startswith(HUMAN_CONFIG.rstrip())
    assert content.rstrip().endswith(BLOCK_END)
    gate, *human_helpers = block(human)[1:]
    assert block(human)[0] == "helper ="
    assert "github_gate.py' credential" in gate or "github_gate.py credential" in gate
    assert human_helpers == [GH_HELPER, 'helper = "store"']
    runtime = human.gate_home / "runtime"
    assert (runtime / "github_gate.py").is_file()
    assert (runtime / "tj_credentials.py").is_file()
    assert (runtime / "credentials/mcp_schemas.json").is_file()
    assert main(["setup"]) == 0
    assert capsys.readouterr().out == "github-gate: already set up\n"
    assert human.global_config.read_text(encoding="utf-8") == content
    assert main(["setup", "--check"]) == 0
    assert capsys.readouterr().out == "github-gate: ready\n"


def add_human_helper(sandbox: Sandbox) -> None:
    content = sandbox.global_config.read_text(encoding="utf-8")
    start = content.index(BLOCK_BEGIN)
    added = '[credential "https://github.com"]\n\thelper = manager\n'
    sandbox.global_config.write_text(
        content[:start] + added + content[start:], encoding="utf-8"
    )


def test_setup_preserves_the_existing_human_path_setting(sandbox: Sandbox) -> None:
    sandbox.git(
        sandbox.neutral,
        "config",
        "--global",
        "credential.https://github.com.useHttpPath",
        "false",
    )
    assert main(["setup"]) == 0
    assert (
        sandbox.git(
            sandbox.neutral,
            "config",
            "--get-urlmatch",
            "credential.useHttpPath",
            "https://github.com/Example/other.git",
        ).strip()
        == "false"
    )


def append_after_block(sandbox: Sandbox) -> None:
    with sandbox.global_config.open("a", encoding="utf-8") as config:
        config.write('[credential "https://github.com"]\n\thelper =\n')


def remove_block(sandbox: Sandbox) -> None:
    content = sandbox.global_config.read_text(encoding="utf-8")
    sandbox.global_config.write_text(
        content[: content.index(BLOCK_BEGIN)], encoding="utf-8"
    )


def tamper_launcher(sandbox: Sandbox) -> None:
    target = launcher(sandbox)
    target.write_bytes(target.read_bytes() + b"\n# changed\n")


def tamper_approve_launcher(sandbox: Sandbox) -> None:
    target = sandbox.bin / CURRENT_PLATFORM.approve_launcher_name()
    target.write_bytes(target.read_bytes() + b"\n# changed\n")


def tamper_runtime(sandbox: Sandbox) -> None:
    target = sandbox.gate_home / "runtime" / "gate_credential.py"
    target.write_text("raise SystemExit(0)\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("drift", "reason"),
    [
        (add_human_helper, "the gate's block is stale"),
        (append_after_block, "configuration after the gate's block"),
        (remove_block, "does not register the gate's Git credential helper"),
        (tamper_launcher, "is missing or stale"),
        (tamper_approve_launcher, "tj-approve"),
        (tamper_runtime, "gate_credential.py is missing or stale"),
    ],
)
def test_check_reports_drift_that_setup_repairs(
    human: Sandbox,
    capsys: pytest.CaptureFixture[str],
    drift: Callable[[Sandbox], None],
    reason: str,
) -> None:
    assert main(["setup"]) == 0
    drift(human)
    capsys.readouterr()
    assert main(["setup", "--check"]) == 1
    assert reason in capsys.readouterr().err
    assert main(["setup"]) == 0
    assert main(["setup", "--check"]) == 0


def test_a_new_human_helper_is_re_added_after_the_gate(human: Sandbox) -> None:
    assert main(["setup"]) == 0
    add_human_helper(human)
    assert main(["setup"]) == 0
    assert block(human)[2:] == [GH_HELPER, 'helper = "store"', 'helper = "manager"']


@pytest.mark.parametrize(
    "name", [CURRENT_PLATFORM.launcher_name(), CURRENT_PLATFORM.approve_launcher_name()]
)
def test_setup_refuses_to_replace_a_command_it_did_not_install(
    sandbox: Sandbox, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    sandbox.bin.mkdir()
    foreign = sandbox.bin / name
    foreign.write_bytes(b"someone else's command")
    assert main(["setup"]) == 1
    error = capsys.readouterr().err
    assert "was not installed by the gate; refusing to replace it" in error
    assert foreign.read_bytes() == b"someone else's command"
    assert not (sandbox.gate_home / "runtime").exists()


@pytest.mark.parametrize(
    "config",
    [
        '[credential "https://github.com/acme"]\n\thelper = acme-only\n',
        '[credential "https://work@github.com"]\n\thelper = work-only\n',
        '[includeIf "gitdir:~/work/"]\n\tpath = work.gitconfig\n',
    ],
)
def test_setup_refuses_helpers_it_cannot_preserve(
    sandbox: Sandbox, capsys: pytest.CaptureFixture[str], config: str
) -> None:
    (sandbox.home / "work.gitconfig").write_text(
        "[credential]\n\thelper = work-store\n", encoding="utf-8"
    )
    sandbox.global_config.write_text(config, encoding="utf-8")
    assert main(["setup"]) == 1
    assert "the gate cannot preserve" in capsys.readouterr().err
    assert sandbox.global_config.read_text(encoding="utf-8") == config


def test_converge_refreshes_only_an_installed_runtime(sandbox: Sandbox) -> None:
    assert main(["converge"]) == 0
    assert not sandbox.gate_home.exists()
    assert main(["setup"]) == 0
    tamper_runtime(sandbox)
    installed = launcher(sandbox).read_bytes()
    assert main(["converge"]) == 0
    assert main(["setup", "--check"]) == 0
    assert launcher(sandbox).read_bytes() == installed


def test_check_requires_the_app_a_repository_declares_to_be_enrolled(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["setup"]) == 0
    repository = sandbox.repository(
        "declared", {"schema_version": 1, "method": "app", "app": "sample-bot"}
    )
    monkeypatch.chdir(repository)
    capsys.readouterr()
    assert main(["setup", "--check"]) == 1
    assert "onboard app --app sample-bot" in capsys.readouterr().err
    sandbox.enrol_cached()
    assert main(["setup", "--check"]) == 0


def test_setup_writes_only_the_gate_runtime_its_gh_and_the_global_config(
    human: Sandbox,
) -> None:
    assert main(["setup"]) == 0
    written = sorted(
        path.relative_to(human.root).as_posix()
        for path in human.root.rglob("*")
        if path.is_file()
    )
    expected = {
        f"bin/{CURRENT_PLATFORM.launcher_name()}",
        f"bin/{CURRENT_PLATFORM.approve_launcher_name()}",
        "home/.gitconfig",
        *(
            f"bin/{CURRENT_PLATFORM.command_launcher_name(name)}"
            for name in (
                "git",
                "tj-credentials",
                "nuget-plugin-tj-credentials",
                "dotnet",
                "npm",
                "docker",
                "az",
            )
        ),
    }
    assert expected <= set(written)
    assert all(
        path in expected or path.startswith("state/github-gate/runtime/")
        for path in written
    )


def test_setup_copies_shipped_packages_and_removes_stale_files(
    sandbox: Sandbox,
) -> None:
    shipped = sandbox.root / "plugin" / "gate"
    shutil.copytree(GATE_SOURCE, shipped, ignore=shutil.ignore_patterns("__pycache__"))
    (shipped / "tj_cli" / "clipboard").mkdir(parents=True)
    (shipped / "tj_cli" / "clipboard" / "__init__.py").write_text("", encoding="utf-8")
    environment = sandbox.environment()
    machine = machine_for(
        environment, CURRENT_PLATFORM, shipped / "github_gate.py", sandbox.neutral
    )
    setup(machine)
    copied = machine.runtime / "tj_cli" / "clipboard" / "__init__.py"
    assert copied.is_file()
    stale = machine.runtime / "tj_cli" / "retired.py"
    stale.write_text("", encoding="utf-8")
    assert f"{stale} is not part of the gate runtime" in check(
        machine,
        sandbox.neutral,
        Apps(sandbox.gate_home, CURRENT_PLATFORM, FakeGitHub(), fake_sign),
    )
    setup(machine)
    assert not stale.exists()
    assert copied.is_file()
