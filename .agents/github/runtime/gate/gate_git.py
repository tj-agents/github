import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from gate_error import GateError
from gate_program import real_program

TIMEOUT_SECONDS = 30
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
COMMAND_CONFIG = frozenset({"GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"})
COMMAND_CONFIG_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
REPOSITORY_SELECTION = frozenset({"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"})


class GitError(GateError):
    pass


def git(
    arguments: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
    stdin: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            [str(real_program("git", environment)), *arguments],
            cwd=cwd,
            env=dict(environment),
            input=stdin,
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GitError(f"git {arguments[0]} could not run") from error


def machine_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Keep configuration file locations; drop one command's injected settings."""
    return {
        key: value
        for key, value in environment.items()
        if key.upper() not in COMMAND_CONFIG
        and not key.upper().startswith(COMMAND_CONFIG_PREFIXES)
        and key.upper() not in REPOSITORY_SELECTION
    }


def git_home(environment: Mapping[str, str], cwd: Path) -> Path:
    """The directory Git expands `~` to, which can differ from Python's home."""
    result = git(
        ["config", "--file", "-", "--null", "--path", "--get", "gate.home"],
        cwd,
        machine_environment(environment),
        b"[gate]\n\thome = ~/\n",
    )
    value = result.stdout.rstrip(b"\0").decode("utf-8")
    if result.returncode or not value:
        raise GitError("Git could not report its home directory")
    return Path(value)
