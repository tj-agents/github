from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from credentials import http, model, providers, selected
from credentials.model import Client, Credential, CredentialError, Declaration
from credentials.service import Service
from credentials.vault import GCMVault, State


@dataclass(frozen=True, slots=True)
class Context:
    cwd: Path
    environment: Mapping[str, str]
    state_root: Path
    windows: bool
    declaration: Declaration | None

    @property
    def home(self) -> Path:
        return Path(
            self.environment.get("USERPROFILE")
            or self.environment.get("HOME")
            or self.state_root.parent
        )

    @classmethod
    def load(
        cls,
        cwd: Path,
        environment: Mapping[str, str],
        state_root: Path,
        *,
        windows: bool,
    ) -> Context:
        declaration = model.committed(cwd, environment)
        if declaration is None:
            declaration = selected.selected(cwd, environment)
        if declaration is not None:
            providers.validate(declaration)
        return cls(cwd, environment, state_root, windows, declaration)

    def service(self, credential: Credential) -> Service:
        return Service(
            credential,
            State(self.state_root, credential),
            GCMVault(credential, self.environment, windows=self.windows),
            providers.PROVIDERS[credential.provider],
            partial(http.request, proxies=http.proxy_configuration(self.environment)),
        )

    def named(self, name: str) -> Credential:
        if self.declaration is None:
            raise CredentialError(
                "credentials-undeclared: declare credentials in .agents/repository.json"
            )
        return self.declaration.named(name)

    def resolve(self, client: Client, url: str) -> Service | None:
        if self.declaration is None:
            return None
        credential = self.declaration.route(client, url)
        return self.service(credential) if credential is not None else None
