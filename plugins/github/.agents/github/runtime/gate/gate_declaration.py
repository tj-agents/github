"""The `access` section of `.agents/repository.json`: how agents reach GitHub."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import assert_never

from gate_error import GateError
from gate_git import git
from repository_configuration import CONFIGURATION, Section, sections
from repository_configuration import committed as committed_configuration

WHERE = f"{CONFIGURATION} access"
SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?")


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
        self.fields = frozenset(("method", *fields))

    @classmethod
    def parse(cls, value: object) -> Method:
        for method in cls:
            if value == method.value:
                return method
        raise DeclarationError(f"{WHERE}: method must be human or app")


@dataclass(frozen=True, slots=True)
class HumanAccess:
    """Agents use the human's own gh and Git login, exactly as the human does."""


@dataclass(frozen=True, slots=True)
class AppAccess:
    """Agents act only as the named GitHub App on the accounts it is installed on."""

    slug: str

    def __post_init__(self) -> None:
        if not SLUG.fullmatch(self.slug):
            raise DeclarationError(f"{WHERE}: app must be a GitHub App slug")


type Access = HumanAccess | AppAccess


def access(value: object) -> Access:
    """The access an `access` section declares."""
    if not isinstance(value, dict):
        raise DeclarationError(f"{WHERE} must be a JSON object")
    method = Method.parse(value.get("method"))
    if set(value) != method.fields:
        raise DeclarationError(
            f"{WHERE}: method {method} declares exactly "
            + ", ".join(sorted(method.fields))
        )
    match method:
        case Method.HUMAN:
            return HumanAccess()
        case Method.APP:
            slug = value["app"]
            if not isinstance(slug, str):
                raise DeclarationError(f"{WHERE}: app must be a GitHub App slug")
            return AppAccess(slug)
        case _:
            assert_never(method)


def parse(text: str | bytes) -> Access | None:
    """The access a configuration document declares; None without an access section."""
    declared = sections(text)
    if Section.ACCESS not in declared:
        return None
    return access(declared[Section.ACCESS])


def committed(cwd: Path, environment: Mapping[str, str]) -> Access | None:
    """The access committed at HEAD of the repository containing `cwd`.

    None outside a repository, before the first commit, or without the file or its
    access section. An untracked or uncommitted file is ignored.
    """
    if git(["rev-parse", "--git-dir"], cwd, environment).returncode:
        return None
    text = committed_configuration(cwd, environment)
    return None if text is None else parse(text)


def declared_app(cwd: Path, environment: Mapping[str, str]) -> str | None:
    """The App slug the repository containing `cwd` commits, if it declares one."""
    declared = committed(cwd, environment)
    match declared:
        case None | HumanAccess():
            return None
        case AppAccess(slug=slug):
            return slug
        case _:
            assert_never(declared)
