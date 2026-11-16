"""The committed `.agents/github-access.json` that selects how agents reach GitHub."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import assert_never

from gate_error import GateError
from gate_git import git

DECLARATION = ".agents/github-access.json"
SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?")
COMMON_FIELDS = ("schema_version", "method")


class DeclarationError(GateError):
    pass


class Method(StrEnum):
    HUMAN = ("human", ())
    APP = ("app", ("app",))

    def __new__(cls, value: str, fields: tuple[str, ...]) -> Method:
        member = str.__new__(cls, value)
        member._value_ = value
        return member

    def __init__(self, value: str, fields: tuple[str, ...]) -> None:
        self.fields = frozenset((*COMMON_FIELDS, *fields))

    @classmethod
    def parse(cls, value: object) -> Method:
        for method in cls:
            if value == method.value:
                return method
        raise DeclarationError(f"{DECLARATION}: method must be human or app")


@dataclass(frozen=True, slots=True)
class HumanAccess:
    """Agents use the human's own gh and Git login, exactly as the human does."""


@dataclass(frozen=True, slots=True)
class AppAccess:
    """Agents act only as the named GitHub App on the accounts it is installed on."""

    slug: str

    def __post_init__(self) -> None:
        if not SLUG.fullmatch(self.slug):
            raise DeclarationError(f"{DECLARATION}: app must be a GitHub App slug")


type Access = HumanAccess | AppAccess


def parse(text: str | bytes) -> Access:
    try:
        data = json.loads(text)
    except ValueError as error:
        raise DeclarationError(f"{DECLARATION} is not valid JSON") from error
    if not isinstance(data, dict):
        raise DeclarationError(f"{DECLARATION} must be a JSON object")
    version = data.get("schema_version")
    if type(version) is not int or version != 1:
        raise DeclarationError(f"{DECLARATION} needs schema_version 1")
    method = Method.parse(data.get("method"))
    if set(data) != method.fields:
        raise DeclarationError(
            f"{DECLARATION}: method {method} declares exactly "
            + ", ".join(sorted(method.fields))
        )
    match method:
        case Method.HUMAN:
            return HumanAccess()
        case Method.APP:
            slug = data["app"]
            if not isinstance(slug, str):
                raise DeclarationError(f"{DECLARATION}: app must be a GitHub App slug")
            return AppAccess(slug)
        case _:
            assert_never(method)


def committed(cwd: Path, environment: Mapping[str, str]) -> Access:
    """Read the declaration committed at HEAD of the repository containing `cwd`.

    Outside a repository, without a commit, or without the file, access is human.
    An untracked or uncommitted file is ignored.
    """
    if git(["rev-parse", "--git-dir"], cwd, environment).returncode:
        return HumanAccess()
    result = git(
        ["cat-file", "--batch"], cwd, environment, f"HEAD:{DECLARATION}\n".encode()
    )
    header, _, content = result.stdout.partition(b"\n")
    if result.returncode or not header:
        raise DeclarationError(f"Git could not read the committed {DECLARATION}")
    if header.endswith(b" missing"):
        return HumanAccess()
    fields = header.split(b" ")
    if len(fields) != 3 or fields[1] != b"blob" or not fields[2].isdigit():
        raise DeclarationError(f"the committed {DECLARATION} is not a file")
    return parse(content[: int(fields[2])])


def declared_app(cwd: Path, environment: Mapping[str, str]) -> str | None:
    """The App slug the repository containing `cwd` commits, if it declares one."""
    access = committed(cwd, environment)
    match access:
        case HumanAccess():
            return None
        case AppAccess(slug=slug):
            return slug
        case _:
            assert_never(access)
