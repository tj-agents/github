import json
import os
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path

import access_core
import github_access
import github_binding
import github_host_activation
import github_hosts
import github_platform
import pytest


@pytest.fixture
def selected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> github_binding.Binding:
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
    home = tmp_path / "native-home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    binding = github_binding.bind(
        profile,
        root,
        tmp_path / "state",
        Path(github_access.__file__).parent,
        github_platform.current_platform(),
    )
    monkeypatch.chdir(root)
    return binding


def test_codex_activation_preserves_policy_and_is_idempotent(
    selected: github_binding.Binding,
) -> None:
    path = selected.root / ".codex/config.toml"
    path.parent.mkdir()
    original = (
        '# retained\nmodel = "selected"\n[shell_environment_policy]\ninherit = "all"\n'
        '[shell_environment_policy.set]\nKEEP = "value"\n'
        '[mcp_servers.other]\ncommand = "other"\n'
    )
    path.write_text(original, encoding="utf-8")
    base = {"PATH": str(Path(sys.executable).parent), "GH_TOKEN": "synthetic-secret"}
    platform = github_platform.current_platform()
    result = github_host_activation.activate(
        selected, platform, github_hosts.NativeHost.CODEX, base
    )
    first = path.read_bytes()
    actual = tomllib.loads(first.decode())
    assert actual["model"] == "selected"
    assert actual["shell_environment_policy"]["set"]["KEEP"] == "value"
    assert actual["mcp_servers"]["other"] == {"command": "other"}
    assert actual["mcp_servers"]["employer_github"]["cwd"] == str(selected.root)
    assert "synthetic-secret" not in first.decode()
    assert result["required_host_arguments"] == []
    github_host_activation.activate(
        selected, platform, github_hosts.NativeHost.CODEX, base
    )
    github_host_activation.activate(
        selected, platform, github_hosts.NativeHost.CODEX, base, check=True
    )
    assert path.read_bytes() == first


def test_claude_conflicting_registration_has_no_writes(
    selected: github_binding.Binding,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = Path.home() / ".claude.json"
    original = json.dumps(
        {
            "projects": {
                str(selected.root): {
                    "mcpServers": {
                        "employer_github": {
                            "type": "stdio",
                            "command": "unrelated",
                            "args": [],
                            "env": {},
                        }
                    }
                }
            }
        }
    )
    profile.write_text(original, encoding="utf-8")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("conflicting registration must remain inert")

    monkeypatch.setattr(github_host_activation, "_claude", forbidden)
    with pytest.raises(access_core.AccessError, match="host-registration-conflict"):
        github_host_activation.activate(
            selected,
            github_platform.current_platform(),
            github_hosts.NativeHost.CLAUDE,
            {"PATH": "tools"},
        )
    assert profile.read_text(encoding="utf-8") == original
    assert not (selected.root / ".claude/settings.local.json").exists()
    assert not (selected.state_root / "github-access/activation").exists()


def test_claude_native_write_timeout_recovers_without_duplicate_registration(
    selected: github_binding.Binding,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = Path.home() / ".claude.json"
    base = {"PATH": "tools", "GH_TOKEN": "synthetic-secret"}
    command = github_hosts.mcp_command(selected, base)
    registration = {
        "type": "stdio",
        "command": command.command,
        "args": list(command.args),
        "env": dict(command.environment),
    }
    calls: list[list[str]] = []

    def native(
        executable: Path | None,
        root: Path,
        arguments: list[str],
        environment: Mapping[str, str],
    ) -> None:
        calls.append(arguments)
        assert root == selected.root
        name = arguments.index("employer_github")
        assert arguments[name - 4 : name] == [
            "--transport",
            "stdio",
            "--scope",
            "local",
        ]
        assert "GH_TOKEN=synthetic-secret" not in arguments
        profile.write_text(
            json.dumps(
                {
                    "unrelated": {"token": "unread-secret"},
                    "projects": {
                        str(root): {"mcpServers": {"employer_github": registration}}
                    },
                }
            ),
            encoding="utf-8",
        )
        raise access_core.AccessError(
            "host-registration-failed: synthetic native timeout"
        )

    monkeypatch.setattr(github_host_activation, "_claude", native)
    platform = github_platform.current_platform()
    with pytest.raises(access_core.AccessError, match="synthetic native timeout"):
        github_host_activation.activate(
            selected,
            platform,
            github_hosts.NativeHost.CLAUDE,
            base,
            executable=Path(sys.executable),
        )
    result = github_host_activation.activate(
        selected, platform, github_hosts.NativeHost.CLAUDE, base
    )
    github_host_activation.activate(
        selected, platform, github_hosts.NativeHost.CLAUDE, base, check=True
    )
    assert len(calls) == 1
    settings = selected.root / ".claude/settings.local.json"
    assert result["required_host_arguments"] == ["--settings", str(settings)]
    assert "synthetic-secret" not in settings.read_text(encoding="utf-8")
    receipts = list(
        (selected.state_root / "github-access/activation").glob("*/claude.json")
    )
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["desired"] == registration
    assert receipt["previous"] is None
    assert "unread-secret" not in receipts[0].read_text(encoding="utf-8")


def test_activation_scope_exit_precedes_settings_and_native_operations(
    selected: github_binding.Binding,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(selected.root.parent)
    with pytest.raises(access_core.AccessError, match="binding-invalid"):
        github_host_activation.activate(
            selected,
            github_platform.current_platform(),
            github_hosts.NativeHost.CODEX,
            os.environ,
        )
    assert not (selected.root / ".codex/config.toml").exists()


def test_custom_claude_profile_is_rejected_without_guessing(
    selected: github_binding.Binding,
) -> None:
    with pytest.raises(access_core.AccessError, match="host-profile-unsupported"):
        github_host_activation.activate(
            selected,
            github_platform.current_platform(),
            github_hosts.NativeHost.CLAUDE,
            {"CLAUDE_CONFIG_DIR": "custom"},
        )
    assert not (selected.root / ".claude/settings.local.json").exists()
