from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from access_core import AccessError

if TYPE_CHECKING:
    from github_platform import Launcher


def real_git() -> Path:
    for candidate in (Path("/usr/bin/git"), Path("/usr/local/bin/git")):
        if candidate.is_file():
            return candidate.resolve()
    raise AccessError("git-unavailable: install Git")


def real_gh() -> Path:
    for candidate in (Path("/usr/bin/gh"), Path("/usr/local/bin/gh")):
        if candidate.is_file():
            return candidate.resolve()
    raise AccessError("gh-unavailable: install the official GitHub CLI")


def launcher_path(launchers: Path, launcher: Launcher) -> Path:
    return launchers / launcher.filename


def launcher_contents(script: Path, binding: Path, launcher: Launcher) -> bytes:
    python = Path(sys.executable).resolve()
    if not python.is_file():
        raise AccessError(
            "binding-invalid: the active Python executable is unavailable"
        )
    command = f"exec {shlex.quote(str(python))} -B {shlex.quote(str(script))}"
    command += f' {launcher.operation} --binding {shlex.quote(str(binding))} -- "$@"\n'
    return ("#!/bin/sh\n" + command).encode()


def write_launcher(path: Path, contents: bytes) -> None:
    path.write_bytes(contents)
    path.chmod(0o755)
