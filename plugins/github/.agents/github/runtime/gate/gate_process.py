"""Run the other programs the gate and `tj approve` depend on."""

import signal
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from gate_error import GateError

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ProcessError(GateError):
    pass


class Run(Protocol):
    """Run a command to completion and capture its text output."""

    def __call__(
        self, command: Sequence[str], environment: Mapping[str, str], timeout: float
    ) -> subprocess.CompletedProcess[str]: ...


class Launch(Protocol):
    """Run gh in the foreground; return its status and, when captured, its output."""

    def __call__(
        self,
        executable: Path,
        arguments: Sequence[str],
        environment: Mapping[str, str],
        capture: bool,
    ) -> tuple[int, str]: ...


def run_captured(
    command: Sequence[str], environment: Mapping[str, str], timeout: float
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            env=dict(environment),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=NO_WINDOW,
        )
    except subprocess.TimeoutExpired as error:
        label = " ".join([Path(command[0]).stem, *command[1:2]])
        raise ProcessError(f"{label} timed out after {timeout:g} seconds") from error


def launch(
    executable: Path,
    arguments: Sequence[str],
    environment: Mapping[str, str],
    capture: bool,
) -> tuple[int, str]:
    """Run gh with the caller's terminal; a captured stdout is also passed through."""
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        command = [str(executable), *arguments]
        if not capture:
            completed = subprocess.run(command, env=dict(environment), check=False)
            return completed.returncode, ""
        captured = subprocess.run(
            command,
            env=dict(environment),
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    finally:
        signal.signal(signal.SIGINT, previous)
    sys.stdout.write(captured.stdout)
    sys.stdout.flush()
    return captured.returncode, captured.stdout


def reason(result: subprocess.CompletedProcess[str]) -> str:
    """The last line a failed command printed, for a one-line report."""
    lines = [line for line in (result.stderr or result.stdout).splitlines() if line]
    return lines[-1].strip() if lines else f"exited {result.returncode}"
