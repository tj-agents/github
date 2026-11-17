from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from gate_error import GateError
from gate_program import execute as execute_program
from gate_program import real_program

from credentials.model import CredentialError


def real_tool(name: str, environment: Mapping[str, str]) -> Path:
    try:
        return real_program(name, environment)
    except GateError as error:
        raise CredentialError(str(error)) from None


def execute(
    executable: Path,
    arguments: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
) -> int:
    try:
        return execute_program(executable, arguments, cwd, environment)
    except GateError as error:
        raise CredentialError(str(error)) from None
