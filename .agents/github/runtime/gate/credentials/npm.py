from __future__ import annotations

import tempfile
import urllib.parse
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from credentials import children
from credentials.context import Context
from credentials.model import Client, CredentialError
from credentials.vault import private_environment


def run(
    context: Context, executable: Path, arguments: Sequence[str], now: datetime
) -> int:
    if not arguments or arguments[0] in {
        "config",
        "help",
        "--help",
        "--version",
        "-v",
        "version",
        "prefix",
        "root",
        "completion",
    }:
        return children.execute(executable, arguments, context.cwd, context.environment)
    environment = private_environment(context.environment)
    lines: list[str] = []
    hosts: set[str] = set()
    if context.declaration is None:
        return children.execute(executable, arguments, context.cwd, context.environment)
    for credential in context.declaration.credentials:
        endpoints = [
            endpoint
            for endpoint in credential.endpoints
            if endpoint.client == Client.NPM
        ]
        if not endpoints:
            continue
        current = context.service(credential)
        secret = current.authorize(now)
        variable = "TJ_CREDENTIAL_" + credential.key.upper()
        environment[variable] = secret.value
        for endpoint in endpoints:
            host = urllib.parse.urlsplit(endpoint.url).netloc
            if host in hosts:
                raise CredentialError(
                    "credential-client-conflict: npm accepts "
                    "one credential per registry host"
                )
            hosts.add(host)
            lines.extend(
                (
                    f"{endpoint.scope}:registry={endpoint.url}",
                    f"//{host}/:_authToken=${{{variable}}}",
                )
            )
    if not lines:
        return children.execute(executable, arguments, context.cwd, context.environment)
    configured = context.environment.get(
        "NPM_CONFIG_USERCONFIG"
    ) or context.environment.get("npm_config_userconfig")
    userconfig = (
        Path(configured).expanduser() if configured else context.home / ".npmrc"
    )
    if userconfig.is_file():
        for line in userconfig.read_text(encoding="utf-8").splitlines():
            key = line.partition("=")[0].casefold()
            if not any(
                secret_key in key
                for secret_key in (
                    "_auth",
                    "_password",
                    "username",
                    "password",
                    "tokenhelper",
                )
            ):
                lines.insert(0, line)
    with tempfile.TemporaryDirectory(prefix="tj-credentials-npm-") as directory:
        public = Path(directory) / "npmrc"
        public.write_text("\n".join(lines) + "\n", encoding="utf-8")
        for key in tuple(environment):
            if key.upper() == "NPM_CONFIG_USERCONFIG":
                environment.pop(key)
        environment.update(NPM_CONFIG_USERCONFIG=str(public), npm_config_logs_max="0")
        return children.execute(executable, arguments, context.cwd, environment)
