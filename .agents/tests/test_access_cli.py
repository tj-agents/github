import io
import json
import os
import subprocess
import sys
from pathlib import Path

import access_core
import github_access
import github_binding
import github_mcp_install
import github_mcp_stdio
import github_native_git
import github_platform
import github_tools
import pytest

TOKEN = "github_pat_" + "a" * 40


class RotationVault:
    def __init__(self) -> None:
        self.checked = False
        self.writes: list[tuple[str, str]] = []

    def check(self) -> None:
        self.checked = True

    def read(self, version: str) -> str:
        raise AssertionError("not expected")

    def write(self, version: str, token: str) -> None:
        self.writes.append((version, token))

    def erase(self, version: str) -> None:
        raise AssertionError("not expected")


@pytest.fixture
def selected(tmp_path: Path) -> github_binding.Binding:
    root = tmp_path / "employer"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "remote",
            "add",
            "origin",
            "https://github.com/employer/private.git",
        ],
        check=True,
    )
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "host": "github.com",
                "organization": "employer",
                "login": "worker",
                "repositories": ["private"],
                "verify_repository": "private",
                "permissions": {
                    "contents": "write",
                    "pull_requests": "write",
                    "actions": "read",
                },
            }
        ),
        encoding="utf-8",
    )
    return github_binding.bind(
        profile,
        root,
        tmp_path / "state",
        Path(github_access.__file__).parent,
        github_platform.current_platform(),
    )


def test_cli_rejects_scope_exit_before_constructing_vault(
    selected: github_binding.Binding,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden_vault(*arguments: object) -> access_core.GCMVault:
        pytest.fail("vault constructed outside selected worktree")

    monkeypatch.setattr(access_core, "GCMVault", forbidden_vault)
    monkeypatch.chdir(tmp_path)
    assert (
        github_access.main(["gh", "--binding", str(selected.path), "--", "pr", "list"])
        == 1
    )
    assert "binding-invalid" in capsys.readouterr().err


def test_cli_check_requires_explicit_profile_or_binding(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert github_access.main(["check"]) == 1
    assert "profile-required" in capsys.readouterr().err


def test_cli_setup_check_uses_selected_source_without_credentials(
    selected: github_binding.Binding,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(github_mcp_install, "check", lambda *arguments: Path("mcp"))
    assert github_access.main(["setup", "--root", str(selected.root), "--check"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["binding"] == str(selected.path)
    assert result["native_hosts"] == "not-yet-configured"


def test_installed_launcher_loads_real_cli_and_rechecks_scope(
    selected: github_binding.Binding,
    tmp_path: Path,
) -> None:
    launcher = github_platform.current_platform().launcher_path(
        selected.launchers, github_platform.Launcher.GH
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT"}
    }
    result = subprocess.run(
        [str(launcher), "pr", "list"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 1
    assert "binding-invalid" in result.stderr
    assert "Traceback" not in result.stderr


def test_native_git_launcher_runs_local_repository_workflow(
    selected: github_binding.Binding,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_vault(*arguments: object) -> access_core.GCMVault:
        pytest.fail("local Git must not construct the employer credential vault")

    monkeypatch.setattr(access_core, "GCMVault", forbidden_vault)
    monkeypatch.chdir(selected.root)
    assert (
        github_native_git.run(selected, ("status",), github_platform.current_platform())
        == 0
    )
    launcher = github_platform.current_platform().launcher_path(
        selected.launchers, github_platform.Launcher.GIT
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT"}
    }
    for arguments in (
        ("--version",),
        ("status",),
        ("rev-parse", "--show-toplevel"),
        ("config", "user.name", "Test Worker"),
        ("config", "user.email", "worker@example.test"),
    ):
        result = subprocess.run(
            [str(launcher), *arguments],
            cwd=selected.root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert result.returncode == 0, result.stderr
    (selected.root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    for arguments in (("add", "tracked.txt"), ("commit", "-m", "local commit")):
        result = subprocess.run(
            [str(launcher), *arguments],
            cwd=selected.root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert result.returncode == 0, result.stderr
    (selected.root / "tracked.txt").write_text("amended\n", encoding="utf-8")
    for commit_arguments in (
        ("add", "tracked.txt"),
        ("commit", "-c", "HEAD", "--no-edit"),
    ):
        result = subprocess.run(
            [str(launcher), *commit_arguments],
            cwd=selected.root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_native_git_launcher_respects_cumulative_cwd_scope(
    selected: github_binding.Binding, tmp_path: Path
) -> None:
    launcher = github_platform.current_platform().launcher_path(
        selected.launchers, github_platform.Launcher.GIT
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT"}
    }
    nested = selected.root / "nested"
    nested.mkdir()
    result = subprocess.run(
        [str(launcher), "-C", "nested", "-C..", "status"],
        cwd=selected.root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    escaped = subprocess.run(
        [str(launcher), "-C", str(tmp_path), "status"],
        cwd=selected.root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert escaped.returncode == 1
    assert "binding-invalid" in escaped.stderr


def test_native_git_local_environment_has_no_credential_or_transport(
    tmp_path: Path,
) -> None:
    environment = github_native_git._local_environment(
        {"PATH": str(tmp_path), "GH_TOKEN": "token", "GITHUB_TOKEN": "token"}
    )
    assert environment["GIT_NO_LAZY_FETCH"] == "1"
    assert environment["GIT_ALLOW_PROTOCOL"] == ""
    assert "GH_TOKEN" not in environment
    assert "GITHUB_TOKEN" not in environment


def test_native_git_replays_effective_config_for_network_commands(
    selected: github_binding.Binding, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, tuple[object, ...]] = {}

    def effective(
        executable: Path, cwd: Path, source: object, timeout: float
    ) -> github_tools.GitConfigSnapshot:
        captured["effective"] = (executable, cwd, source, timeout)
        return github_tools.GitConfigSnapshot((), (), None)

    def runner(
        executable: Path,
        arguments: object,
        cwd: Path,
        environment: object,
    ) -> int:
        captured["run"] = (executable, arguments, cwd, environment)
        return 0

    monkeypatch.setattr(
        github_native_git,
        "remotes",
        lambda executable, cwd: {"origin": "https://github.com/employer/private.git"},
    )
    monkeypatch.setattr(github_tools, "_effective_git_config", effective)
    monkeypatch.setattr(
        github_tools, "_config_entries", lambda executable, cwd, source, timeout: ()
    )
    monkeypatch.setattr(github_native_git, "_run", runner)
    monkeypatch.chdir(selected.root)

    assert (
        github_native_git.run(
            selected, ("fetch", "origin"), github_platform.current_platform()
        )
        == 0
    )
    assert captured["effective"] == (selected.git, selected.root, os.environ, 60)
    assert captured["run"][:3] == (
        selected.git,
        ("fetch", "--recurse-submodules=no", "origin"),
        selected.root,
    )


@pytest.mark.parametrize(
    "arguments",
    [
        ("fetch", "https://github.com/employer/unlisted.git"),
        ("fetch", "git@github.com:employer/private.git"),
        ("archive", "origin/main"),
    ],
)
def test_native_git_denies_unsafe_network_targets_before_vault(
    selected: github_binding.Binding,
    arguments: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden_vault(*values: object) -> access_core.GCMVault:
        pytest.fail("unsafe Git command reached the credential vault")

    monkeypatch.setattr(access_core, "GCMVault", forbidden_vault)
    monkeypatch.chdir(selected.root)
    assert (
        github_access.main(
            ["native-git", "--binding", str(selected.path), "--", *arguments]
        )
        == 1
    )
    error = capsys.readouterr().err
    assert any(
        message in error
        for message in ("git-invalid", "repository-denied", "repository-invalid")
    )


def test_native_git_denies_unsafe_config_before_vault(
    selected: github_binding.Binding,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    subprocess.run(
        [str(selected.git), "config", "http.proxy", "http://proxy"],
        cwd=selected.root,
        check=True,
    )

    def forbidden_vault(*arguments: object) -> access_core.GCMVault:
        pytest.fail("unsafe Git config reached the credential vault")

    monkeypatch.setattr(access_core, "GCMVault", forbidden_vault)
    monkeypatch.chdir(selected.root)
    assert (
        github_access.main(
            ["native-git", "--binding", str(selected.path), "--", "fetch", "origin"]
        )
        == 1
    )
    assert "git-config-unsafe" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [
        ("-c", "credential.helper=store", "status"),
        ("--git-dir", ".git", "status"),
    ],
)
def test_native_git_rejects_initial_global_controls(
    selected: github_binding.Binding,
    arguments: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(selected.root)
    assert (
        github_access.main(
            ["native-git", "--binding", str(selected.path), "--", *arguments]
        )
        == 1
    )
    assert "git-invalid" in capsys.readouterr().err


@pytest.mark.parametrize("backend", ["gpg", "secretservice"])
def test_binding_activation_settings_preserve_explicit_secure_backend(
    selected: github_binding.Binding, backend: str
) -> None:
    assert (
        selected.activation_settings({"GCM_CREDENTIAL_STORE": backend})[
            "GCM_CREDENTIAL_STORE"
        ]
        == backend
    )
    assert "GCM_CREDENTIAL_STORE" not in selected.activation_settings(
        {"GCM_CREDENTIAL_STORE": "plaintext"}
    )


def test_cli_revalidates_binding_after_receiving_token_before_rotation(
    selected: github_binding.Binding,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vault = RotationVault()

    def vault_factory(*arguments: object) -> RotationVault:
        return vault

    def receive_token(profile: access_core.Profile) -> str:
        subprocess.run(
            [
                "git",
                "-C",
                str(selected.root),
                "remote",
                "set-url",
                "origin",
                "https://github.com/other/private.git",
            ],
            check=True,
        )
        return TOKEN

    def rotate(*arguments: object, **keywords: object) -> list[str]:
        pytest.fail("rotation must not begin after binding revalidation fails")

    monkeypatch.setattr(access_core, "GCMVault", vault_factory)
    monkeypatch.setattr(access_core, "receive_token", receive_token)
    monkeypatch.setattr(access_core, "rotate", rotate)
    monkeypatch.chdir(selected.root)

    assert github_access.main(["rotate", "--binding", str(selected.path)]) == 1
    assert "binding-invalid" in capsys.readouterr().err
    assert vault.checked
    assert vault.writes == []


def test_verify_tools_revalidates_before_git_probe(
    selected: github_binding.Binding,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = "employer/private"
    platform = github_platform.current_platform()
    gh_probes = 0
    git_probes = 0

    def real_gh() -> Path:
        return Path(sys.executable)

    def gh_probe(
        *arguments: object, **keywords: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal gh_probes
        gh_probes += 1
        subprocess.run(
            [
                "git",
                "-C",
                str(selected.root),
                "remote",
                "set-url",
                "origin",
                "https://github.com/other/private.git",
            ],
            check=True,
        )
        return subprocess.CompletedProcess([], 0, repository + "\n", "")

    def git_probe(*arguments: object, **keywords: object) -> int:
        nonlocal git_probes
        git_probes += 1
        return 0

    monkeypatch.setattr(platform, "real_gh", real_gh)
    monkeypatch.setattr(github_tools, "run_gh", gh_probe)
    monkeypatch.setattr(github_access, "run_git", git_probe)
    monkeypatch.chdir(selected.root)

    with pytest.raises(access_core.AccessError, match="binding-invalid"):
        github_access.verify_tools(
            selected,
            selected.profile,
            access_core.State(tmp_path / "state", selected.profile),
            RotationVault(),
            platform,
        )
    assert gh_probes == 1
    assert git_probes == 0


def test_verify_tools_revalidates_scope_exit_before_git_probe(
    selected: github_binding.Binding,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = "employer/private"
    platform = github_platform.current_platform()
    gh_probes = 0
    git_probes = 0

    def real_gh() -> Path:
        return Path(sys.executable)

    def gh_probe(
        *arguments: object, **keywords: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal gh_probes
        gh_probes += 1
        monkeypatch.chdir(tmp_path)
        return subprocess.CompletedProcess([], 0, repository + "\n", "")

    def git_probe(*arguments: object, **keywords: object) -> int:
        nonlocal git_probes
        git_probes += 1
        return 0

    monkeypatch.setattr(platform, "real_gh", real_gh)
    monkeypatch.setattr(github_tools, "run_gh", gh_probe)
    monkeypatch.setattr(github_access, "run_git", git_probe)
    monkeypatch.chdir(selected.root)

    with pytest.raises(access_core.AccessError, match="binding-invalid"):
        github_access.verify_tools(
            selected,
            selected.profile,
            access_core.State(tmp_path / "state", selected.profile),
            RotationVault(),
            platform,
        )
    assert gh_probes == 1
    assert git_probes == 0


@pytest.mark.parametrize("tool_error", [False, True])
def test_mcp_verification_exercises_bound_read_and_rejects_tool_errors(
    selected: github_binding.Binding,
    monkeypatch: pytest.MonkeyPatch,
    tool_error: bool,
) -> None:
    monkeypatch.chdir(selected.root)
    requests: list[dict[str, object]] = []

    def serve(
        binding: github_binding.Binding,
        platform: github_platform.PlatformProtocol,
        input_stream: io.StringIO,
        output_stream: io.StringIO,
    ) -> int:
        assert binding == selected
        requests.extend(
            json.loads(line) for line in input_stream.getvalue().splitlines()
        )
        output_stream.write(
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}) + "\n"
        )
        output_stream.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "isError": tool_error,
                        "content": [{"type": "text", "text": "[]"}],
                    },
                }
            )
            + "\n"
        )
        return 0

    monkeypatch.setattr(github_mcp_stdio, "serve", serve)
    if tool_error:
        with pytest.raises(access_core.AccessError, match="mcp-check-failed"):
            github_access.verify_mcp(selected, github_platform.current_platform())
    else:
        github_access.verify_mcp(selected, github_platform.current_platform())
    assert requests[-1] == {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "list_branches",
            "arguments": {"owner": "employer", "repo": "private", "perPage": 1},
        },
    }


def test_verify_tools_revalidates_before_mcp_probe(
    selected: github_binding.Binding,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform = github_platform.current_platform()
    monkeypatch.chdir(selected.root)
    monkeypatch.setattr(platform, "real_gh", lambda: Path(sys.executable))
    monkeypatch.setattr(
        github_tools,
        "run_gh",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [], 0, "employer/private\n", ""
        ),
    )

    def git_probe(*arguments: object, **keywords: object) -> int:
        monkeypatch.chdir(tmp_path)
        return 0

    def forbidden(*arguments: object, **keywords: object) -> int:
        pytest.fail("MCP must not start after scope exit")

    monkeypatch.setattr(github_access, "run_git", git_probe)
    monkeypatch.setattr(github_mcp_stdio, "serve", forbidden)
    with pytest.raises(access_core.AccessError, match="binding-invalid"):
        github_access.verify_tools(
            selected,
            selected.profile,
            access_core.State(selected.state_root, selected.profile),
            RotationVault(),
            platform,
        )
