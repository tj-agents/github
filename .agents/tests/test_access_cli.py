import json
import os
import subprocess
from pathlib import Path

import access_core
import github_access
import github_binding
import pytest


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
        profile, root, tmp_path / "state", Path(github_access.__file__).parent
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
    launcher = selected.launchers / ("gh.exe" if os.name == "nt" else "gh")
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


def test_binding_environment_preserves_explicit_secure_backend(
    selected: github_binding.Binding,
) -> None:
    assert (
        selected.environment({"GCM_CREDENTIAL_STORE": "gpg"})["GCM_CREDENTIAL_STORE"]
        == "gpg"
    )
    assert "GCM_CREDENTIAL_STORE" not in selected.environment(
        {"GCM_CREDENTIAL_STORE": "plaintext"}
    )
