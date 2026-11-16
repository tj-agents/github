import os
import shlex
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path

from gate_platform import MARKER, PrivateStateError


def default_bin_directory(environment: Mapping[str, str], home: Path) -> Path:
    return home / ".local" / "bin"


def launcher_name() -> str:
    return "gh"


def launcher_bytes(python: Path, script: Path) -> bytes:
    command = " ".join(
        (
            "exec",
            shlex.quote(python.as_posix()),
            "-B",
            shlex.quote(script.as_posix()),
            "gh",
            '"$@"',
        )
    )
    return f"#!/bin/sh\n# {MARKER}\n{command}\n".encode()


def write_launcher(path: Path, content: bytes) -> None:
    path.write_bytes(content)
    path.chmod(0o755)


def protect(path: Path, *, directory: bool) -> None:
    path.chmod(0o700 if directory else 0o600)


def verify_private(path: Path, *, directory: bool) -> None:
    expected = 0o700 if directory else 0o600
    if stat.S_IMODE(path.stat().st_mode) != expected:
        raise PrivateStateError(f"{path} is readable by other users; run onboarding")


def execute(
    executable: Path,
    arguments: Sequence[str],
    environment: Mapping[str, str] | None,
) -> int:
    command = [str(executable), *arguments]
    if environment is None:
        os.execv(executable, command)
    os.execve(executable, command, dict(environment))
