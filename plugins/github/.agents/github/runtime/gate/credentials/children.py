from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from gate_launcher import is_gate_launcher

from credentials.model import CredentialError


def real_tool(name: str, environment: Mapping[str, str]) -> Path:
    suffixes = (".exe", ".cmd", ".bat", "") if os.name == "nt" else ("",)
    for entry in environment.get("PATH", "").split(os.pathsep):
        directory = Path(entry)
        if not entry or not directory.is_absolute():
            continue
        for suffix in suffixes:
            candidate = directory / (name + suffix)
            if (
                candidate.is_file()
                and os.access(candidate, os.X_OK)
                and not is_gate_launcher(candidate)
            ):
                return candidate.resolve()
    raise CredentialError(f"tool-missing: install {name} and rerun tj install")


def execute(
    executable: Path,
    arguments: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
) -> int:
    try:
        return subprocess.run(
            [str(executable), *arguments], cwd=cwd, env=dict(environment), check=False
        ).returncode
    except OSError:
        raise CredentialError(
            "credential-client-unavailable: the selected client could not start"
        ) from None
