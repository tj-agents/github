import subprocess

import pytest
from credentials.model import Client, Credential, Endpoint, Provider
from gate_sandbox import CURRENT_PLATFORM, Sandbox
from github_gate import main
from test_declared_credentials import document


@pytest.mark.parametrize("marker", [None, "CODEX_THREAD_ID", "CLAUDECODE"])
def test_only_declared_agents_enable_repository_paths(
    sandbox: Sandbox, marker: str | None
) -> None:
    repository = sandbox.repository("declared", origin="https://github.com/Example/one")
    credential = Credential(
        "work",
        Provider.GITHUB_CLASSIC,
        "Example",
        "fixture-user",
        ("repo",),
        (Endpoint(Client.GIT, "https://github.com/Example/one"),),
    )
    declaration = repository / ".agents/credentials.json"
    declaration.parent.mkdir()
    declaration.write_text(document(credential), encoding="utf-8")
    sandbox.git(repository, "add", ".agents/credentials.json")
    sandbox.git(repository, "commit", "-m", "declare")
    sandbox.git(
        sandbox.neutral, "config", "--global", "credential.useHttpPath", "false"
    )
    assert main(["setup"]) == 0
    environment = sandbox.environment(**({marker: "fixture"} if marker else {}))
    result = subprocess.run(
        [
            str(sandbox.bin / CURRENT_PLATFORM.command_launcher_name("git")),
            "config",
            "--get-urlmatch",
            "credential.useHttpPath",
            "https://github.com/Example/other",
        ],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
        check=True,
    )
    assert result.stdout.strip() == ("true" if marker else "false")


def test_human_host_scoped_credentials_still_match_after_setup(
    sandbox: Sandbox,
) -> None:
    repository = sandbox.repository("human")
    store = sandbox.home / "fixture-credentials"
    sandbox.git(
        repository,
        "config",
        "--global",
        "credential.helper",
        'store --file="' + store.as_posix() + '"',
    )
    approved = subprocess.run(
        ["git", "credential", "approve"],
        cwd=repository,
        env=sandbox.environment(),
        input="protocol=https\nhost=github.com\nusername=fixture-user\n"
        "password=nonsecret-fixture\n\n",
        text=True,
        capture_output=True,
        check=True,
    )
    assert approved.returncode == 0
    assert main(["setup"]) == 0
    response = subprocess.run(
        [
            str(sandbox.bin / CURRENT_PLATFORM.command_launcher_name("git")),
            "credential",
            "fill",
        ],
        input="protocol=https\nhost=github.com\npath=Example/other\n\n",
        cwd=repository,
        env=sandbox.environment(),
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    assert "username=fixture-user" in response.stdout
    assert "password=nonsecret-fixture" in response.stdout


@pytest.mark.parametrize("declared_target", [False, True])
def test_agent_git_uses_the_effective_repository_without_changing_human_settings(
    sandbox: Sandbox,
    declared_target: bool,
) -> None:
    target = sandbox.repository("target")
    launching = sandbox.neutral if declared_target else sandbox.repository("launching")
    declared = target if declared_target else launching
    credential = Credential(
        "work",
        Provider.GITHUB_CLASSIC,
        "Example",
        "fixture-user",
        ("repo",),
        (Endpoint(Client.GIT, "https://github.com/Example/one"),),
    )
    declaration = declared / ".agents/credentials.json"
    declaration.parent.mkdir()
    declaration.write_text(document(credential), encoding="utf-8")
    sandbox.git(declared, "add", ".agents/credentials.json")
    sandbox.git(declared, "commit", "-qm", "declare")
    sandbox.git(
        sandbox.neutral, "config", "--global", "credential.useHttpPath", "false"
    )
    assert main(["setup"]) == 0
    prefixes = (
        ("-C", str(target)),
        ("-C" + str(target),),
        ("-C", str(sandbox.root), "-C", "target"),
        ("-c", "core.ignorecase=false", "-C", str(target)),
        ("--git-dir", str(target / ".git"), "--work-tree", str(target)),
        ("--git-dir=" + str(target / ".git"), "--work-tree=" + str(target)),
    )
    for prefix in prefixes:
        result = subprocess.run(
            [
                str(sandbox.bin / CURRENT_PLATFORM.command_launcher_name("git")),
                *prefix,
                "config",
                "--get-urlmatch",
                "credential.useHttpPath",
                "https://github.com/Example/other",
            ],
            cwd=launching,
            env=sandbox.environment(CODEX_THREAD_ID="fixture"),
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        assert result.returncode == 0, (prefix, result.stdout, result.stderr)
        assert result.stdout.strip() == ("true" if declared_target else "false"), (
            prefix,
            result.stdout,
            result.stderr,
        )
    assert (
        sandbox.git(
            target, "config", "--global", "--get", "credential.useHttpPath"
        ).strip()
        == "false"
    )
