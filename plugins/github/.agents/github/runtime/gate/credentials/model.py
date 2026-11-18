from __future__ import annotations

import hashlib
import re
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from gate_error import GateError
from gate_git import git, machine_environment
from repository_configuration import (
    CONFIGURATION,
    ConfigurationError,
    Section,
    sections,
)
from repository_configuration import committed as committed_configuration

WHERE = f"the credentials in {CONFIGURATION}"
NAME = re.compile(r"[a-z][a-z0-9-]{0,62}")
OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}")


class CredentialError(GateError):
    pass


class Provider(StrEnum):
    GITHUB_CLASSIC = "github-classic"
    GITHUB_FINE_GRAINED = "github-fine-grained"
    AZURE_DEVOPS_PAT = "azure-devops-pat"


class Client(StrEnum):
    GIT = "git"
    GH = "gh"
    MCP = "mcp"
    NUGET = "nuget"
    NPM = "npm"
    CONTAINER = "container"
    AZURE_DEVOPS = "azure-devops"


class Tool(StrEnum):
    DOTNET = "dotnet"
    NPM = "npm"
    DOCKER = "docker"
    AZURE_CLI = "az"


def plain_url(value: str) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or "%" in value
        or "\\" in value
        or any(part in {".", ".."} for part in parsed.path.split("/"))
        or any(ord(character) < 33 for character in value)
    ):
        raise CredentialError("credentials-invalid: endpoints need plain HTTPS URLs")
    return parsed


@dataclass(frozen=True, slots=True)
class Endpoint:
    client: Client
    url: str
    probe: str | None = None
    scope: str | None = None

    def __post_init__(self) -> None:
        parsed = plain_url(self.url)
        if self.scope is not None and not re.fullmatch(
            r"@[a-z0-9][a-z0-9-]{0,99}", self.scope
        ):
            raise CredentialError("credentials-invalid: npm scope must be @scope")
        if (self.client == Client.NPM) != (self.scope is not None):
            raise CredentialError(
                "credentials-invalid: declare scope only for npm endpoints"
            )
        if self.probe is not None and plain_url(self.probe).netloc != parsed.netloc:
            raise CredentialError(
                "credentials-invalid: probe must use the endpoint's HTTPS host"
            )

    def matches(self, client: Client, url: str) -> bool:
        if client != self.client:
            return False
        try:
            target = plain_url(url)
        except (CredentialError, ValueError):
            return False
        base = urllib.parse.urlsplit(self.url)
        if target.netloc.casefold() != base.netloc.casefold():
            return False
        prefix = base.path.rstrip("/").casefold()
        path = target.path.rstrip("/").casefold()
        if client in {Client.GIT, Client.GH, Client.MCP}:
            prefix = prefix.removesuffix(".git")
            path = path.removesuffix(".git")
        return path == prefix or path.startswith(prefix + "/")


@dataclass(frozen=True, slots=True)
class Credential:
    name: str
    provider: Provider
    owner: str
    login: str
    scopes: tuple[str, ...]
    endpoints: tuple[Endpoint, ...]
    expires_in: int = 30
    permissions: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not NAME.fullmatch(self.name) or not OWNER.fullmatch(self.owner):
            raise CredentialError(
                "credentials-invalid: invalid credential name or owner"
            )
        if not re.fullmatch(r"[A-Za-z0-9_.@+-]{1,200}", self.login):
            raise CredentialError("credentials-invalid: invalid account login")
        if type(self.expires_in) is not int or not 1 <= self.expires_in <= 90:
            raise CredentialError(
                "credentials-invalid: expires_in must be 1 to 90 days"
            )
        if not self.endpoints or len(set(self.endpoints)) != len(self.endpoints):
            raise CredentialError(
                "credentials-invalid: declare distinct credential endpoints"
            )
        if any(
            not re.fullmatch(r"[a-z][a-z0-9_.]*(?::[a-z_]+)?", scope)
            for scope in self.scopes
        ):
            raise CredentialError("credentials-invalid: invalid token scope")
        if len(set(self.scopes)) != len(self.scopes):
            raise CredentialError("credentials-invalid: duplicate token scopes")
        allowed = {"contents", "pull_requests", "issues", "actions", "workflows"}
        if any(
            key not in allowed or value not in {"read", "write"}
            for key, value in self.permissions
        ) or len(dict(self.permissions)) != len(self.permissions):
            raise CredentialError(
                "credentials-invalid: unsupported repository permissions"
            )

    @property
    def key(self) -> str:
        identity = f"{self.provider}/{self.owner}/{self.login}".casefold()
        return hashlib.sha256(identity.encode()).hexdigest()

    @property
    def action(self) -> str:
        return f"tj-credentials onboard --name {self.name}"

    def supports(self, client: Client) -> bool:
        return any(endpoint.client == client for endpoint in self.endpoints)

    def matches(self, client: Client, url: str) -> bool:
        return any(endpoint.matches(client, url) for endpoint in self.endpoints)


@dataclass(frozen=True, slots=True)
class Declaration:
    credentials: tuple[Credential, ...]

    def supports(self, client: Client) -> bool:
        return any(credential.supports(client) for credential in self.credentials)

    def __post_init__(self) -> None:
        names = [credential.name for credential in self.credentials]
        if len(names) != len(set(names)):
            raise CredentialError(
                "credentials-invalid: declare distinct credential names"
            )
        routes: list[tuple[Client, str, str | None]] = []
        for credential in self.credentials:
            routes.extend(
                (endpoint.client, endpoint.url.casefold(), endpoint.scope)
                for endpoint in credential.endpoints
            )
        if len(routes) != len(set(routes)):
            raise CredentialError(
                "credentials-invalid: two credentials claim the same endpoint"
            )

    @classmethod
    def parse(cls, entries: object) -> Declaration:
        """The credentials a `credentials` section lists."""
        try:
            if not isinstance(entries, list) or len(entries) > 32:
                raise ValueError
            credentials = tuple(_parse_credential(entry) for entry in entries)
        except (ValueError, TypeError, KeyError) as error:
            raise CredentialError(f"credentials-invalid: check {WHERE}") from error
        return cls(credentials)

    def named(self, name: str) -> Credential:
        for credential in self.credentials:
            if credential.name == name:
                return credential
        raise CredentialError("credential-unknown: select a declared credential name")

    def route(self, client: Client, url: str) -> Credential | None:
        matches = [
            credential
            for credential in self.credentials
            if credential.matches(client, url)
        ]
        if len(matches) > 1:
            raise CredentialError(
                "credential-ambiguous: overlapping credential endpoints"
            )
        return matches[0] if matches else None


def _parse_credential(entry: object) -> Credential:
    required = {
        "name",
        "provider",
        "owner",
        "login",
        "scopes",
        "endpoints",
        "expires_in",
    }
    if (
        not isinstance(entry, dict)
        or not required <= set(entry)
        or set(entry) - required - {"permissions"}
    ):
        raise ValueError("credential fields")
    if not all(
        isinstance(entry[field], str)
        for field in ("name", "provider", "owner", "login")
    ):
        raise ValueError("credential identity")
    if not isinstance(entry["scopes"], list) or not all(
        isinstance(scope, str) for scope in entry["scopes"]
    ):
        raise ValueError("credential scopes")
    endpoints_data = entry["endpoints"]
    if not isinstance(endpoints_data, list) or len(endpoints_data) > 100:
        raise ValueError("credential endpoints")
    endpoints = []
    for item in endpoints_data:
        if (
            not isinstance(item, dict)
            or set(item) - {"client", "url", "probe", "scope"}
            or not {"client", "url"} <= set(item)
        ):
            raise ValueError("endpoint fields")
        if not all(
            isinstance(value, str) or (key in {"probe", "scope"} and value is None)
            for key, value in item.items()
        ):
            raise ValueError("endpoint values")
        endpoints.append(
            Endpoint(
                Client(item["client"]),
                item["url"],
                item.get("probe"),
                item.get("scope"),
            )
        )
    permissions = entry.get("permissions", {})
    if not isinstance(permissions, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in permissions.items()
    ):
        raise ValueError("credential permissions")
    return Credential(
        entry["name"],
        Provider(entry["provider"]),
        entry["owner"],
        entry["login"],
        tuple(entry["scopes"]),
        tuple(endpoints),
        entry["expires_in"],
        tuple(permissions.items()),
    )


def declared(text: str | bytes) -> Declaration | None:
    """The credentials a configuration document declares; None without that section."""
    try:
        declared_sections = sections(text)
    except ConfigurationError as error:
        raise CredentialError(f"credentials-invalid: {error}") from error
    if Section.CREDENTIALS not in declared_sections:
        return None
    return Declaration.parse(declared_sections[Section.CREDENTIALS])


def committed(cwd: Path, environment: Mapping[str, str]) -> Declaration | None:
    """The credentials committed at HEAD, else at the fetched `origin/HEAD`.

    A revision whose configuration has no credentials section leaves them to the next.
    """
    safe = machine_environment(environment)
    if git(["rev-parse", "--git-dir"], cwd, safe).returncode:
        return None
    for revision in ("HEAD", "refs/remotes/origin/HEAD"):
        declaration = _at_revision(cwd, safe, revision)
        if declaration is not None:
            return declaration
    return None


def _at_revision(
    cwd: Path, environment: Mapping[str, str], revision: str
) -> Declaration | None:
    try:
        text = committed_configuration(cwd, environment, revision)
    except ConfigurationError as error:
        raise CredentialError(f"credentials-invalid: {error}") from error
    return None if text is None else declared(text)
