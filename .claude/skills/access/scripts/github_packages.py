from __future__ import annotations

import base64
import hashlib
import html
import http.client
import json
import os
import re
import secrets
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from access_core import (
    AccessError,
    NoRedirect,
    TokenPage,
    private_environment,
    receive_token_page,
    validate_token,
)
from github_tokens import TokenKind


@dataclass(frozen=True, slots=True)
class PackageTarget:
    login: str
    image: str

    def __post_init__(self) -> None:
        if not re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", self.login
        ):
            raise AccessError("login-invalid: select the expected GitHub login")
        if not re.fullmatch(
            r"ghcr\.io/[a-z0-9]+(?:[._-][a-z0-9]+)*"
            r"(?:/[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*)+@sha256:[a-f0-9]{64}",
            self.image,
        ):
            raise AccessError(
                "image-invalid: use ghcr.io/owner/image@sha256:"
                "<64 lowercase hex digits>"
            )

    @property
    def repository(self) -> str:
        return self.image.removeprefix("ghcr.io/").split("@", 1)[0]

    @property
    def digest(self) -> str:
        return self.image.split("@", 1)[1]

    def token_page(self) -> TokenPage:
        return TokenPage(
            "GitHub package access",
            f"Account: {html.escape(self.login)}. Image: {html.escape(self.image)}",
            "Create a classic token with only read:packages selected. "
            "Choose its expiry. "
            "If the package owner requires SSO, authorize that owner in GitHub. "
            "Paste the token here; it will be checked before replacing "
            "the GHCR credential.",
            "I selected only read:packages for the displayed GitHub account.",
            TokenKind.CLASSIC,
            "https://github.com/settings/tokens/new?"
            + urllib.parse.urlencode(
                {
                    "description": "Read GitHub container packages",
                    "scopes": "read:packages",
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class Response:
    content: bytes
    headers: Mapping[str, str]


class Transport(Protocol):
    def read(self, url: str, authorization: str, accept: str) -> Response: ...


class HTTPS:
    def __init__(self) -> None:
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect()
        )

    def read(self, url: str, authorization: str, accept: str) -> Response:
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": authorization,
                "Accept": accept,
                "User-Agent": "tj-agents-github-access",
            },
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                content = response.read(4 * 1024 * 1024 + 1)
                headers = {
                    key.lower(): value for key, value in response.headers.items()
                }
        except urllib.error.HTTPError as error:
            if error.code in {401, 403, 404}:
                raise AccessError(
                    "package-access-denied: check token expiry, package access "
                    "and owner SSO"
                ) from None
            raise AccessError(
                "package-probe-failed: retry the GitHub registry check"
            ) from None
        except (OSError, urllib.error.URLError, http.client.HTTPException) as error:
            raise AccessError(
                "network-unavailable: check GitHub and GHCR connectivity"
            ) from error
        if len(content) > 4 * 1024 * 1024:
            raise AccessError("package-probe-invalid: oversized registry response")
        return Response(content, headers)


def json_object(content: str | bytes) -> dict[str, object]:
    try:
        data: object = json.loads(content)
    except ValueError as error:
        raise AccessError("package-response-invalid: expected JSON object") from error
    if not isinstance(data, dict) or any(not isinstance(key, str) for key in data):
        raise AccessError("package-response-invalid: expected JSON object")
    return data


def verify(target: PackageTarget, token: str, transport: Transport) -> None:
    validate_token(token, TokenKind.CLASSIC)
    response = transport.read(
        "https://api.github.com/user", "Bearer " + token, "application/vnd.github+json"
    )
    identity = json_object(response.content).get("login")
    if not isinstance(identity, str) or identity.casefold() != target.login.casefold():
        raise AccessError(
            "identity-mismatch: create a token for the selected GitHub account"
        )
    scopes = {
        scope.strip() for scope in response.headers.get("x-oauth-scopes", "").split(",")
    }
    if scopes != {"read:packages"}:
        raise AccessError(
            "scope-mismatch: create a classic token with only read:packages"
        )
    authorization = base64.b64encode(f"{target.login}:{token}".encode()).decode("ascii")
    registry = transport.read(
        "https://ghcr.io/token?"
        + urllib.parse.urlencode(
            {"service": "ghcr.io", "scope": f"repository:{target.repository}:pull"}
        ),
        "Basic " + authorization,
        "application/json",
    )
    bearer = json_object(registry.content).get("token")
    if not isinstance(bearer, str) or not bearer or "\r" in bearer or "\n" in bearer:
        raise AccessError("package-probe-invalid: registry did not issue a pull token")
    manifest = transport.read(
        f"https://ghcr.io/v2/{target.repository}/manifests/{target.digest}",
        "Bearer " + bearer,
        ", ".join(
            (
                "application/vnd.oci.image.index.v1+json",
                "application/vnd.oci.image.manifest.v1+json",
                "application/vnd.docker.distribution.manifest.list.v2+json",
                "application/vnd.docker.distribution.manifest.v2+json",
            )
        ),
    )
    if "sha256:" + hashlib.sha256(manifest.content).hexdigest() != target.digest:
        raise AccessError(
            "package-digest-mismatch: registry returned a different manifest"
        )
    if json_object(manifest.content).get("schemaVersion") != 2:
        raise AccessError("package-probe-invalid: expected a container image manifest")


class PackageStore(Protocol):
    def check(self, login: str) -> None: ...
    def read(self, login: str) -> str | None: ...
    def write(self, login: str, token: str) -> None: ...
    def erase(self) -> None: ...


def helper_executable(helper: str, search_path: str) -> str:
    for entry in search_path.split(os.pathsep):
        directory = Path(entry)
        if not entry or not directory.is_absolute():
            continue
        executable = shutil.which(str(directory / ("docker-credential-" + helper)))
        if executable is not None and Path(executable).is_absolute():
            return executable
    raise AccessError(
        "secure-store-unavailable: install the configured Docker helper "
        "in an absolute PATH directory"
    )


class DockerStore:
    def __init__(
        self, config: Path, environment: Mapping[str, str], platform: str
    ) -> None:
        try:
            data = json_object((config / "config.json").read_text(encoding="utf-8"))
        except (OSError, AccessError) as error:
            raise AccessError(
                "secure-store-unavailable: configure Docker's secure credential helper"
            ) from error
        helpers = data.get("credHelpers", {})
        if not isinstance(helpers, dict):
            raise AccessError(
                "secure-store-unavailable: invalid Docker credential helpers"
            )
        helper = helpers.get("ghcr.io", data.get("credsStore"))
        supported = {
            "win32": {"desktop", "wincred"},
            "linux": {"desktop", "desktop.exe", "secretservice", "pass"},
            "darwin": {"desktop", "osxkeychain"},
        }
        if not isinstance(helper, str) or helper not in supported.get(platform, set()):
            raise AccessError(
                "secure-store-unavailable: select an OS-backed Docker credential helper"
            )
        self.executable = helper_executable(helper, environment.get("PATH", ""))
        self.environment = private_environment(environment)

    def _run(self, operation: str, value: str) -> str:
        try:
            result = subprocess.run(
                [self.executable, operation],
                input=value,
                text=True,
                capture_output=True,
                env=self.environment,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise AccessError(
                "secure-store-unavailable: unlock the Docker credential store"
            ) from error
        if result.returncode:
            raise AccessError(
                "secure-store-unavailable: unlock the Docker credential store"
            )
        return result.stdout

    def check(self, login: str) -> None:
        stored = json_object(self._run("list", "")).get("ghcr.io")
        if stored is not None and (
            not isinstance(stored, str) or stored.casefold() != login.casefold()
        ):
            raise AccessError(
                "credential-conflict: GHCR is configured for another account"
            )

    def read(self, login: str) -> str | None:
        if "ghcr.io" not in json_object(self._run("list", "")):
            return None
        value = json_object(self._run("get", "ghcr.io"))
        username, token = value.get("Username"), value.get("Secret")
        if not isinstance(username, str) or username.casefold() != login.casefold():
            raise AccessError(
                "identity-mismatch: stored GHCR account differs from selected login"
            )
        if not isinstance(token, str):
            raise AccessError("credential-missing: run packages onboard")
        validate_token(token, TokenKind.CLASSIC)
        return token

    def write(self, login: str, token: str) -> None:
        self._run(
            "store",
            json.dumps({"ServerURL": "ghcr.io", "Username": login, "Secret": token}),
        )

    def erase(self) -> None:
        self._run("erase", "ghcr.io")


def onboard(
    target: PackageTarget, token: str, store: PackageStore, transport: Transport
) -> None:
    verify(target, token, transport)
    store.check(target.login)
    previous = store.read(target.login)
    try:
        store.write(target.login, token)
        stored = store.read(target.login)
        if stored is None or not secrets.compare_digest(stored, token):
            raise AccessError(
                "credential-write-failed: secure helper readback differed"
            )
    except AccessError:
        try:
            if previous is None:
                store.erase()
            else:
                store.write(target.login, previous)
            if store.read(target.login) != previous:
                raise AccessError(
                    "credential-rollback-failed: secure helper readback differed"
                )
        except AccessError:
            raise AccessError(
                "credential-rollback-failed: prior GHCR credential "
                "could not be restored; "
                "unlock the secure helper and rerun onboarding"
            ) from None
        raise AccessError(
            "credential-write-failed: prior GHCR credential restored; retry onboarding"
        ) from None


def run(
    target: PackageTarget, store: PackageStore, *, onboarding: bool
) -> dict[str, str]:
    store.check(target.login)
    transport = HTTPS()
    if onboarding:
        token = receive_token_page(target.token_page())
        onboard(target, token, store, transport)
    else:
        saved_token = store.read(target.login)
        if saved_token is None:
            raise AccessError("credential-missing: run packages onboard")
        verify(target, saved_token, transport)
    return {
        "status": "package-access-verified",
        "login": target.login,
        "image": target.image,
        "scope": "read:packages",
        "credential_store": "docker-secure-helper",
        "evidence": "identity-scope-immutable-manifest",
    }
