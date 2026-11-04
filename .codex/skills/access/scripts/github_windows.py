from __future__ import annotations

import io
import os
import platform
import sys
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from access_core import AccessError

if TYPE_CHECKING:
    from github_platform import Launcher


def real_gh() -> Path:
    candidate = (
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "GitHub CLI/gh.exe"
    )
    if candidate.is_file():
        return candidate.resolve()
    raise AccessError("gh-unavailable: install the official GitHub CLI")


def _environment_value(environment: Mapping[str, str], key: str) -> str | None:
    return next(
        (
            value
            for name, value in environment.items()
            if name.casefold() == key.casefold()
        ),
        None,
    )


def _generated_launchers(environment: Mapping[str, str]) -> tuple[Path, ...]:
    launchers: list[Path] = []
    state = _environment_value(environment, "AGENT_STATE_DIRECTORY")
    if state:
        launchers.append(Path(state) / "github-access" / "bin")
    config = _environment_value(environment, "GH_CONFIG_DIR")
    if config:
        launchers.append(Path(config) / "git-child")
    return tuple(launchers)


def _is_generated_launcher(candidate: Path, launchers: Sequence[Path]) -> bool:
    resolved = candidate.resolve(strict=False)
    for launcher in launchers:
        try:
            resolved.relative_to(launcher.resolve(strict=False))
        except ValueError:
            continue
        return True
    return False


def real_git(environment: Mapping[str, str]) -> Path:
    launchers = _generated_launchers(environment)
    candidates = [
        Path(directory) / "git.exe"
        for directory in (_environment_value(environment, "PATH") or "").split(
            os.pathsep
        )
        if directory
    ]
    program_files = _environment_value(environment, "PROGRAMFILES")
    if program_files:
        root = Path(program_files) / "Git"
        candidates.extend((root / "cmd/git.exe", root / "bin/git.exe"))
    local_app_data = _environment_value(environment, "LOCALAPPDATA")
    if local_app_data:
        root = Path(local_app_data) / "Programs" / "Git"
        candidates.extend((root / "cmd/git.exe", root / "bin/git.exe"))
    for candidate in candidates:
        if candidate.is_file() and not _is_generated_launcher(candidate, launchers):
            return candidate.resolve()
    raise AccessError("git-unavailable: install Git for Windows")


def launcher_path(launchers: Path, launcher: Launcher) -> Path:
    return launchers / f"{launcher.filename}.exe"


def _launcher_stub() -> bytes:
    import ensurepip

    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64", "x64"}:
        name = "t64.exe"
    elif machine in {"arm64", "aarch64"}:
        name = "t64-arm.exe"
    else:
        raise AccessError(
            "binding-invalid: unsupported Windows architecture for gh.exe"
        )
    wheels = sorted((Path(ensurepip.__file__).parent / "_bundled").glob("pip-*.whl"))
    if not wheels:
        raise AccessError("binding-invalid: this Python has no bundled launcher stub")
    with zipfile.ZipFile(wheels[-1]) as wheel:
        try:
            return wheel.read(f"pip/_vendor/distlib/{name}")
        except KeyError as error:
            raise AccessError(
                "binding-invalid: this Python lacks the required launcher stub"
            ) from error


def command_launcher_contents(script: Path, arguments: Sequence[str]) -> bytes:
    python = Path(sys.executable).resolve()
    if not python.is_file():
        raise AccessError(
            "binding-invalid: the active Python executable is unavailable"
        )
    command = [str(script), *arguments]
    main = "import runpy, sys\n"
    main += f"sys.path.insert(0, {str(script.parent)!r})\n"
    main += f"sys.argv = {command!r} + sys.argv[1:]\n"
    main += f"runpy.run_path({str(script)!r}, run_name='__main__')\n"
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as payload:
        payload.writestr(zipfile.ZipInfo("__main__.py", (1980, 1, 1, 0, 0, 0)), main)
    return _launcher_stub() + f'#!"{python}" -B\n'.encode() + archive.getvalue()


def launcher_contents(script: Path, binding: Path, launcher: Launcher) -> bytes:
    return command_launcher_contents(
        script, (launcher.operation, "--binding", str(binding), "--")
    )


def write_launcher(path: Path, contents: bytes) -> None:
    path.write_bytes(contents)
