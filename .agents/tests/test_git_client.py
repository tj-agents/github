import json
import subprocess
import time

import pytest
from credentials.model import Client, Credential, Endpoint, Provider
from gate_private import write_private
from gate_program import real_program
from gate_sandbox import CURRENT_PLATFORM, Sandbox
from github_gate import main
from test_declared_credentials import document


@pytest.mark.parametrize("owner_default", [False, True])
@pytest.mark.parametrize("marker", [None, "CODEX_THREAD_ID", "CLAUDECODE"])
def test_app_agents_enable_paths_without_changing_human_settings(
    sandbox: Sandbox, owner_default: bool, marker: str | None
) -> None:
    app = {"schema_version": 1, "method": "app", "app": "sample-bot"}
    repository = sandbox.repository("app", None if owner_default else app)
    sandbox.git(
        sandbox.neutral, "config", "--global", "credential.useHttpPath", "false"
    )
    sandbox.git(
        sandbox.neutral,
        "config",
        "--global",
        "credential.https://github.com/other-org.useHttpPath",
        "false",
    )
    assert main(["setup"]) == 0
    sandbox.enrol_cached()
    if owner_default:
        write_private(
            sandbox.gate_home / "owner-defaults/sample-org.json",
            json.dumps({"fetched": time.time(), "text": json.dumps(app)}).encode(),
            CURRENT_PLATFORM,
        )
    result = subprocess.run(
        [
            str(sandbox.bin / CURRENT_PLATFORM.command_launcher_name("git")),
            "-c",
            "credential.https://github.com.useHttpPath=false",
            "config",
            "--get-urlmatch",
            "credential.useHttpPath",
            "https://github.com/other-org/repository.git",
        ],
        cwd=repository,
        env=sandbox.environment(**({marker: "fixture"} if marker else {})),
        text=True,
        capture_output=True,
        timeout=20,
        check=True,
    )
    assert result.stdout.strip() == ("true" if marker else "false")
    assert (
        sandbox.git(
            repository, "config", "--global", "--get", "credential.useHttpPath"
        ).strip()
        == "false"
    )


def test_app_paths_from_an_explicit_bare_git_directory(sandbox: Sandbox) -> None:
    app = {"schema_version": 1, "method": "app", "app": "sample-bot"}
    source = sandbox.repository("seed", app)
    bare = sandbox.root / "arbitrary-directory"
    sandbox.git(sandbox.root, "clone", "--bare", str(source), str(bare))
    sandbox.git(
        sandbox.neutral, "config", "--global", "credential.useHttpPath", "false"
    )
    assert main(["setup"]) == 0
    result = subprocess.run(
        [
            str(sandbox.bin / CURRENT_PLATFORM.command_launcher_name("git")),
            "--git-dir=" + str(bare),
            "config",
            "--get-urlmatch",
            "credential.useHttpPath",
            "https://github.com/other-org/repository.git",
        ],
        cwd=sandbox.neutral,
        env=sandbox.environment(CODEX_THREAD_ID="fixture"),
        text=True,
        capture_output=True,
        timeout=20,
        check=True,
    )
    assert result.stdout.strip() == "true"


def test_invalid_declaration_repair_does_not_lazily_fetch_missing_blobs(
    sandbox: Sandbox,
) -> None:
    seed = sandbox.repository("seed")
    declaration = seed / ".agents/credentials.json"
    declaration.parent.mkdir()
    declaration.write_text("{invalid", encoding="utf-8")
    history = seed / "history.txt"
    history.write_text("old content\n", encoding="utf-8")
    sandbox.git(seed, "add", ".")
    sandbox.git(seed, "commit", "-qm", "old content and invalid declaration")
    old_blob = sandbox.git(seed, "rev-parse", "HEAD:history.txt").strip()
    history.write_text("new content\n", encoding="utf-8")
    sandbox.git(seed, "commit", "-qam", "new content")
    remote = sandbox.root / "remote.git"
    sandbox.git(sandbox.root, "clone", "--bare", str(seed), str(remote))
    sandbox.git(remote, "config", "uploadpack.allowFilter", "true")
    partial = sandbox.root / "partial"
    sandbox.git(
        sandbox.root,
        "clone",
        "--filter=blob:none",
        "--no-checkout",
        remote.as_uri(),
        str(partial),
    )
    sandbox.git(partial, "checkout", "--force", "main")
    assert "?" + old_blob in sandbox.git(
        partial, "rev-list", "--objects", "--missing=print", "HEAD"
    )
    assert main(["setup"]) == 0
    refused = subprocess.run(
        [
            str(sandbox.bin / CURRENT_PLATFORM.command_launcher_name("git")),
            "diff",
            "HEAD~1",
            "HEAD",
        ],
        cwd=partial,
        env=sandbox.environment(CODEX_THREAD_ID="fixture"),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert refused.returncode != 0, (refused.stdout, refused.stderr)
    assert "?" + old_blob in sandbox.git(
        partial, "rev-list", "--objects", "--missing=print", "HEAD"
    )
    assert "old content" in sandbox.git(partial, "diff", "HEAD~1", "HEAD")


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


def test_malformed_committed_declaration_can_be_repaired_with_agent_git(
    sandbox: Sandbox,
) -> None:
    repository = sandbox.repository("repair", origin="https://github.com/Example/one")
    declaration = repository / ".agents/credentials.json"
    declaration.parent.mkdir()
    declaration.write_text('{"schema_version":1,"credentials":', encoding="utf-8")
    sandbox.git(repository, "add", ".agents/credentials.json")
    sandbox.git(repository, "commit", "-qm", "malformed declaration")
    assert main(["setup"]) == 0

    def run(
        *arguments: str, request: str | None = None, native: bool = False
    ) -> subprocess.CompletedProcess[str]:
        executable = (
            real_program("git", sandbox.environment())
            if native
            else sandbox.bin / CURRENT_PLATFORM.command_launcher_name("git")
        )
        return subprocess.run(
            [str(executable), *arguments],
            cwd=repository,
            env=sandbox.environment(CODEX_THREAD_ID="fixture"),
            input=request,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )

    refused = run(
        "credential",
        "fill",
        request="protocol=https\nhost=github.com\npath=Example/one\ncapability[]=authtype\n\n",
        native=True,
    )
    assert refused.returncode != 0
    assert "credentials-invalid" in refused.stderr
    assert "told us to quit" in refused.stderr
    status = run("status", "--short")
    assert status.returncode == 0, (status.stdout, status.stderr)
    credential = Credential(
        "work",
        Provider.GITHUB_CLASSIC,
        "Example",
        "fixture-user",
        ("repo",),
        (Endpoint(Client.GIT, "https://github.com/Example/one"),),
    )
    repaired = document(credential)
    declaration.write_text(repaired, encoding="utf-8")
    difference = run("diff", "--", ".agents/credentials.json")
    assert difference.returncode == 0, (difference.stdout, difference.stderr)
    assert difference.stdout
    added = run("add", ".agents/credentials.json")
    assert added.returncode == 0, (added.stdout, added.stderr)
    committed = run("commit", "-qm", "repair declaration")
    assert committed.returncode == 0, (committed.stdout, committed.stderr)
    assert sandbox.git(repository, "show", "HEAD:.agents/credentials.json") == repaired
    configured = run(
        "config",
        "--get-urlmatch",
        "credential.useHttpPath",
        "https://github.com/Example/one",
    )
    assert configured.returncode == 0, (configured.stdout, configured.stderr)
    assert configured.stdout.strip() == "true"


def test_malformed_committed_declaration_blocks_remote_commands(
    sandbox: Sandbox,
) -> None:
    repository = sandbox.repository("malformed")
    declaration = repository / ".agents/credentials.json"
    declaration.parent.mkdir()
    declaration.write_text('{"schema_version":1,"credentials":', encoding="utf-8")
    sandbox.git(repository, "add", ".agents/credentials.json")
    sandbox.git(repository, "commit", "-qm", "malformed declaration")
    remote = sandbox.root / "remote.git"
    sandbox.git(sandbox.root, "clone", "--bare", str(repository), str(remote))
    assert "refs/heads/main" in sandbox.git(repository, "ls-remote", str(remote))
    assert main(["setup"]) == 0
    result = subprocess.run(
        [
            str(sandbox.bin / CURRENT_PLATFORM.command_launcher_name("git")),
            "-c",
            "credential.helper=",
            "ls-remote",
            str(remote),
        ],
        cwd=repository,
        env=sandbox.environment(CODEX_THREAD_ID="fixture"),
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode != 0, (result.stdout, result.stderr)
    assert "credentials-invalid" in result.stderr
    assert "refs/heads/main" not in result.stdout


def test_bare_committed_declaration_enables_paths_only_for_agents(
    sandbox: Sandbox,
) -> None:
    repository = sandbox.repository("seed", origin="https://github.com/Example/one")
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
    sandbox.git(repository, "commit", "-qm", "declare")
    bare = sandbox.root / "bare.git"
    sandbox.git(sandbox.root, "clone", "--bare", str(repository), str(bare))
    sandbox.git(
        sandbox.neutral, "config", "--global", "credential.useHttpPath", "false"
    )
    assert main(["setup"]) == 0
    for prefix in (("-C", str(bare)), ("--git-dir=" + str(bare),)):
        for marker in (None, "CODEX_THREAD_ID"):
            result = subprocess.run(
                [
                    str(sandbox.bin / CURRENT_PLATFORM.command_launcher_name("git")),
                    *prefix,
                    "config",
                    "--get-urlmatch",
                    "credential.useHttpPath",
                    "https://github.com/Example/one",
                ],
                cwd=sandbox.neutral,
                env=sandbox.environment(**({marker: "fixture"} if marker else {})),
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            assert result.returncode == 0, (
                prefix,
                marker,
                result.stdout,
                result.stderr,
            )
            assert result.stdout.strip() == ("true" if marker else "false"), (
                prefix,
                marker,
                result.stdout,
                result.stderr,
            )
