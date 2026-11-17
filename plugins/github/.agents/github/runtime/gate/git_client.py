"""Give declared agent Git requests a path without changing human Git settings."""

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from gate_agent import agent_host
from gate_error import GateError
from gate_program import execute, real_program


def split_global_options(arguments: Sequence[str]) -> tuple[list[str], list[str]]:
    prefix: list[str] = []
    end = 0
    while end < len(arguments) and arguments[end].startswith("-"):
        option = arguments[end]
        end += 1
        if option.startswith("-C") and option != "-C":
            prefix.extend(("-C", option[2:]))
        else:
            prefix.append(option)
            if option in {"-C", "-c", "--git-dir", "--work-tree"} and end < len(
                arguments
            ):
                prefix.append(arguments[end])
                end += 1
    return prefix, list(arguments[end:])


def effective_directory(
    executable: Path,
    prefix: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
) -> Path | None:
    try:
        result = subprocess.run(
            [str(executable), *prefix, "rev-parse", "--show-toplevel"],
            cwd=cwd,
            env=dict(environment),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=30,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GateError(
            "credential-client-unavailable: Git target lookup failed"
        ) from error
    if result.returncode:
        return None
    target = Path(result.stdout.strip())
    return target if target.is_absolute() else None


def main() -> int:
    environment = dict(os.environ)
    arguments = sys.argv[1:]
    cwd = Path.cwd()
    try:
        executable = real_program("git", environment)
        if agent_host(environment) is None or arguments[:1] in (
            ["--version"],
            ["--help"],
            ["help"],
        ):
            return execute(executable, arguments, cwd, environment)
        from credentials.context import Context
        from credentials.model import Client

        home = Path(environment.get("USERPROFILE") or environment.get("HOME") or "~")
        state = Path(
            environment.get("AGENT_STATE_DIRECTORY", str(home / ".agents-state"))
        )
        prefix, command = split_global_options(arguments)
        arguments = [*prefix, *command]
        target = effective_directory(executable, prefix, cwd, environment)
        if target is None:
            return execute(executable, arguments, cwd, environment)
        context = Context.load(target, environment, state, windows=os.name == "nt")
        if context.declaration is not None and context.declaration.supports(Client.GIT):
            arguments = [
                "-c",
                "credential.https://github.com.useHttpPath=true",
                *arguments,
            ]
        return execute(executable, arguments, cwd, environment)
    except GateError as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
