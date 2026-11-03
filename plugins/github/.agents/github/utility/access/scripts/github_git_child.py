from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence

from access_core import AccessError, private_environment
from github_platform import current_platform


def main(arguments: Sequence[str]) -> int:
    platform = current_platform()
    environment = private_environment(os.environ)
    for key in tuple(environment):
        if key.upper().startswith(("GH_", "GITHUB_")):
            environment.pop(key)
    environment.update({"GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": ""})
    try:
        return subprocess.run(
            [str(platform.real_git()), *arguments], env=environment, check=False
        ).returncode
    except OSError as error:
        raise AccessError(
            "tool-unavailable: Git child did not complete safely"
        ) from error


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except AccessError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from None
