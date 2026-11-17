from __future__ import annotations

import re
import urllib.parse
from datetime import datetime

from credentials import github, http
from credentials.model import Credential, CredentialError
from credentials.vault import Secret


def validate(credential: Credential) -> None:
    github.validate(credential, packages=True)
    if (
        any(value == "write" for _, value in credential.permissions)
        and "repo" not in credential.scopes
    ):
        raise CredentialError(
            "credentials-invalid: repository writes require declared repo scope"
        )
    if (
        dict(credential.permissions).get("workflows") == "write"
        and "workflow" not in credential.scopes
    ):
        raise CredentialError(
            "credentials-invalid: workflow writes require declared workflow scope"
        )


def creation_url(credential: Credential) -> str:
    return "https://github.com/settings/tokens/new?" + urllib.parse.urlencode(
        {
            "description": "TJ Agents " + credential.name,
            "scopes": ",".join(credential.scopes),
        }
    )


def instructions(credential: Credential) -> str:
    return (
        f"Create a classic token as {credential.login} "
        f"with {', '.join(credential.scopes)}. Select the displayed expiry "
        f"and authorize {credential.owner} with Configure SSO."
    )


def verify(
    credential: Credential, secret: Secret, transport: http.Transport
) -> datetime | None:
    if not re.fullmatch(r"ghp_[A-Za-z0-9]{36}", secret.value):
        raise CredentialError("credential-type-mismatch: submit a GitHub classic PAT")
    response = github.identity(credential, secret, transport)
    scopes = {
        scope.strip()
        for scope in response.headers.get("x-oauth-scopes", "").split(",")
        if scope.strip()
    }
    if not set(credential.scopes) <= scopes:
        raise CredentialError(f"credential-scope-missing: {credential.action}")
    github.verify_endpoints(credential, secret, transport)
    return github.expiration(response)
