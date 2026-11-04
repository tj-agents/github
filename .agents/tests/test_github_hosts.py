from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path

import pytest
from github_hosts import (
    MCP_ENV_VARS,
    MCPCommand,
    NativeHost,
    SettingsError,
    apply_settings,
    mcp_command,
    public_environment,
    render_claude,
    render_codex,
    stage_setting,
)


class Binding:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / ".agents" / "github-access.json"
        self.runtime = root / ".agents-state" / "runtime"
        self.launchers = root / ".agents-state" / "bin"

    def activation_settings(self, base: Mapping[str, str]) -> dict[str, str]:
        return {
            "PATH": os.pathsep.join(
                (str(self.launchers), base.get("PATH", ""), str(self.launchers))
            ),
            "GITHUB_ACCESS_BINDING": str(self.path),
            "AGENT_STATE_DIRECTORY": str(self.runtime.parent),
            **(
                {"GCM_CREDENTIAL_STORE": base["GCM_CREDENTIAL_STORE"]}
                if "GCM_CREDENTIAL_STORE" in base
                else {}
            ),
        }


@pytest.fixture
def binding(tmp_path: Path) -> Binding:
    return Binding(tmp_path)


def command(binding: Binding) -> MCPCommand:
    return mcp_command(binding, {"PATH": "tools", "GCM_CREDENTIAL_STORE": "gpg"})


def test_public_environment_and_command_are_exact_and_token_free(
    binding: Binding,
) -> None:
    environment = public_environment(binding, {"PATH": "tools", "GH_TOKEN": "secret"})
    assert environment == {
        "PATH": os.pathsep.join((str(binding.launchers), "tools")),
        "GITHUB_ACCESS_BINDING": str(binding.path),
        "AGENT_STATE_DIRECTORY": str(binding.runtime.parent),
    }
    current = command(binding)
    assert current.command == str(Path(sys.executable).resolve())
    assert current.args == (
        "-B",
        str(binding.runtime / "github_access.py"),
        "mcp",
        "--binding",
        str(binding.path),
    )
    assert current.cwd == str(binding.root)
    assert current.env_vars == MCP_ENV_VARS


def test_claude_render_preserves_unrelated_settings_and_is_idempotent(
    binding: Binding,
) -> None:
    original = (
        '{\n  "permissions": {"allow": ["Read"]},\n'
        '  "env": {"PATH": "old", "OTHER": "keep"}\n}\n'
    )
    updated = render_claude(original, command(binding))
    assert '"OTHER": "keep"' in updated
    assert '"permissions"' in updated
    assert render_claude(updated, command(binding)) == updated
    with pytest.raises(SettingsError, match="string values"):
        render_claude('{"env": {"OTHER": false}}', command(binding))


def test_codex_render_preserves_comments_quoted_tables_multiline_and_other_servers(
    binding: Binding,
) -> None:
    original = '''# keep this comment
[mcp_servers."other.server"]
command = "other"
args = ["--one", "--two"]

[shell_environment_policy.set]
OTHER = "keep"
PATH = "old"

[tool."quoted.key"]
message = """line one
line two"""
'''
    updated = render_codex(original, command(binding))
    assert "# keep this comment" in updated
    assert '[mcp_servers."other.server"]' in updated
    assert 'OTHER = "keep"' in updated
    assert 'message = """line one\nline two"""' in updated
    assert "[mcp_servers.employer_github]" in updated
    assert render_codex(updated, command(binding)) == updated


def test_codex_replaces_changed_binding_and_refuses_inline_ancestor(
    binding: Binding,
) -> None:
    first = render_codex("", command(binding))
    changed = Binding(binding.root)
    changed.path = binding.root / ".agents" / "changed.json"
    second = render_codex(first, command(changed))
    document = tomllib.loads(second)
    assert document["mcp_servers"]["employer_github"]["env"][
        "GITHUB_ACCESS_BINDING"
    ] == str(changed.path)
    with pytest.raises(SettingsError, match="mixed inline"):
        render_codex(
            (
                'mcp_servers = { employer_github = { command = "old" }, '
                'other = { command = "keep" } }\n'
            ),
            command(binding),
        )
    with pytest.raises(SettingsError, match="mixed inline"):
        render_codex(
            'shell_environment_policy = { set = { PATH = "old", OTHER = "keep" } }\n',
            command(binding),
        )


@pytest.mark.parametrize(
    "original",
    [
        'model = "x"',
        "[shell_environment_policy.set]",
        '[[skills.config]]\npath = "local-skill"\nenabled = false\n',
        '[[skills.config]]\npath = "first"\n[[skills.config]]\npath = "second"',
    ],
    ids=["root-no-newline", "header-no-newline", "array-table", "repeated-array-table"],
)
def test_codex_preserves_valid_configuration_forms(
    binding: Binding, original: str
) -> None:
    before = tomllib.loads(original)
    updated = render_codex(original, command(binding))
    after = tomllib.loads(updated)
    for key, value in before.items():
        if key != "shell_environment_policy":
            assert after[key] == value
    assert render_codex(updated, command(binding)) == updated


@pytest.mark.parametrize(
    "text",
    [
        '[shell_environment_policy]\ninclude_only = ["PATH"]\n',
        '[shell_environment_policy]\nfilters = { "*" = "unknown" }\n',
    ],
)
def test_codex_rejects_filters_that_cannot_carry_public_settings(
    binding: Binding, text: str
) -> None:
    with pytest.raises(SettingsError):
        render_codex(text, command(binding))


def test_staging_reads_only_target_and_apply_checks_original_and_symlinks(
    binding: Binding, tmp_path: Path
) -> None:
    target = tmp_path / ".codex" / "config.toml"
    target.parent.mkdir()
    target.write_text('model = "x"\n', encoding="utf-8")
    change = stage_setting(NativeHost.CODEX, target, command(binding))
    assert change is not None
    target.write_text('model = "changed"\n', encoding="utf-8")
    with pytest.raises(SettingsError, match="changed while staging"):
        apply_settings([change])
    change = stage_setting(NativeHost.CODEX, target, command(binding))
    assert change is not None
    apply_settings([change])
    assert stage_setting(NativeHost.CODEX, target, command(binding)) is None
    if hasattr(os, "symlink"):
        link = tmp_path / "link"
        try:
            link.symlink_to(target.parent, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks are unavailable")
        with pytest.raises(SettingsError, match="symlink"):
            stage_setting(NativeHost.CODEX, link / "config.toml", command(binding))


def test_apply_preserves_external_edit_made_while_writing(
    binding: Binding, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".codex" / "config.toml"
    target.parent.mkdir()
    target.write_text('model = "x"\n', encoding="utf-8")
    change = stage_setting(NativeHost.CODEX, target, command(binding))
    assert change is not None
    fsync = os.fsync

    def edit_target(descriptor: int) -> None:
        fsync(descriptor)
        target.write_text('model = "external"\n', encoding="utf-8")

    monkeypatch.setattr("github_hosts.os.fsync", edit_target)
    with pytest.raises(SettingsError, match="Settings update failed"):
        apply_settings([change])
    assert target.read_text(encoding="utf-8") == 'model = "external"\n'


def test_junction_ancestor_fails_closed_without_writing_target(
    binding: Binding, tmp_path: Path
) -> None:
    if os.name != "nt":
        pytest.skip("Windows junctions require Windows")
    target = tmp_path / "isolated-second-checkout" / ".codex"
    target.mkdir(parents=True)
    settings = target / "config.toml"
    settings.write_text('model = "x"\n', encoding="utf-8")
    junction = tmp_path / "junction"
    junction_path = str(junction).replace("'", "''")
    target_path = str(target).replace("'", "''")
    junction_command = (
        f"New-Item -ItemType Junction -Path '{junction_path}' -Target '{target_path}'"
    )
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            junction_command,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    try:
        assert junction.is_junction()
        with pytest.raises(SettingsError, match="symlink"):
            stage_setting(NativeHost.CODEX, junction / "config.toml", command(binding))
        assert settings.read_text(encoding="utf-8") == 'model = "x"\n'
    finally:
        junction.rmdir()
