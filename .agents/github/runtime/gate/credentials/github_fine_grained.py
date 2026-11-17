from __future__ import annotations

import re
import urllib.parse
from datetime import datetime

from credentials import github, http
from credentials.model import Credential, CredentialError
from credentials.vault import Secret


def validate(credential: Credential) -> None:
    github.validate(credential, packages=False)
    if credential.scopes:
        raise CredentialError(
            "credentials-invalid: fine-grained tokens use repository "
            "permissions, not classic scopes"
        )


def creation_url(credential: Credential) -> str:
    return (
        "https://github.com/settings/personal-access-tokens/new?"
        + urllib.parse.urlencode(
            {
                "name": "TJ Agents " + credential.name,
                "target_name": credential.owner,
                "expires_in": credential.expires_in,
                **dict(credential.permissions),
            }
        )
    )


def instructions(credential: Credential) -> str:
    return (
        f"Create a fine-grained token as {credential.login}, select "
        f"resource owner {credential.owner} and the declared repositories "
        "and permissions, and obtain organization approval."
    )


def verify(
    credential: Credential, secret: Secret, transport: http.Transport
) -> datetime | None:
    if not re.fullmatch(r"github_pat_[A-Za-z0-9_]{40,245}", secret.value):
        raise CredentialError(
            "credential-type-mismatch: submit a GitHub fine-grained PAT"
        )
    response = github.identity(credential, secret, transport)
    github.verify_endpoints(credential, secret, transport)
    return github.expiration(response)
