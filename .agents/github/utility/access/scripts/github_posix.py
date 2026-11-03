import shlex
import sys
from pathlib import Path

from access_core import AccessError


def real_gh() -> Path:
    for candidate in (Path("/usr/bin/gh"), Path("/usr/local/bin/gh")):
        if candidate.is_file():
            return candidate.resolve()
    raise AccessError("gh-unavailable: install the official GitHub CLI")


def launcher_path(launchers: Path) -> Path:
    return launchers / "gh"


def launcher_contents(script: Path, binding: Path) -> bytes:
    python = Path(sys.executable).resolve()
    if not python.is_file():
        raise AccessError(
            "binding-invalid: the active Python executable is unavailable"
        )
    command = f"exec {shlex.quote(str(python))} -B {shlex.quote(str(script))}"
    command += f' gh --binding {shlex.quote(str(binding))} -- "$@"\n'
    return ("#!/bin/sh\n" + command).encode()


def write_launcher(path: Path, contents: bytes) -> None:
    path.write_bytes(contents)
    path.chmod(0o755)
