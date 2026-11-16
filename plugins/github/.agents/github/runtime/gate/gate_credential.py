"""The Git credential helper setup registers first for https://github.com.

It prints nothing for humans and for agents whose repository resolves to `human`, so the
human's own helpers that follow it answer exactly as before. For an agent whose
repository resolves to `app` it answers with the App's installation token, or tells Git
to quit; it never lets the human's helpers answer such an agent.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

from gate_access import Declarations
from gate_agent import agent_host
from gate_app import GitHubApp
from gate_declaration import AppAccess, HumanAccess
from gate_error import GateError
from gate_owner import origin_owner

AUTHTYPE = "authtype"
QUIT = "quit=1\n"
MAX_REQUEST_CHARACTERS = 64 * 1024


class CredentialError(GateError):
    pass


@dataclass(frozen=True, slots=True)
class CredentialRequest:
    protocol: str
    host: str
    path: str
    capabilities: frozenset[str]

    @classmethod
    def parse(cls, text: str) -> CredentialRequest:
        fields: dict[str, str] = {}
        capabilities: set[str] = set()
        for line in text.splitlines():
            if not line:
                break
            key, _, value = line.partition("=")
            if key == "capability[]":
                capabilities.add(value)
            else:
                fields[key] = value
        return cls(
            fields.get("protocol", ""),
            fields.get("host", "").lower(),
            fields.get("path", ""),
            frozenset(capabilities),
        )

    @property
    def is_github(self) -> bool:
        return self.protocol == "https" and self.host == "github.com"

    @property
    def owner(self) -> str | None:
        return self.path.split("/")[0] or None


@dataclass(frozen=True, slots=True)
class Answer:
    """What the helper writes to Git, and a message for the person or agent."""

    text: str
    message: str | None = None


def app_credential(
    request: CredentialRequest,
    environment: Mapping[str, str],
    cwd: Path,
    app: GitHubApp,
    now: float,
) -> str:
    """The App's token as a one-off Authorization header Git must not store.

    Git also sends `store` and `erase` to every later helper. An `authtype` credential
    carries no password, so the human's helpers cannot save the App token, and its
    `x-access-token` username keeps an `erase` away from the human's own entries.
    """
    if AUTHTYPE not in request.capabilities:
        raise CredentialError(
            "agents acting as a GitHub App need Git 2.46 or newer, which keeps the "
            "App token away from the human's credential helpers"
        )
    owner = request.owner or origin_owner(cwd, environment)
    if owner is None:
        raise CredentialError(
            "cannot tell which GitHub account this request targets; use an origin "
            "remote on github.com or set credential.useHttpPath"
        )
    token = app.installation_token(owner, now)
    encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return (
        "capability[]=authtype\n"
        "username=x-access-token\n"
        "authtype=Basic\n"
        f"credential={encoded}\n"
        "ephemeral=1\n"
    )


def agent_reply(
    request: CredentialRequest,
    environment: Mapping[str, str],
    cwd: Path,
    declarations: Declarations,
    now: float,
) -> str:
    access = declarations.resolve(cwd, environment, now)
    match access:
        case HumanAccess():
            return ""
        case AppAccess(slug=slug):
            app = declarations.apps(slug)
            return app_credential(request, environment, cwd, app, now)
        case _:
            assert_never(access)


def answer(
    operation: str,
    request: CredentialRequest,
    environment: Mapping[str, str],
    cwd: Path,
    declarations: Declarations,
    now: float,
) -> Answer:
    if operation != "get" or not request.is_github or agent_host(environment) is None:
        return Answer("")
    try:
        text = agent_reply(request, environment, cwd, declarations, now)
    except GateError as error:
        return Answer(QUIT, str(error))
    return Answer(text)
