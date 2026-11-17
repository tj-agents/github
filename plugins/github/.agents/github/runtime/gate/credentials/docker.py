from __future__ import annotations

import json
import secrets
import subprocess
import tempfile
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from credentials import children
from credentials.context import Context
from credentials.model import Client, CredentialError
from credentials.vault import Secret, executable, private_environment


@dataclass(frozen=True, slots=True)
class Saved:
    login: str
    secret: Secret


def helper(registry: str, context: Context) -> Path:
    directory = Path(
        context.environment.get("DOCKER_CONFIG", str(context.home / ".docker"))
    )
    try:
        config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CredentialError(
            "docker-vault-unavailable: configure Docker's OS credential helper"
        ) from None
    if not isinstance(config, dict):
        raise CredentialError("docker-vault-unavailable: invalid Docker configuration")
    scoped = config.get("credHelpers", {})
    name = scoped.get(registry) if isinstance(scoped, dict) else None
    name = name or config.get("credsStore")
    allowed = (
        {"desktop", "wincred"}
        if context.windows
        else {"desktop", "desktop.exe", "secretservice", "pass"}
    )
    if not isinstance(name, str) or name not in allowed:
        raise CredentialError(
            "docker-vault-unavailable: configure Docker's OS credential helper"
        )
    return executable("docker-credential-" + name, context.environment)


def call(
    program: Path, operation: str, payload: str, environment: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="tj-credentials-docker-") as neutral:
        try:
            result = subprocess.run(
                [str(program), operation],
                input=payload,
                text=True,
                encoding="utf-8",
                capture_output=True,
                cwd=neutral,
                env=private_environment(environment),
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise CredentialError(
                "docker-vault-unavailable: unlock Docker's OS credential store"
            ) from None
    return result


def read(program: Path, registry: str, environment: Mapping[str, str]) -> Saved | None:
    result = call(program, "get", registry + "\n", environment)
    if result.returncode:
        message = (result.stdout + result.stderr).casefold()
        if (
            "credentials not found" in message
            or "not found in native keychain" in message
        ):
            return None
        raise CredentialError(
            "docker-vault-unavailable: unlock Docker's OS credential store"
        )
    try:
        data = json.loads(result.stdout)
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("Username"), str)
            or not isinstance(data.get("Secret"), str)
        ):
            raise ValueError
        saved = Saved(data["Username"], Secret(data["Secret"]))
    except (ValueError, CredentialError):
        raise CredentialError(
            "docker-vault-unavailable: invalid secure-helper response"
        ) from None
    return saved


def store(
    program: Path, registry: str, saved: Saved, environment: Mapping[str, str]
) -> None:
    payload = json.dumps(
        {"ServerURL": registry, "Username": saved.login, "Secret": saved.secret.value}
    )
    if call(program, "store", payload, environment).returncode:
        raise CredentialError("docker-vault-unavailable: secure-helper storage failed")


def synchronize(
    program: Path, registry: str, saved: Saved, environment: Mapping[str, str]
) -> None:
    previous = read(program, registry, environment)
    if previous is not None and previous.login.casefold() != saved.login.casefold():
        raise CredentialError(
            "docker-account-conflict: resolve the existing registry account "
            "with Docker before onboarding"
        )
    if previous is not None and secrets.compare_digest(
        previous.secret.value, saved.secret.value
    ):
        return
    try:
        store(program, registry, saved, environment)
        actual = read(program, registry, environment)
        if (
            actual is None
            or actual.login != saved.login
            or not secrets.compare_digest(actual.secret.value, saved.secret.value)
        ):
            raise CredentialError(
                "docker-vault-unavailable: secure-helper readback failed"
            )
    except CredentialError:
        try:
            if previous is None:
                if call(program, "erase", registry + "\n", environment).returncode:
                    raise CredentialError("erase failed")
            else:
                store(program, registry, previous, environment)
        except CredentialError:
            raise CredentialError(
                "docker-state-uncertain: inspect the secure-helper rollback "
                "before retrying"
            ) from None
        raise


def run(
    context: Context, executable: Path, arguments: Sequence[str], now: datetime
) -> int:
    if not arguments or arguments[0] in {
        "version",
        "--version",
        "help",
        "--help",
        "info",
        "ps",
        "context",
    }:
        return children.execute(executable, arguments, context.cwd, context.environment)
    if context.declaration is None:
        return children.execute(executable, arguments, context.cwd, context.environment)
    for credential in context.declaration.credentials:
        endpoints = [
            endpoint
            for endpoint in credential.endpoints
            if endpoint.client == Client.CONTAINER
        ]
        if not endpoints:
            continue
        current = context.service(credential)
        saved = Saved(credential.login, current.authorize(now))
        for endpoint in endpoints:
            registry = urllib.parse.urlsplit(endpoint.url).netloc
            synchronize(helper(registry, context), registry, saved, context.environment)
    return children.execute(
        executable, arguments, context.cwd, private_environment(context.environment)
    )
