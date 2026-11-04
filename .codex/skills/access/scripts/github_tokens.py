from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from access_core import Profile


class TokenKind(StrEnum):
    FINE_GRAINED = (
        "fine-grained",
        r"github_pat_[A-Za-z0-9_]{40,245}",
        "Fine-grained token",
    )
    CLASSIC = ("classic", r"ghp_[A-Za-z0-9]{36}", "Classic token")

    def __new__(cls, value: str, pattern: str, label: str) -> TokenKind:
        member = str.__new__(cls, value)
        member._value_ = value
        return member

    def __init__(self, value: str, pattern: str, label: str) -> None:
        self.pattern = pattern
        self.label = label

    @classmethod
    def parse(cls, value: object) -> TokenKind:
        for kind in cls:
            if value == kind.value:
                return kind
        raise ValueError("choose fine-grained or classic")


class TokenForm(Protocol):
    kind: TokenKind

    def token_url(self, profile: Profile) -> str: ...

    def instructions(self, profile: Profile) -> tuple[str, str]: ...
