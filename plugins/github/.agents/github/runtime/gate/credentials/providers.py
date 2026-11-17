from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from credentials import azure_devops, github_classic, github_fine_grained, http
from credentials.model import Credential, Declaration, Provider
from credentials.vault import Secret


class CredentialProvider(Protocol):
    def validate(self, credential: Credential) -> None: ...
    def creation_url(self, credential: Credential) -> str: ...
    def instructions(self, credential: Credential) -> str: ...
    def verify(
        self, credential: Credential, secret: Secret, transport: http.Transport
    ) -> datetime | None: ...


PROVIDERS: Mapping[Provider, CredentialProvider] = {
    Provider.GITHUB_CLASSIC: github_classic,
    Provider.GITHUB_FINE_GRAINED: github_fine_grained,
    Provider.AZURE_DEVOPS_PAT: azure_devops,
}


def validate(declaration: Declaration) -> None:
    for credential in declaration.credentials:
        PROVIDERS[credential.provider].validate(credential)
