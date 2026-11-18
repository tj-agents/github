"""The committed `.agents/repository.json` and its independently resolved sections.

One file carries a repository's `access`, `merge` and `credentials` sections. Each
reader resolves only its own section: a document without that section leaves it to
the reader's next source, such as the owner default in `<owner>/.github`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path

from gate_error import GateError
from gate_git import git

CONFIGURATION = ".agents/repository.json"
MAX_BYTES = 128 * 1024


class Section(StrEnum):
    ACCESS = "access"
    MERGE = "merge"
    CREDENTIALS = "credentials"


class ConfigurationError(GateError):
    pass


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError(f"{CONFIGURATION} repeats the property {key}")
        result[key] = value
    return result


def sections(text: str | bytes) -> dict[str, object]:
    """The sections a configuration document declares, keyed by section name."""
    try:
        data = json.loads(text, object_pairs_hook=_unique)
    except ValueError as error:
        raise ConfigurationError(f"{CONFIGURATION} is not valid JSON") from error
    if not isinstance(data, dict):
        raise ConfigurationError(f"{CONFIGURATION} must be a JSON object")
    version = data.pop("schema_version", None)
    if type(version) is not int or version != 1:
        raise ConfigurationError(f"{CONFIGURATION} needs schema_version 1")
    unknown = sorted(set(data) - set(Section))
    if unknown:
        raise ConfigurationError(
            f"{CONFIGURATION} has unknown keys: {', '.join(unknown)}"
        )
    return data


def committed(
    cwd: Path, environment: Mapping[str, str], revision: str = "HEAD"
) -> bytes | None:
    """The configuration committed at `revision` of the repository containing `cwd`.

    None when that commit has no such file. An untracked or uncommitted file is ignored.
    """
    result = git(
        ["cat-file", "--batch"],
        cwd,
        environment,
        f"{revision}:{CONFIGURATION}\n".encode(),
    )
    header, _, content = result.stdout.partition(b"\n")
    if result.returncode or not header:
        raise ConfigurationError(f"Git could not read the committed {CONFIGURATION}")
    if header.endswith(b" missing"):
        return None
    fields = header.split(b" ")
    if (
        len(fields) != 3
        or fields[1] != b"blob"
        or not fields[2].isdigit()
        or int(fields[2]) > MAX_BYTES
    ):
        raise ConfigurationError(
            f"the committed {CONFIGURATION} must be a small JSON file"
        )
    return content[: int(fields[2])]
