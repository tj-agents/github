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
    PNPM = "pnpm"
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
    variable: str | None = None

    def __post_init__(self) -> None:
        parsed = plain_url(self.url)
        if self.variable is not None and (
            self.client not in {Client.NUGET, Client.NPM}
            or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", self.variable)
        ):
            raise CredentialError(
                "credentials-invalid: declare an upper-case variable "
                "only for nuget or npm endpoints"
            )
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
class ActionsSecret:
    repository: str
    name: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", self.repository) or (
            self.repository.split("/")[1] in {".", ".."}
        ):
            raise CredentialError("credentials-invalid: declare the Actions repository")
        if not re.fullmatch(
            r"[A-Z_][A-Z0-9_]{0,99}", self.name
        ) or self.name.startswith("GITHUB_"):
            raise CredentialError("credentials-invalid: invalid Actions secret name")


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
    actions_secret: ActionsSecret | None = None

    def __post_init__(self) -> None:
        if not NAME.fullmatch(self.name) or not OWNER.fullmatch(self.owner):
            raise CredentialError(
                "credentials-invalid: invalid credential name or owner"
            )
        if not re.fullmatch(r"[A-Za-z0-9_.@+-]{1,200}", self.login):
            raise CredentialError("credentials-invalid: invalid account login")
        if self.actions_secret is not None:
            self.validate_actions_secret(self.actions_secret)
        maximum_lifetime = 180 if self.actions_secret is not None else 90
        if (
            type(self.expires_in) is not int
            or not 1 <= self.expires_in <= maximum_lifetime
        ):
            raise CredentialError(
                f"credentials-invalid: expires_in must be 1 to {maximum_lifetime} days"
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
        if self.actions_secret is not None:
            target = self.actions_secret
            identity += f"/actions/{target.repository}/{target.name}".casefold()
        return hashlib.sha256(identity.encode()).hexdigest()

    def validate_actions_secret(self, target: ActionsSecret) -> None:
        if (
            self.provider != Provider.GITHUB_FINE_GRAINED
            or self.scopes
            or self.permissions != (("contents", "read"),)
            or target.repository.split("/")[0].casefold() != self.owner.casefold()
        ):
            raise CredentialError(
                "credentials-invalid: Actions secrets require a same-owner destination "
                "and a fine-grained token with only Contents read"
            )
        for endpoint in self.endpoints:
            if (
                endpoint.client != Client.GIT
                or endpoint.probe is not None
                or not re.fullmatch(
                    rf"https://github\.com/{re.escape(self.owner)}/[A-Za-z0-9_.-]+",
                    endpoint.url,
                    re.IGNORECASE,
                )
                or endpoint.url.endswith(".git")
            ):
                raise CredentialError(
                    "credentials-invalid: name each source by its GitHub repository URL"
                )

    @property
    def action(self) -> str:
        return f"tj-credentials onboard --name {self.name}"

    def supports(self, client: Client) -> bool:
        return self.actions_secret is None and any(
            endpoint.client == client for endpoint in self.endpoints
        )

    def matches(self, client: Client, url: str) -> bool:
        return self.actions_secret is None and any(
            endpoint.matches(client, url) for endpoint in self.endpoints
        )


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
        destinations: set[tuple[str, str]] = set()
        for credential in self.credentials:
            target = credential.actions_secret
            if target is not None:
                key = target.repository.casefold(), target.name
                if key in destinations:
                    raise CredentialError(
                        "credentials-invalid: duplicate Actions secret"
                    )
                destinations.add(key)
                continue
            routes.extend(
                (endpoint.client, endpoint.url.casefold(), endpoint.scope)
                for endpoint in credential.endpoints
            )
        if len(routes) != len(set(routes)):
            raise CredentialError(
                "credentials-invalid: two credentials claim the same endpoint"
            )
        variables: dict[str, str] = {}
        for credential in self.credentials:
            for endpoint in credential.endpoints:
                if (
                    endpoint.variable is not None
                    and variables.setdefault(endpoint.variable, credential.key)
                    != credential.key
                ):
                    raise CredentialError(
                        "credentials-invalid: two credentials claim the same variable"
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
        or set(entry) - required - {"permissions", "actions_secret"}
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
            or set(item) - {"client", "url", "probe", "scope", "variable"}
            or not {"client", "url"} <= set(item)
        ):
            raise ValueError("endpoint fields")
        if not all(
            isinstance(value, str)
            or (key in {"probe", "scope", "variable"} and value is None)
            for key, value in item.items()
        ):
            raise ValueError("endpoint values")
        endpoints.append(
            Endpoint(
                Client(item["client"]),
                item["url"],
                item.get("probe"),
                item.get("scope"),
                item.get("variable"),
            )
        )
    permissions = entry.get("permissions", {})
    if not isinstance(permissions, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in permissions.items()
    ):
        raise ValueError("credential permissions")
    target = entry.get("actions_secret")
    if target is not None and (
        not isinstance(target, dict)
        or set(target) != {"repository", "name"}
        or not all(isinstance(value, str) for value in target.values())
    ):
        raise ValueError("Actions secret destination")
    return Credential(
        entry["name"],
        Provider(entry["provider"]),
        entry["owner"],
        entry["login"],
        tuple(entry["scopes"]),
        tuple(endpoints),
        entry["expires_in"],
        tuple(permissions.items()),
        ActionsSecret(**target) if target is not None else None,
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
        declaration = at_revision(cwd, safe, revision)
        if declaration is not None:
            return declaration
    return None


def at_revision(
    cwd: Path, environment: Mapping[str, str], revision: str
) -> Declaration | None:
    try:
        text = committed_configuration(cwd, environment, revision)
    except ConfigurationError as error:
        raise CredentialError(f"credentials-invalid: {error}") from error
    return None if text is None else declared(text)
