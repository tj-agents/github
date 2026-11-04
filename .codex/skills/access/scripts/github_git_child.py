from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from access_core import AccessError, private_environment


def main(executable: Path, arguments: Sequence[str]) -> int:
    if not executable.is_absolute() or not executable.is_file():
        raise AccessError("launcher-invalid: pass the real absolute Git executable")
    environment = private_environment(os.environ)
    for key in tuple(environment):
        if key.upper().startswith(("GH_", "GITHUB_")):
            environment.pop(key)
    environment.update({"GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": ""})
    try:
        return subprocess.run(
            [str(executable), *arguments], env=environment, check=False
        ).returncode
    except OSError as error:
        raise AccessError(
            "tool-unavailable: Git child did not complete safely"
        ) from error


if __name__ == "__main__":
    try:
        if len(sys.argv) < 2:
            raise AccessError("launcher-invalid: pass the real absolute Git executable")
        raise SystemExit(main(Path(sys.argv[1]), sys.argv[2:]))
    except AccessError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from None
