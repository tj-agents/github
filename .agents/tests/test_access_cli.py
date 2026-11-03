import json
import os
import subprocess
import sys
from pathlib import Path

import access_core
import github_access
import github_binding
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
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert github_access.main(["setup", "--root", str(selected.root), "--check"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["binding"] == str(selected.path)
    assert result["native_hosts"] == "not-yet-configured"


def test_installed_launcher_loads_real_cli_and_rechecks_scope(
    selected: github_binding.Binding,
    tmp_path: Path,
) -> None:
    launcher = github_platform.current_platform().launcher_path(selected.launchers)
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
