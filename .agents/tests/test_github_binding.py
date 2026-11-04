import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from access_core import AccessError
from github_binding import Binding, bind
from github_platform import (
    PLATFORMS,
    Launcher,
    PlatformProtocol,
    SupportedPlatform,
    current_platform,
)
from github_tools import git_environment


@pytest.fixture
def platform() -> PlatformProtocol:
    return current_platform()


def test_current_platform_selects_the_mapped_implementation() -> None:
    assert current_platform() is PLATFORMS[SupportedPlatform(os.name)]


@pytest.fixture
def declaration() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "host": "github.com",
        "organization": "employer",
        "login": "developer",
        "verify_repository": "private-project",
        "repositories": ["private-project"],
        "permissions": {"contents": "write"},
    }


@pytest.fixture
def profile_path(tmp_path: Path, declaration: dict[str, Any]) -> Path:
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(declaration), encoding="utf-8")
    return path


@pytest.fixture
def source_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "source"
    directory.mkdir()
    (directory / "github_access.py").write_text(
        "import json, os, sys\nfrom access_core import MARKER\n"
        "target = os.environ.get('GITHUB_BINDING_CAPTURE')\n"
        "if target:\n"
        "    open(target, 'w', encoding='utf-8').write(\n"
        "        json.dumps([MARKER, sys.argv])\n"
        "    )\n",
        encoding="utf-8",
    )
    (directory / "access_core.py").write_text("MARKER = 'copied'\n", encoding="utf-8")
    return directory


def worktree(
    tmp_path: Path, origin: str = "https://github.com/employer/private-project.git"
) -> Path:
    root = tmp_path / "worktree"
    root.mkdir(parents=True)
    for arguments in (("init",), ("remote", "add", "origin", origin)):
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
    return root


def state_root(tmp_path: Path) -> Path:
    return tmp_path.parents[2] / ("binding-" + tmp_path.name[-12:])


def test_bind_writes_exact_public_selection_and_immutable_runtime(
    tmp_path: Path,
    profile_path: Path,
    source_directory: Path,
    platform: PlatformProtocol,
) -> None:
    root = worktree(tmp_path)
    binding = bind(profile_path, root, state_root(tmp_path), source_directory, platform)
    stored = json.loads(binding.path.read_text(encoding="utf-8"))
    assert binding.path == root / ".agents" / "github-access.json"
    assert stored["root"] == str(root.resolve())
    assert stored["state_root"] == str(state_root(tmp_path).resolve())
    assert stored["git"] == str(platform.real_git(os.environ))
    assert stored["profile"]["organization"] == "employer"
    assert {"token", "password", "credential"}.isdisjoint(stored["profile"])
    assert stored["profile"]["token_kind"] == "fine-grained"
    assert (
        (binding.runtime / "github_access.py")
        .read_text(encoding="utf-8")
        .startswith("import json")
    )
    assert (binding.runtime / "access_core.py").read_text(
        encoding="utf-8"
    ) == "MARKER = 'copied'\n"
    assert binding.validate(root, platform) == binding.profile
    assert platform.launcher_path(binding.launchers, Launcher.GIT).is_file()
    assert platform.launcher_path(binding.launchers, Launcher.GH).is_file()
    assert (
        bind(profile_path, root, state_root(tmp_path), source_directory, platform)
        == binding
    )


def test_binding_root_checks_use_pinned_git_instead_of_path_shim(
    tmp_path: Path,
    profile_path: Path,
    source_directory: Path,
    platform: PlatformProtocol,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = worktree(tmp_path)
    shim_directory = tmp_path / "shim"
    shim_directory.mkdir()
    shim = shim_directory / ("git.cmd" if os.name == "nt" else "git")
    if os.name == "nt":
        shim.write_text("@exit /b 1\r\n", encoding="utf-8")
    else:
        shim.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        shim.chmod(0o700)
    monkeypatch.setenv("PATH", str(shim_directory) + os.pathsep + os.environ["PATH"])
    binding = bind(profile_path, root, state_root(tmp_path), source_directory, platform)
    assert binding.validate(root, platform) == binding.profile


@pytest.mark.parametrize(
    "origin",
    [
        "git@github.com:employer/private-project.git",
        "https://github.com/other/private-project.git",
        "https://github.com/employer/other-project.git",
        "https://github.com/employer/private-project/extra",
        "employer/private-project",
    ],
)
def test_bind_rejects_non_allowlisted_origin_before_installation(
    tmp_path: Path,
    profile_path: Path,
    source_directory: Path,
    origin: str,
    platform: PlatformProtocol,
) -> None:
    root = worktree(tmp_path, origin)
    with pytest.raises(AccessError, match="binding-invalid"):
        bind(profile_path, root, state_root(tmp_path), source_directory, platform)
    assert not (root / ".agents" / "github-access.json").exists()


def test_binding_rejects_parent_sibling_and_changed_origin(
    tmp_path: Path,
    profile_path: Path,
    source_directory: Path,
    platform: PlatformProtocol,
) -> None:
    root = worktree(tmp_path)
    binding = bind(profile_path, root, state_root(tmp_path), source_directory, platform)
    with pytest.raises(AccessError, match="binding-invalid"):
        binding.validate(root.parent, platform)
    sibling = worktree(tmp_path / "sibling")
    with pytest.raises(AccessError, match="another worktree"):
        binding.validate(sibling, platform)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "remote",
            "set-url",
            "origin",
            "https://github.com/other/private-project.git",
        ],
        check=True,
    )
    with pytest.raises(AccessError, match="allowlist"):
        binding.validate(root, platform)


def test_existing_other_account_needs_explicit_retirement(
    tmp_path: Path,
    profile_path: Path,
    source_directory: Path,
    declaration: dict[str, Any],
    platform: PlatformProtocol,
) -> None:
    root = worktree(tmp_path)
    bind(profile_path, root, state_root(tmp_path), source_directory, platform)
    declaration["login"] = "another-developer"
    replacement = tmp_path / "replacement.json"
    replacement.write_text(json.dumps(declaration), encoding="utf-8")
    with pytest.raises(AccessError, match="binding-exists"):
        bind(replacement, root, state_root(tmp_path), source_directory, platform)
    assert (root / ".agents" / "github-access.json").exists()


def test_environment_removes_inherited_identity_proxy_and_python_controls(
    tmp_path: Path,
    profile_path: Path,
    source_directory: Path,
    platform: PlatformProtocol,
) -> None:
    binding = bind(
        profile_path,
        worktree(tmp_path),
        state_root(tmp_path),
        source_directory,
        platform,
    )
    environment = binding.activation_settings(
        {
            "PATH": "host-tools",
            "GH_TOKEN": "secret",
            "GITHUB_TOKEN": "secret",
            "HTTP_PROXY": "http://proxy",
            "https_proxy": "http://proxy",
            "PYTHONPATH": "attacker",
            "PYTHONSTARTUP": "attacker",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "store",
        }
    )
    assert environment["GITHUB_ACCESS_BINDING"] == str(binding.path)
    assert environment["PATH"].split(os.pathsep)[0] == str(binding.launchers)
    assert environment["AGENT_STATE_DIRECTORY"] == str(binding.state_root)
    assert set(environment) == {
        "PATH",
        "GITHUB_ACCESS_BINDING",
        "AGENT_STATE_DIRECTORY",
    }


@pytest.mark.parametrize("backend", ["gpg", "secretservice"])
def test_binding_safe_credential_backend_reaches_git_child(
    tmp_path: Path,
    profile_path: Path,
    source_directory: Path,
    platform: PlatformProtocol,
    backend: str,
) -> None:
    binding = bind(
        profile_path,
        worktree(tmp_path),
        state_root(tmp_path),
        source_directory,
        platform,
    )
    public = binding.activation_settings(
        {"PATH": "host-tools", "GCM_CREDENTIAL_STORE": backend}
    )
    child = git_environment(public, "helper", tmp_path / "home")
    assert child["GCM_CREDENTIAL_STORE"] == backend


def test_installed_launcher_invokes_runtime_entry_with_exact_binding(
    tmp_path: Path,
    profile_path: Path,
    source_directory: Path,
    platform: PlatformProtocol,
) -> None:
    binding = bind(
        profile_path,
        worktree(tmp_path),
        state_root(tmp_path),
        source_directory,
        platform,
    )
    capture = tmp_path / "argv.json"
    launcher = platform.launcher_path(binding.launchers, Launcher.GH)
    environment = {"GITHUB_BINDING_CAPTURE": str(capture), "PATH": os.environ["PATH"]}
    result = subprocess.run(
        [str(launcher), "pr", "status"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(capture.read_text(encoding="utf-8")) == [
        "copied",
        [
            str(binding.runtime / "github_access.py"),
            "gh",
            "--binding",
            str(binding.path),
            "--",
            "pr",
            "status",
        ],
    ]


def test_binding_load_rejects_tampered_public_paths(
    tmp_path: Path,
    profile_path: Path,
    source_directory: Path,
    platform: PlatformProtocol,
) -> None:
    binding = bind(
        profile_path,
        worktree(tmp_path),
        state_root(tmp_path),
        source_directory,
        platform,
    )
    document = json.loads(binding.path.read_text(encoding="utf-8"))
    document["launchers"] = str(binding.runtime / "bin")
    binding.path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(AccessError, match="binding-invalid"):
        Binding.load(binding.path, platform)


@pytest.mark.parametrize("another_account", [False, True])
def test_setup_reselects_git_while_preserving_the_existing_account(
    tmp_path: Path,
    profile_path: Path,
    source_directory: Path,
    declaration: dict[str, Any],
    platform: PlatformProtocol,
    another_account: bool,
) -> None:
    root = worktree(tmp_path)
    binding = bind(profile_path, root, state_root(tmp_path), source_directory, platform)
    previous_git = tmp_path / binding.git.name
    shutil.copyfile(binding.git, previous_git)
    document = json.loads(binding.path.read_text(encoding="utf-8"))
    document["git"] = str(previous_git)
    binding.path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(AccessError, match="selected Git executable changed"):
        Binding.load(binding.path, platform)
    if another_account:
        declaration["login"] = "another-developer"
        profile_path.write_text(json.dumps(declaration), encoding="utf-8")
        with pytest.raises(AccessError, match="binding-exists"):
            bind(profile_path, root, state_root(tmp_path), source_directory, platform)
        assert json.loads(binding.path.read_text(encoding="utf-8")) == document
    else:
        updated = bind(
            profile_path, root, state_root(tmp_path), source_directory, platform
        )
        assert updated.git == platform.real_git(os.environ)
        assert updated.validate(root, platform) == binding.profile


def test_check_requires_exact_root_and_current_runtime(
    tmp_path: Path,
    profile_path: Path,
    source_directory: Path,
    platform: PlatformProtocol,
) -> None:
    root = worktree(tmp_path)
    binding = bind(profile_path, root, state_root(tmp_path), source_directory, platform)
    binding.check(root, source_directory, platform)
    with pytest.raises(AccessError, match="exact Git worktree root"):
        binding.check(root / ".git", source_directory, platform)
    (source_directory / "access_core.py").write_text(
        "MARKER = 'changed'\n", encoding="utf-8"
    )
    with pytest.raises(AccessError, match="runtime is stale"):
        binding.check(root, source_directory, platform)
