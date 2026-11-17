from __future__ import annotations

import base64
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from credentials.context import Context
from credentials.model import Client, CredentialError

if TYPE_CHECKING:
    from gate_credential import CredentialRequest


def reply(request: CredentialRequest, context: Context, now: datetime) -> str:
    path = request.path
    if not path:
        if context.declaration is not None and context.declaration.supports(Client.GIT):
            raise CredentialError(
                "credential-target-unknown: Git omitted the requested repository; "
                "enable credential.https://github.com.useHttpPath"
            )
        return ""
    url = f"{request.protocol}://{request.host}/{path}"
    current = context.resolve(Client.GIT, url)
    if current is None:
        if context.declaration is not None and context.declaration.supports(Client.GIT):
            raise CredentialError(
                "credential-target-denied: Git target has no declared credential"
            )
        return ""
    if "authtype" not in request.capabilities:
        raise CredentialError(
            "credential-client-unsupported: Git 2.46 or newer is required"
        )
    secret = current.authorize(now)
    encoded = base64.b64encode(
        f"{current.credential.login}:{secret.value}".encode()
    ).decode()
    return (
        "capability[]=authtype\n"
        f"username=tj-credentials-{current.credential.key}\n"
        f"authtype=Basic\ncredential={encoded}\nephemeral=1\n"
    )


def configured_reply(
    request: CredentialRequest, cwd: Path, environment: Mapping[str, str], now: float
) -> str:
    home = Path(
        environment.get("USERPROFILE") or environment.get("HOME") or "~"
    ).expanduser()
    state = Path(environment.get("AGENT_STATE_DIRECTORY", str(home / ".agents-state")))
    context = Context.load(
        cwd,
        environment,
        state,
        windows="SYSTEMROOT" in {key.upper() for key in environment},
    )
    return reply(request, context, datetime.fromtimestamp(now, UTC))
