import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from access_core import AccessError
from github_binding import Binding, bind


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
    tmp_path: Path, profile_path: Path, source_directory: Path
) -> None:
    root = worktree(tmp_path)
    binding = bind(profile_path, root, state_root(tmp_path), source_directory)
    stored = json.loads(binding.path.read_text(encoding="utf-8"))
    assert binding.path == root / ".agents" / "github-access.json"
    assert stored["root"] == str(root.resolve())
    assert stored["state_root"] == str(state_root(tmp_path).resolve())
    assert stored["profile"]["organization"] == "employer"
    assert "token" not in binding.path.read_text(encoding="utf-8").casefold()
    assert (
        (binding.runtime / "github_access.py")
        .read_text(encoding="utf-8")
        .startswith("import json")
    )
    assert (binding.runtime / "access_core.py").read_text(
        encoding="utf-8"
    ) == "MARKER = 'copied'\n"
    assert binding.validate(root) == binding.profile
    assert bind(profile_path, root, state_root(tmp_path), source_directory) == binding


@pytest.mark.parametrize(
    "origin",
    [
        "git@github.com:employer/private-project.git",
        "https://github.com/other/private-project.git",
        "https://github.com/employer/other-project.git",
        "https://github.com/employer/private-project/extra",
    ],
)
def test_bind_rejects_non_allowlisted_origin_before_installation(
    tmp_path: Path, profile_path: Path, source_directory: Path, origin: str
) -> None:
    root = worktree(tmp_path, origin)
    with pytest.raises(AccessError, match="binding-invalid"):
        bind(profile_path, root, state_root(tmp_path), source_directory)
    assert not (root / ".agents" / "github-access.json").exists()


def test_binding_rejects_parent_sibling_and_changed_origin(
    tmp_path: Path, profile_path: Path, source_directory: Path
) -> None:
    root = worktree(tmp_path)
    binding = bind(profile_path, root, state_root(tmp_path), source_directory)
    with pytest.raises(AccessError, match="binding-invalid"):
        binding.validate(root.parent)
    sibling = worktree(tmp_path / "sibling")
    with pytest.raises(AccessError, match="another worktree"):
        binding.validate(sibling)
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
        binding.validate(root)


def test_existing_other_account_needs_explicit_retirement(
    tmp_path: Path,
    profile_path: Path,
    source_directory: Path,
    declaration: dict[str, Any],
) -> None:
    root = worktree(tmp_path)
    bind(profile_path, root, state_root(tmp_path), source_directory)
    declaration["login"] = "another-developer"
    replacement = tmp_path / "replacement.json"
    replacement.write_text(json.dumps(declaration), encoding="utf-8")
    with pytest.raises(AccessError, match="binding-exists"):
        bind(replacement, root, state_root(tmp_path), source_directory)
    assert (root / ".agents" / "github-access.json").exists()


def test_environment_removes_inherited_identity_proxy_and_python_controls(
    tmp_path: Path, profile_path: Path, source_directory: Path
) -> None:
    binding = bind(
        profile_path, worktree(tmp_path), state_root(tmp_path), source_directory
    )
    environment = binding.environment(
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
    configured = [
        (
            environment[f"GIT_CONFIG_KEY_{index}"],
            environment[f"GIT_CONFIG_VALUE_{index}"],
        )
        for index in range(int(environment["GIT_CONFIG_COUNT"]))
    ]
    assert ("credential.helper", "") in configured
    assert ("http.extraHeader", "") in configured
    assert ("http.followRedirects", "false") in configured
    assert ("http.sslVerify", "true") in configured
    assert ("credential.useHttpPath", "true") in configured
    assert configured[-1][0] == "credential.https://github.com.helper"
    assert configured[-1][1].startswith("!")
    assert all(
        not key.casefold().startswith(
            ("gh_", "github_", "http_proxy", "https_proxy", "pythonpath")
        )
        or key == "GITHUB_ACCESS_BINDING"
        for key in environment
    )
    assert "GH_TOKEN" not in environment
    assert "GITHUB_TOKEN" not in environment


def test_installed_launcher_invokes_runtime_entry_with_exact_binding(
    tmp_path: Path, profile_path: Path, source_directory: Path
) -> None:
    binding = bind(
        profile_path, worktree(tmp_path), state_root(tmp_path), source_directory
    )
    capture = tmp_path / "argv.json"
    launcher = binding.launchers / ("gh.exe" if os.name == "nt" else "gh")
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
    tmp_path: Path, profile_path: Path, source_directory: Path
) -> None:
    binding = bind(
        profile_path, worktree(tmp_path), state_root(tmp_path), source_directory
    )
    document = json.loads(binding.path.read_text(encoding="utf-8"))
    document["launchers"] = str(binding.runtime / "bin")
    binding.path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(AccessError, match="binding-invalid"):
        Binding.load(binding.path)


def test_check_requires_exact_root_and_current_runtime(
    tmp_path: Path, profile_path: Path, source_directory: Path
) -> None:
    root = worktree(tmp_path)
    binding = bind(profile_path, root, state_root(tmp_path), source_directory)
    binding.check(root, source_directory)
    with pytest.raises(AccessError, match="exact Git worktree root"):
        binding.check(root / ".git", source_directory)
    (source_directory / "access_core.py").write_text(
        "MARKER = 'changed'\n", encoding="utf-8"
    )
    with pytest.raises(AccessError, match="runtime is stale"):
        binding.check(root, source_directory)
