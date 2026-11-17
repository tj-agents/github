"""Keep gh's local Git inspections from inheriting its credential or fetching."""

import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from credentials.model import CredentialError
from credentials.vault import private_environment


def execute(executable: Path, arguments: Sequence[str], source: dict[str, str]) -> int:
    if not executable.is_absolute() or not executable.is_file():
        raise CredentialError(
            "credential-client-invalid: select the real Git executable"
        )
    environment = private_environment(source)
    for key in tuple(environment):
        if key.upper().startswith(("GH_", "GITHUB_")):
            environment.pop(key)
    environment.update(GIT_NO_LAZY_FETCH="1", GIT_ALLOW_PROTOCOL="")
    try:
        return subprocess.run(
            [str(executable), *arguments], env=environment, check=False
        ).returncode
    except OSError:
        raise CredentialError(
            "credential-client-unavailable: Git inspection failed"
        ) from None


def main() -> int:
    try:
        if len(sys.argv) < 2:
            raise CredentialError(
                "credential-client-invalid: select the real Git executable"
            )
        return execute(Path(sys.argv[1]), sys.argv[2:], dict(os.environ))
    except CredentialError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
