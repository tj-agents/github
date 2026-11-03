import platform
import shlex
import sys
from pathlib import Path

from access_core import AccessError
from github_mcp_release import LINUX_X64, ReleaseArtifact


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


def mcp_release() -> ReleaseArtifact:
    if platform.system().casefold() != "linux":
        raise AccessError("mcp-unsupported: GitHub MCP supports Windows/Linux x64 only")
    if platform.machine().lower() not in {"amd64", "x86_64", "x64"}:
        raise AccessError("mcp-unsupported: GitHub MCP supports Windows/Linux x64 only")
    return LINUX_X64
