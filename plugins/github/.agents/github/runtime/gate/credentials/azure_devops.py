from __future__ import annotations

import base64
import re
from datetime import datetime

from credentials import http
from credentials.model import Client, Credential, CredentialError, plain_url
from credentials.vault import Secret


def validate(credential: Credential) -> None:
    prefix = "/" + credential.owner.casefold()
    for endpoint in credential.endpoints:
        if endpoint.client == Client.NUGET and endpoint.probe is None:
            raise CredentialError(
                "credentials-invalid: declare an Azure package access probe"
            )
        parsed = plain_url(endpoint.url)
        path = parsed.path.rstrip("/").casefold()
        if (
            parsed.hostname not in {"dev.azure.com", "pkgs.dev.azure.com"}
            or not (path == prefix or path.startswith(prefix + "/"))
            or endpoint.client not in {Client.NUGET, Client.AZURE_DEVOPS}
        ):
            raise CredentialError(
                "credentials-invalid: Azure DevOps endpoint is outside "
                "the declared organization"
            )
        if endpoint.probe is not None and not plain_url(
            endpoint.probe
        ).path.casefold().startswith(prefix + "/"):
            raise CredentialError(
                "credentials-invalid: Azure probe is outside the declared organization"
            )


def creation_url(credential: Credential) -> str:
    return f"https://dev.azure.com/{credential.owner}/_usersSettings/tokens"


def instructions(credential: Credential) -> str:
    return (
        f"Create an organization-scoped Azure DevOps PAT for {credential.owner} "
        f"as {credential.login} with {', '.join(credential.scopes)}. "
        "Select the displayed expiry."
    )


def verify(
    credential: Credential, secret: Secret, transport: http.Transport
) -> datetime | None:
    if not re.fullmatch(
        r"[A-Za-z0-9]{52}|[A-Za-z0-9]{75}AZDO[A-Za-z0-9]{5}", secret.value
    ):
        raise CredentialError("credential-type-mismatch: submit an Azure DevOps PAT")
    encoded = base64.b64encode((":" + secret.value).encode()).decode()
    headers = {"Authorization": "Basic " + encoded, "Accept": "application/json"}
    response = transport(
        f"https://dev.azure.com/{credential.owner}/_apis/connectionData?api-version=7.1",
        headers,
    )
    http.require_success(response, credential.action)
    data = response.json()
    user = data.get("authenticatedUser") if isinstance(data, dict) else None
    if not isinstance(user, dict):
        raise CredentialError(
            "credential-probe-invalid: Azure DevOps returned no authenticated identity"
        )
    identities = [
        user.get("id"),
        user.get("providerDisplayName"),
        user.get("uniqueName"),
    ]
    properties = user.get("properties")
    account = properties.get("Account") if isinstance(properties, dict) else None
    if isinstance(account, dict):
        identities.append(account.get("$value"))
    if credential.login.casefold() not in {
        value.casefold() for value in identities if isinstance(value, str)
    }:
        raise CredentialError(f"credential-account-mismatch: {credential.action}")
    for endpoint in credential.endpoints:
        if endpoint.probe is not None:
            http.require_success(transport(endpoint.probe, headers), credential.action)
    return None
