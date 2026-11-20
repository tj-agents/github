from __future__ import annotations

import base64
import re
import urllib.parse
from datetime import UTC, datetime

from credentials import http
from credentials.model import Client, Credential, CredentialError, Endpoint, plain_url
from credentials.vault import Secret


def validate(credential: Credential, *, packages: bool) -> None:
    owner = "/" + credential.owner.casefold()
    for endpoint in credential.endpoints:
        parsed = plain_url(endpoint.url)
        path = parsed.path.rstrip("/").casefold()
        valid = (
            (
                parsed.hostname == "github.com"
                and (path == owner or path.startswith(owner + "/"))
                and endpoint.client in {Client.GIT, Client.GH, Client.MCP}
            )
            or (
                parsed.hostname == "nuget.pkg.github.com"
                and path == owner + "/index.json"
                and endpoint.client == Client.NUGET
            )
            or (
                parsed.hostname == "npm.pkg.github.com"
                and not path
                and endpoint.client == Client.NPM
                and endpoint.scope == "@" + credential.owner.casefold()
            )
            or (
                parsed.hostname == "ghcr.io"
                and (path == owner or path.startswith(owner + "/"))
                and endpoint.client == Client.CONTAINER
            )
        )
        if not valid:
            raise CredentialError(
                "credentials-invalid: GitHub endpoint is outside the declared account"
            )
        if endpoint.client in {Client.GIT, Client.GH, Client.MCP}:
            parts = parsed.path.strip("/").split("/")
            if len(parts) > 2 or (len(parts) == 1 and endpoint.probe is None):
                raise CredentialError(
                    "credentials-invalid: declare a repository access probe"
                )
            if endpoint.probe is not None:
                probe = plain_url(endpoint.probe)
                probe_parts = probe.path.strip("/").split("/")
                if (
                    probe.hostname != "github.com"
                    or len(probe_parts) != 2
                    or probe_parts[0].casefold() != credential.owner.casefold()
                ):
                    raise CredentialError(
                        "credentials-invalid: repository probe is outside "
                        "the declared owner"
                    )
        if endpoint.client in {Client.NUGET, Client.NPM, Client.CONTAINER}:
            if not packages or "read:packages" not in credential.scopes:
                raise CredentialError(
                    "credentials-invalid: GitHub Packages requires "
                    "github-classic and read:packages"
                )
            if endpoint.probe is None:
                raise CredentialError(
                    "credentials-invalid: declare an authenticated package probe"
                )
            validate_probe(credential, endpoint)


def validate_probe(credential: Credential, endpoint: Endpoint) -> None:
    if endpoint.probe is None:
        return
    parsed = plain_url(endpoint.probe)
    prefix = "/" + credential.owner.casefold() + "/"
    path = parsed.path.casefold()
    valid = (
        (endpoint.client == Client.NUGET and path.startswith(prefix))
        or (
            endpoint.client == Client.NPM
            and path.startswith("/@" + credential.owner.casefold() + "/")
        )
        or (
            endpoint.client == Client.CONTAINER
            and path.startswith("/v2" + prefix)
            and "/manifests/" in path
        )
    )
    if not valid:
        raise CredentialError(
            "credentials-invalid: package probe must name a package "
            "in the declared account"
        )


def authorization(secret: Secret) -> dict[str, str]:
    return {
        "Authorization": "Bearer " + secret.value,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def identity(
    credential: Credential, secret: Secret, transport: http.Transport
) -> http.Response:
    response = transport("https://api.github.com/user", authorization(secret))
    http.require_success(response, credential.action)
    user = response.json()
    if (
        not isinstance(user, dict)
        or str(user.get("login", "")).casefold() != credential.login.casefold()
    ):
        raise CredentialError(f"credential-account-mismatch: {credential.action}")
    return response


def expiration(response: http.Response) -> datetime | None:
    value = response.headers.get("github-authentication-token-expiration")
    if value is None:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S %Z").replace(tzinfo=UTC)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise CredentialError(
                "credential-probe-invalid: GitHub returned an invalid token expiry"
            ) from None
    if parsed.tzinfo is None:
        raise CredentialError(
            "credential-probe-invalid: GitHub returned an ambiguous token expiry"
        )
    return parsed


def verify_endpoints(
    credential: Credential, secret: Secret, transport: http.Transport
) -> None:
    for endpoint in credential.endpoints:
        parsed = plain_url(endpoint.url)
        if endpoint.client in {Client.GIT, Client.GH, Client.MCP}:
            selected = (
                plain_url(endpoint.probe) if endpoint.probe is not None else parsed
            )
            components = selected.path.strip("/").split("/")
            if len(components) != 2:
                raise CredentialError(
                    "credentials-invalid: declare a repository access probe"
                )
            repository = components[1].removesuffix(".git")
            url = f"https://api.github.com/repos/{credential.owner}/{repository}"
            response = transport(url, authorization(secret))
            http.require_success(response, credential.action)
            data = response.json()
            if (
                not isinstance(data, dict)
                or str(data.get("full_name", "")).casefold()
                != f"{credential.owner}/{repository}".casefold()
            ):
                raise CredentialError(
                    "credential-probe-invalid: unexpected repository identity"
                )
            if credential.actions_secret is not None:
                commits = transport(url + "/commits?per_page=1", authorization(secret))
                http.require_success(commits, credential.action)
                latest = commits.json()
                if not (
                    isinstance(latest, list)
                    and latest
                    and isinstance(latest[0], dict)
                    and re.fullmatch(r"[0-9a-f]{40}", str(latest[0].get("sha")))
                ):
                    raise CredentialError(
                        "credential-probe-invalid: expected a readable source commit"
                    )
        elif endpoint.client == Client.CONTAINER:
            response = container_probe(credential, endpoint, secret, transport)
        else:
            if endpoint.probe is None:
                raise CredentialError(
                    "credentials-invalid: package verification needs a declared probe"
                )
            headers = authorization(secret)
            if endpoint.client == Client.NUGET:
                encoded = base64.b64encode(
                    f"{credential.login}:{secret.value}".encode()
                ).decode()
                headers["Authorization"] = "Basic " + encoded
            if endpoint.client == Client.NPM:
                url = endpoint.probe.replace(
                    "/@" + credential.owner.casefold() + "/",
                    "/@" + credential.owner.casefold() + "%2f",
                    1,
                )
            else:
                url = endpoint.probe
            response = transport(url, headers)
        http.require_success(response, credential.action)


def container_probe(
    credential: Credential,
    endpoint: Endpoint,
    secret: Secret,
    transport: http.Transport,
) -> http.Response:
    if endpoint.probe is None:
        raise CredentialError(
            "credentials-invalid: container verification needs a declared manifest"
        )
    path = plain_url(endpoint.probe).path
    match = re.fullmatch(
        r"/v2/([a-z0-9._/-]+)/manifests/(sha256:[a-f0-9]{64}|[A-Za-z0-9_.-]+)", path
    )
    if match is None:
        raise CredentialError("credentials-invalid: invalid container manifest probe")
    repository, _ = match.groups()
    encoded = base64.b64encode(f"{credential.login}:{secret.value}".encode()).decode()
    url = "https://ghcr.io/token?" + urllib.parse.urlencode(
        {"service": "ghcr.io", "scope": "repository:" + repository + ":pull"}
    )
    response = transport(url, {"Authorization": "Basic " + encoded})
    http.require_success(response, credential.action)
    value = response.json()
    token = value.get("token") if isinstance(value, dict) else None
    if (
        not isinstance(token, str)
        or not token
        or any(character.isspace() for character in token)
    ):
        raise CredentialError(
            "credential-probe-invalid: invalid registry bearer credential"
        )
    return transport(
        endpoint.probe,
        {
            "Authorization": "Bearer " + token,
            "Accept": (
                "application/vnd.oci.image.manifest.v1+json, "
                "application/vnd.docker.distribution.manifest.v2+json"
            ),
        },
    )
