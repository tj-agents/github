from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from access_core import AccessError, Profile, private_environment
from github_platform import Launcher, PlatformProtocol
from github_tools import parse_https_repository


def _public_profile(profile: Profile) -> dict[str, object]:
    return {
        "schema_version": 1,
        "host": "github.com",
        "organization": profile.organization,
        "login": profile.login,
        "verify_repository": profile.verify_repository,
        "repositories": list(profile.repositories),
        "permissions": dict(profile.permissions),
        "expires_in": profile.expires_in,
    }


def _secure_environment(source: Mapping[str, str]) -> dict[str, str]:
    environment = private_environment(source)
    return {
        key: value
        for key, value in environment.items()
        if not key.upper().startswith(
            (
                "GH_",
                "GITHUB_",
                "GIT_CONFIG_",
                "GIT_TRACE",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "NO_PROXY",
                "PYTHONPATH",
                "PYTHONSTARTUP",
            )
        )
        and key.upper()
        not in {
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "PYTHONPATH",
            "PYTHONSTARTUP",
        }
    }


def _git_environment(source: Mapping[str, str]) -> dict[str, str]:
    environment = _secure_environment(source)
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_COUNT": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def _git(
    executable: Path,
    root: Path,
    *arguments: str,
    environment: Mapping[str, str] | None = None,
) -> str:
    if not executable.is_absolute() or not executable.is_file():
        raise AccessError("binding-invalid: the selected Git executable is unavailable")
    try:
        result = subprocess.run(
            [str(executable), "-C", str(root), *arguments],
            text=True,
            capture_output=True,
            env=_git_environment(os.environ) if environment is None else environment,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AccessError(
            "binding-invalid: Git is unavailable for root validation"
        ) from error
    if result.returncode:
        raise AccessError("binding-invalid: select an existing Git worktree")
    return result.stdout.removesuffix("\n")


def _worktree_root(
    executable: Path, root: Path, environment: Mapping[str, str] | None = None
) -> Path:
    candidate = root.expanduser().resolve(strict=False)
    top_level = _git(
        executable,
        candidate,
        "rev-parse",
        "--show-toplevel",
        environment=environment,
    )
    if not top_level:
        raise AccessError("binding-invalid: select an existing Git worktree")
    try:
        return Path(top_level).resolve(strict=True)
    except OSError as error:
        raise AccessError(
            "binding-invalid: Git reported an unavailable worktree"
        ) from error


def _origin(profile: Profile, executable: Path, root: Path) -> None:
    fetch = _git(executable, root, "remote", "get-url", "--all", "origin").splitlines()
    push = _git(
        executable, root, "remote", "get-url", "--push", "--all", "origin"
    ).splitlines()
    if len(fetch) != 1 or len(push) != 1 or fetch[0] != push[0]:
        raise AccessError(
            "binding-invalid: origin must have one matching fetch and push URL"
        )
    try:
        parse_https_repository(profile, fetch[0])
    except AccessError as error:
        raise AccessError(
            "binding-invalid: origin must be an allowlisted HTTPS github.com repository"
        ) from error


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _source_files(directory: Path) -> list[Path]:
    files = sorted(path for path in directory.glob("*.py") if path.is_file())
    if not files or not any(path.name == "github_access.py" for path in files):
        raise AccessError("binding-invalid: source directory lacks github_access.py")
    return files


def _runtime_version(directory: Path) -> str:
    digest = hashlib.sha256()
    for source in _source_files(directory):
        digest.update(source.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(source.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _runtime_directory(state_root: Path, source_directory: Path) -> Path:
    version = _runtime_version(source_directory)
    base = state_root.expanduser().resolve(strict=False) / "github-access" / "runtime"
    destination = base / version
    if destination.exists():
        if not destination.is_dir() or any(
            destination.joinpath(source.name).read_bytes() != source.read_bytes()
            for source in _source_files(source_directory)
        ):
            raise AccessError(
                "binding-invalid: existing runtime payload does not match its version"
            )
        return destination
    base.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="runtime-", dir=base))
    try:
        for source in _source_files(source_directory):
            shutil.copyfile(source, temporary / source.name)
        try:
            temporary.rename(destination)
        except FileExistsError as error:
            if not destination.is_dir() or any(
                destination.joinpath(source.name).read_bytes() != source.read_bytes()
                for source in _source_files(source_directory)
            ):
                raise AccessError(
                    "binding-invalid: concurrent runtime payload differs"
                ) from error
        return destination
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _launcher_directory(runtime: Path, binding: Path) -> Path:
    key = hashlib.sha256(f"{binding}\0{runtime.name}".encode()).hexdigest()
    return runtime.parent.parent / "bin" / key


def _install_launcher(runtime: Path, binding: Path, platform: PlatformProtocol) -> Path:
    launchers = _launcher_directory(runtime, binding)
    launchers.mkdir(parents=True, mode=0o700, exist_ok=True)
    script = runtime / "github_access.py"
    for identity in Launcher:
        launcher = platform.launcher_path(launchers, identity)
        expected = platform.launcher_contents(script, binding, identity)
        if launcher.exists() and launcher.read_bytes() != expected:
            raise AccessError(
                "binding-invalid: existing launcher belongs to another root"
            )
        if not launcher.exists():
            platform.write_launcher(launcher, expected)
    return launchers


@dataclass(frozen=True, slots=True)
class Binding:
    path: Path
    root: Path
    profile: Profile
    state_root: Path
    runtime: Path
    launchers: Path
    git: Path

    @classmethod
    def load(cls, path: Path, platform: PlatformProtocol) -> Binding:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise AccessError(
                "binding-invalid: read the public GitHub access selection"
            ) from error
        fields = {
            "schema_version",
            "root",
            "profile",
            "state_root",
            "runtime",
            "launchers",
            "git",
        }
        if (
            not isinstance(data, dict)
            or set(data) != fields
            or data.get("schema_version") != 1
        ):
            raise AccessError("binding-invalid: invalid public GitHub access selection")
        root_value, state_root_value, runtime_value, launchers_value, git_value = (
            data.get("root"),
            data.get("state_root"),
            data.get("runtime"),
            data.get("launchers"),
            data.get("git"),
        )
        if any(
            not isinstance(value, str) or not value
            for value in (
                root_value,
                state_root_value,
                runtime_value,
                launchers_value,
                git_value,
            )
        ):
            raise AccessError("binding-invalid: invalid public GitHub access paths")
        assert isinstance(root_value, str)
        assert isinstance(state_root_value, str)
        assert isinstance(runtime_value, str)
        assert isinstance(launchers_value, str)
        assert isinstance(git_value, str)
        try:
            root, state_root, runtime, launchers, git = (
                Path(value).resolve(strict=True)
                for value in (
                    root_value,
                    state_root_value,
                    runtime_value,
                    launchers_value,
                    git_value,
                )
            )
        except OSError as error:
            raise AccessError(
                "binding-invalid: public GitHub access paths are unavailable"
            ) from error
        profile = Profile.from_data(data.get("profile"))
        binding = cls(
            path.resolve(strict=True),
            root,
            profile,
            state_root,
            runtime,
            launchers,
            git,
        )
        expected_launchers = _launcher_directory(binding.runtime, binding.path)
        if (
            binding.path.parent != binding.root / ".agents"
            or binding.launchers != expected_launchers
        ):
            raise AccessError(
                "binding-invalid: selection paths do not match the exact root"
            )
        if binding.runtime.parent != binding.state_root / "github-access" / "runtime":
            raise AccessError(
                "binding-invalid: runtime is outside the selected state root"
            )
        if binding.runtime.name != _runtime_version(binding.runtime):
            raise AccessError("binding-invalid: selected runtime is incomplete")
        if binding.git != platform.real_git(os.environ):
            raise AccessError("binding-invalid: selected Git executable changed")
        for identity in Launcher:
            launcher = platform.launcher_path(binding.launchers, identity)
            if not launcher.is_file():
                raise AccessError("binding-invalid: selected launcher is unavailable")
        return binding

    def validate(self, cwd: Path, platform: PlatformProtocol) -> Profile:
        if _worktree_root(self.git, cwd) != self.root:
            raise AccessError(
                "binding-invalid: GitHub employer access is bound to another worktree"
            )
        _origin(self.profile, self.git, self.root)
        current = Binding.load(self.path, platform)
        if current != self:
            raise AccessError(
                "binding-invalid: GitHub employer access selection changed"
            )
        return self.profile

    def validate_execution(
        self,
        cwd: Path,
        platform: PlatformProtocol,
        environment: Mapping[str, str],
    ) -> Profile:
        profile = self.validate(cwd, platform)
        if _worktree_root(self.git, cwd, environment) != self.root:
            raise AccessError(
                "binding-invalid: GitHub employer access is bound to another worktree"
            )
        return profile

    def activation_settings(self, base: Mapping[str, str]) -> dict[str, str]:
        path = next((value for key, value in base.items() if key.upper() == "PATH"), "")
        environment = {
            "PATH": str(self.launchers) + (os.pathsep + path if path else ""),
            "GITHUB_ACCESS_BINDING": str(self.path),
            "AGENT_STATE_DIRECTORY": str(self.state_root),
        }
        if base.get("GCM_CREDENTIAL_STORE") in {"gpg", "secretservice"}:
            environment["GCM_CREDENTIAL_STORE"] = base["GCM_CREDENTIAL_STORE"]
        return environment

    def check(
        self, root: Path, source_directory: Path, platform: PlatformProtocol
    ) -> None:
        if root.expanduser().resolve(strict=False) != self.root:
            raise AccessError(
                "binding-invalid: setup requires the exact Git worktree root"
            )
        self.validate(root, platform)
        expected = (
            self.state_root
            / "github-access"
            / "runtime"
            / _runtime_version(source_directory)
        )
        if self.runtime != expected:
            raise AccessError("binding-invalid: selected runtime is stale; run setup")
        script = self.runtime / "github_access.py"
        for identity in Launcher:
            expected_launcher = platform.launcher_contents(script, self.path, identity)
            launcher = platform.launcher_path(self.launchers, identity)
            if launcher.read_bytes() != expected_launcher:
                raise AccessError(
                    "binding-invalid: selected launcher is stale; run setup"
                )


def bind(
    profile_path: Path,
    root: Path,
    state_root: Path,
    source_directory: Path,
    platform: PlatformProtocol,
) -> Binding:
    profile = Profile.load(profile_path)
    git = platform.real_git(os.environ)
    requested_root = root.expanduser().resolve(strict=False)
    exact_root = _worktree_root(git, root)
    if requested_root != exact_root:
        raise AccessError("binding-invalid: setup requires the exact Git worktree root")
    _origin(profile, git, exact_root)
    selection = exact_root / ".agents" / "github-access.json"
    if selection.exists():
        existing = Binding.load(selection, platform)
        if (
            existing.profile.organization.casefold() != profile.organization.casefold()
            or existing.profile.login.casefold() != profile.login.casefold()
        ):
            raise AccessError(
                "binding-exists: explicit retirement is required for another "
                "employer account"
            )
    runtime = _runtime_directory(state_root, source_directory)
    launchers = _install_launcher(runtime, selection, platform)
    _atomic_json(
        selection,
        {
            "schema_version": 1,
            "root": str(exact_root),
            "profile": _public_profile(profile),
            "state_root": str(state_root.expanduser().resolve(strict=False)),
            "runtime": str(runtime),
            "launchers": str(launchers),
            "git": str(git),
        },
    )
    return Binding.load(selection, platform)
