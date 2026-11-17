from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import secrets
import subprocess
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from credentials.model import Credential, CredentialError

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Secret:
    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not self.value
            or len(self.value) > 4096
            or any(ord(character) < 33 for character in self.value)
        ):
            raise CredentialError(
                "credential-invalid: submit the credential through onboarding"
            )


class Vault(Protocol):
    def read(self, version: str) -> Secret: ...
    def write(self, version: str, secret: Secret) -> None: ...
    def erase(self, version: str) -> None: ...


def private_environment(source: Mapping[str, str]) -> dict[str, str]:
    excluded = {
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GITHUB_NUGET_PASSWORD",
        "GITHUB_PERSONAL_ACCESS_TOKEN",
        "GITHUB_PAT",
        "NODE_AUTH_TOKEN",
        "NPM_TOKEN",
        "AZURE_DEVOPS_EXT_PAT",
        "SSLKEYLOGFILE",
        "GIT_ASKPASS",
        "SSH_ASKPASS",
        "GIT_EXEC_PATH",
        "GIT_TEMPLATE_DIR",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_CONFIG_PARAMETERS",
    }
    prefixes = (
        "GIT_CONFIG_",
        "GIT_TRACE",
        "GIT_SSL_",
        "GIT_HTTP_",
        "GIT_CURL_",
        "GCM_",
        "GH_",
        "GITHUB_",
        "NUGETPACKAGESOURCECREDENTIALS_",
        "TJ_CREDENTIAL_",
        "NUGET_PLUGIN_DIAGNOSTICS",
        "NUGET_CREDENTIALPROVIDER_LOG",
        "ARTIFACTS_CREDENTIALPROVIDER_LOG",
    )
    return {
        key: value
        for key, value in source.items()
        if key.upper() not in excluded
        and not key.upper().startswith(prefixes)
        and not key.upper().endswith("_PROXY")
    }


def executable(name: str, environment: Mapping[str, str]) -> Path:
    names = (
        (name + ".exe", name + ".cmd", name + ".bat", name)
        if os.name == "nt"
        else (name,)
    )
    for entry in environment.get("PATH", "").split(os.pathsep):
        directory = Path(entry)
        if not entry or not directory.is_absolute():
            continue
        for filename in names:
            candidate = directory / filename
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate.resolve()
    raise CredentialError(f"tool-missing: install {name} and rerun tj install")


class GCMVault:
    def __init__(
        self, credential: Credential, environment: Mapping[str, str], *, windows: bool
    ) -> None:
        backend = (
            "wincredman" if windows else environment.get("GCM_CREDENTIAL_STORE", "")
        )
        if backend not in ({"wincredman"} if windows else {"secretservice", "gpg"}):
            raise CredentialError(
                "vault-unavailable: configure GCM's OS-backed credential store"
            )
        self.credential = credential
        self.executable = executable("git-credential-manager", environment)
        self.environment = private_environment(environment)
        self.environment.update(
            GCM_CREDENTIAL_STORE=backend,
            GCM_NAMESPACE="tj-agents-credentials-" + credential.key,
            GCM_PROVIDER="generic",
            GCM_INTERACTIVE="0",
            GCM_GUI_PROMPT="0",
            GCM_ALLOW_WINDOWSAUTH="false",
            GIT_TERMINAL_PROMPT="0",
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_SYSTEM=os.devnull,
            GIT_CONFIG_COUNT="0",
            GCM_TRACE="0",
            GCM_TRACE_SECRETS="0",
            GCM_TRACE_MSAUTH="0",
            GCM_DEBUG="0",
        )

    def call(self, operation: str, version: str, secret: Secret | None = None) -> str:
        if not re.fullmatch(r"[a-f0-9]{32}", version):
            raise CredentialError("credential-state-invalid: rerun onboarding")
        fields = {
            "protocol": "https",
            "host": "credentials.tj-agents.invalid",
            "path": self.credential.key + "/" + version,
            "username": self.credential.login,
        }
        if secret is not None:
            fields["password"] = secret.value
        payload = "".join(f"{key}={value}\n" for key, value in fields.items()) + "\n"
        with tempfile.TemporaryDirectory(prefix="tj-credentials-vault-") as neutral:
            try:
                result = subprocess.run(
                    [str(self.executable), operation],
                    input=payload,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    cwd=neutral,
                    env=self.environment,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                raise CredentialError(
                    "vault-unavailable: unlock the OS credential store and retry"
                ) from None
        if result.returncode:
            if operation == "get" and (
                "cannot prompt because user interactivity has been disabled"
                in result.stderr.casefold()
            ):
                return ""
            raise CredentialError(
                "vault-unavailable: unlock the OS credential store and retry"
            )
        passwords = [
            line.removeprefix("password=")
            for line in result.stdout.splitlines()
            if line.startswith("password=")
        ]
        if len(passwords) > 1:
            raise CredentialError("vault-unavailable: invalid secure-store response")
        return passwords[0] if passwords else ""

    def read(self, version: str) -> Secret:
        value = self.call("get", version)
        if not value:
            raise CredentialError(f"credential-missing: {self.credential.action}")
        return Secret(value)

    def write(self, version: str, secret: Secret) -> None:
        self.call("store", version, secret)

    def erase(self, version: str) -> None:
        self.call("erase", version)


@dataclass(frozen=True, slots=True)
class Enrollment:
    version: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if (
            not re.fullmatch(r"[a-f0-9]{32}", self.version)
            or self.expires_at.tzinfo is None
        ):
            raise CredentialError("credential-state-invalid: rerun onboarding")

    def status(self, credential: Credential, now: datetime) -> str:
        remaining = (self.expires_at - now).total_seconds()
        if remaining <= 0:
            raise CredentialError(f"credential-expired: {credential.action}")
        return "expiring" if remaining <= 7 * 86400 else "ready"


class State:
    def __init__(self, directory: Path, credential: Credential) -> None:
        self.directory = directory / "credentials" / credential.key
        self.path = self.directory / "active.json"
        self.credential = credential

    def active(self) -> Enrollment | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or set(data) != {"schema_version", "version", "expires_at"}
                or data["schema_version"] != 1
            ):
                raise ValueError
            enrollment = Enrollment(
                data["version"], datetime.fromisoformat(data["expires_at"])
            )
        except (OSError, ValueError, TypeError, KeyError):
            raise CredentialError(
                "credential-state-invalid: inspect the public enrollment metadata"
            ) from None
        return enrollment

    def select(self, enrollment: Enrollment | None) -> None:
        if enrollment is None:
            self.path.unlink(missing_ok=True)
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        descriptor, filename = tempfile.mkstemp(prefix="active-", dir=self.directory)
        temporary = Path(filename)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(
                    {
                        "schema_version": 1,
                        "version": enrollment.version,
                        "expires_at": enrollment.expires_at.astimezone(UTC).isoformat(),
                    },
                    stream,
                )
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        self.directory.mkdir(parents=True, exist_ok=True)
        lock = self.directory / "enrollment.lock"
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise CredentialError(
                "credential-busy: another onboarding owns this account"
            ) from None
        try:
            os.close(descriptor)
            yield
        finally:
            lock.unlink()

    def save(self, vault: Vault, secret: Secret, expires_at: datetime) -> Enrollment:
        candidate = Enrollment(secrets.token_hex(16), expires_at)
        with self.lock():
            previous = self.active()
            try:
                vault.write(candidate.version, secret)
                if not secrets.compare_digest(
                    vault.read(candidate.version).value, secret.value
                ):
                    raise CredentialError(
                        "vault-unavailable: credential readback failed"
                    )
                self.select(candidate)
            except (CredentialError, OSError):
                try:
                    self.select(previous)
                    vault.erase(candidate.version)
                except (CredentialError, OSError):
                    raise CredentialError(
                        "credential-state-uncertain: inspect the secure-store "
                        "rollback before retrying"
                    ) from None
                raise
            if previous is not None:
                try:
                    vault.erase(previous.version)
                except CredentialError:
                    LOGGER.warning(
                        "credential-cleanup-required: remove the previous vault "
                        "version for %s; the new enrollment is active",
                        self.credential.name,
                    )
        return candidate

    def read(self, vault: Vault, now: datetime) -> Secret:
        active = self.active()
        if active is None:
            raise CredentialError(f"credential-missing: {self.credential.action}")
        active.status(self.credential, now)
        return vault.read(active.version)
