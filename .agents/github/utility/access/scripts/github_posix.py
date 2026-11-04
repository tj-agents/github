from __future__ import annotations

import platform
import shlex
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from access_core import AccessError
from github_mcp_release import LINUX_X64, ReleaseArtifact

if TYPE_CHECKING:
    from github_platform import Launcher


def real_gh() -> Path:
    for candidate in (Path("/usr/bin/gh"), Path("/usr/local/bin/gh")):
        if candidate.is_file():
            return candidate.resolve()
    raise AccessError("gh-unavailable: install the official GitHub CLI")


def real_git(environment: Mapping[str, str]) -> Path:
    for candidate in (Path("/usr/bin/git"), Path("/usr/local/bin/git")):
        if candidate.is_file():
            return candidate.resolve()
    raise AccessError("git-unavailable: install Git")


def launcher_path(launchers: Path, launcher: Launcher) -> Path:
    return launchers / launcher.filename


def command_launcher_contents(script: Path, arguments: Sequence[str]) -> bytes:
    python = Path(sys.executable).resolve()
    if not python.is_file():
        raise AccessError(
            "binding-invalid: the active Python executable is unavailable"
        )
    command = " ".join(
        (
            "exec",
            shlex.quote(str(python)),
            "-B",
            shlex.quote(str(script)),
            *(shlex.quote(argument) for argument in arguments),
            '"$@"',
        )
    )
    return ("#!/bin/sh\n" + command).encode()


def launcher_contents(script: Path, binding: Path, launcher: Launcher) -> bytes:
    return command_launcher_contents(
        script, (launcher.operation, "--binding", str(binding), "--")
    )


def write_launcher(path: Path, contents: bytes) -> None:
    path.write_bytes(contents)
    path.chmod(0o755)


def mcp_release() -> ReleaseArtifact:
    if platform.system().casefold() != "linux":
        raise AccessError("mcp-unsupported: GitHub MCP supports Windows/Linux x64 only")
    if platform.machine().lower() not in {"amd64", "x86_64", "x64"}:
        raise AccessError("mcp-unsupported: GitHub MCP supports Windows/Linux x64 only")
    return LINUX_X64
