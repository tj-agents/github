from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from credentials.model import Client, Credential


@dataclass(frozen=True, slots=True)
class RepositoryScope:
    organization: str
    repositories: tuple[str, ...]
    permissions: Mapping[str, str]

    @classmethod
    def from_credential(cls, credential: Credential) -> RepositoryScope:
        repositories = set()
        for endpoint in credential.endpoints:
            if endpoint.client in {Client.GIT, Client.GH, Client.MCP}:
                parts = (
                    endpoint.url.removeprefix("https://github.com/")
                    .strip("/")
                    .split("/")
                )
                repositories.add(
                    parts[1].removesuffix(".git") if len(parts) > 1 else "*"
                )
        return cls(
            credential.owner,
            ("*",) if "*" in repositories else tuple(sorted(repositories)),
            dict(credential.permissions)
            or {
                "contents": "read",
                "pull_requests": "read",
                "issues": "read",
                "actions": "read",
            },
        )

    def allows_repository(self, owner: str, name: str) -> bool:
        return (
            re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name) is not None
            and name not in {".", ".."}
            and not name.endswith(".git")
            and owner.casefold() == self.organization.casefold()
            and (
                self.repositories == ("*",)
                or name.casefold()
                in {repository.casefold() for repository in self.repositories}
            )
        )
