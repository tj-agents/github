"""Act as a GitHub App: sign its JWT, read its installations and mint their tokens.

The App's covered accounts are exactly its installations, read from GitHub. The only
local state is the enrolled private key, GitHub's public App descriptor and caches.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from gate_error import GateError
from gate_platform import Platform
from gate_private import read_private, write_private

API = "https://api.github.com"
TIMEOUT_SECONDS = 30
TOKEN_MARGIN_SECONDS = 300
JWT_BACKDATE_SECONDS = 60
JWT_LIFETIME_SECONDS = 540
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_KEY_BYTES = 32 * 1024
TOKEN = re.compile(r"[A-Za-z0-9_]{20,255}")
LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class AppError(GateError):
    pass


class GitHubError(AppError):
    def __init__(self, message: str, status: int | None) -> None:
        super().__init__(message)
        self.status = status


class Fetch(Protocol):
    def __call__(self, path: str, bearer: str | None, method: str) -> object: ...


class Sign(Protocol):
    def __call__(self, key: Path, message: bytes) -> bytes: ...


def fetch_github(path: str, bearer: str | None, method: str) -> object:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    request = urllib.request.Request(API + path, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            content = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise GitHubError(
            f"GitHub {method} {path} failed (HTTP {error.code})", error.code
        ) from error
    except OSError as error:
        raise GitHubError(
            f"GitHub {method} {path} could not be reached", None
        ) from error
    if len(content) > MAX_RESPONSE_BYTES:
        raise GitHubError(f"GitHub {method} {path} returned too much data", None)
    try:
        return json.loads(content)
    except ValueError as error:
        raise GitHubError(
            f"GitHub {method} {path} returned invalid JSON", None
        ) from error


def find_openssl(path_value: str) -> Path:
    found = shutil.which("openssl", path=path_value)
    if found:
        return Path(found)
    git = shutil.which("git", path=path_value)
    if git:
        for root in Path(git).resolve().parents[1:3]:
            for candidate in (
                root / "mingw64/bin/openssl.exe",
                root / "usr/bin/openssl",
            ):
                if candidate.is_file():
                    return candidate
    raise AppError("openssl is required to sign as the GitHub App; install OpenSSL")


@dataclass(frozen=True, slots=True)
class OpenSSL:
    """Sign with openssl, found on the given search path only when first needed."""

    search_path: str

    def __call__(self, key: Path, message: bytes) -> bytes:
        try:
            result = subprocess.run(
                [str(find_openssl(self.search_path)), "dgst", "-sha256", "-sign", key],
                input=message,
                capture_output=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
                creationflags=NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise AppError("openssl could not run") from error
        if result.returncode or not result.stdout:
            raise AppError("openssl could not sign with the enrolled GitHub App key")
        return result.stdout


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@dataclass(frozen=True, slots=True)
class AppDescriptor:
    """GitHub's public description of an App, from `GET /apps/<slug>`."""

    slug: str
    app_id: int
    client_id: str
    owner: str

    @classmethod
    def from_github(cls, value: object, slug: str) -> AppDescriptor:
        if not isinstance(value, Mapping):
            raise AppError(f"GitHub returned an invalid descriptor for App {slug}")
        app_id, client_id, owner = (
            value.get("id"),
            value.get("client_id"),
            value.get("owner"),
        )
        login = owner.get("login") if isinstance(owner, Mapping) else None
        if (
            value.get("slug") != slug
            or type(app_id) is not int
            or app_id <= 0
            or not isinstance(client_id, str)
            or not client_id.isascii()
            or not client_id
            or not isinstance(login, str)
            or not LOGIN.fullmatch(login)
        ):
            raise AppError(f"GitHub returned an invalid descriptor for App {slug}")
        return cls(slug, app_id, client_id, login)

    def to_json(self) -> bytes:
        return json.dumps(
            {
                "slug": self.slug,
                "id": self.app_id,
                "client_id": self.client_id,
                "owner": {"login": self.owner},
            }
        ).encode()

    @property
    def install_url(self) -> str:
        return f"https://github.com/apps/{self.slug}/installations/new"

    def jwt(self, key: Path, now: float, sign: Sign) -> str:
        issued = int(now)
        header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        claims = {
            "iat": issued - JWT_BACKDATE_SECONDS,
            "exp": issued + JWT_LIFETIME_SECONDS,
            "iss": self.client_id,
        }
        signing = f"{header}.{b64url(json.dumps(claims).encode())}"
        return f"{signing}.{b64url(sign(key, signing.encode()))}"


@dataclass(frozen=True, slots=True)
class GitHubApp:
    """One App's enrolment on this machine and the GitHub calls that use it."""

    slug: str
    directory: Path
    platform: Platform
    fetch: Fetch
    sign: Sign

    @property
    def key(self) -> Path:
        return self.directory / "key.pem"

    @property
    def descriptor_path(self) -> Path:
        return self.directory / "app.json"

    @property
    def installations_path(self) -> Path:
        return self.directory / "installations.json"

    @property
    def tokens(self) -> Path:
        return self.directory / "tokens"

    def onboarding_hint(self) -> str:
        return (
            f"GitHub App {self.slug} is not enrolled on this machine; run "
            f"`github_gate.py onboard app --app {self.slug} --key <private-key.pem>`"
        )

    def descriptor(self) -> AppDescriptor:
        if not self.descriptor_path.is_file() or not self.key.is_file():
            raise AppError(self.onboarding_hint())
        try:
            value = json.loads(read_private(self.descriptor_path, self.platform))
        except ValueError as error:
            raise AppError(self.onboarding_hint()) from error
        return AppDescriptor.from_github(value, self.slug)

    def verify_enrolled(self) -> AppDescriptor:
        app = self.descriptor()
        self.platform.verify_private(self.key, directory=False)
        return app

    def bearer(self, app: AppDescriptor, now: float) -> str:
        self.platform.verify_private(self.key, directory=False)
        return app.jwt(self.key, now, self.sign)

    def installations(
        self, app: AppDescriptor, now: float, *, refresh: bool
    ) -> dict[str, int]:
        """Map each covered account (lowercase login) to its installation id."""
        if not refresh and self.installations_path.is_file():
            try:
                cached = json.loads(
                    read_private(self.installations_path, self.platform)
                )
            except ValueError:
                cached = None
            if isinstance(cached, dict) and all(
                isinstance(login, str) and type(number) is int
                for login, number in cached.items()
            ):
                return cached
        listed = self.fetch(
            "/app/installations?per_page=100", self.bearer(app, now), "GET"
        )
        found: dict[str, int] = {}
        if not isinstance(listed, list):
            raise AppError(f"GitHub returned invalid installations for App {self.slug}")
        for item in listed:
            account = item.get("account") if isinstance(item, Mapping) else None
            login = account.get("login") if isinstance(account, Mapping) else None
            number = item.get("id") if isinstance(item, Mapping) else None
            if not isinstance(login, str) or type(number) is not int:
                raise AppError(
                    f"GitHub returned invalid installations for App {self.slug}"
                )
            found[login.lower()] = number
        write_private(
            self.installations_path, json.dumps(found).encode(), self.platform
        )
        return found

    def covered_accounts(self, now: float) -> list[str]:
        return sorted(self.installations(self.descriptor(), now, refresh=True))

    def cached_token(self, cache: Path, now: float) -> str | None:
        if not cache.is_file():
            return None
        try:
            cached = json.loads(read_private(cache, self.platform))
        except ValueError:
            return None
        if not isinstance(cached, dict):
            return None
        token, expires = cached.get("token"), cached.get("expires")
        if (
            isinstance(token, str)
            and TOKEN.fullmatch(token)
            and isinstance(expires, int | float)
            and expires - TOKEN_MARGIN_SECONDS > now
        ):
            return token
        return None

    def installation_token(self, owner: str, now: float) -> str:
        """A token for the installation covering `owner`; refuse uncovered owners."""
        app = self.descriptor()
        account = owner.lower()
        covered = self.installations(app, now, refresh=False)
        if account not in covered:
            covered = self.installations(app, now, refresh=True)
        if account not in covered:
            raise AppError(
                f"{owner} is not covered by GitHub App {self.slug}, and agents never "
                f"use the human's credentials; install the App at {app.install_url}"
            )
        cache = self.tokens / f"{account}.json"
        cached = self.cached_token(cache, now)
        if cached is not None:
            return cached
        try:
            issued = self.fetch(
                f"/app/installations/{covered[account]}/access_tokens",
                self.bearer(app, now),
                "POST",
            )
        except GitHubError:
            self.installations_path.unlink(missing_ok=True)
            raise
        token = issued.get("token") if isinstance(issued, Mapping) else None
        expires_at = issued.get("expires_at") if isinstance(issued, Mapping) else None
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise AppError(f"GitHub returned an invalid token for {owner}")
        if not isinstance(expires_at, str):
            raise AppError(f"GitHub returned no token expiry for {owner}")
        try:
            expires = datetime.fromisoformat(expires_at).timestamp()
        except ValueError as error:
            raise AppError(
                f"GitHub returned an invalid token expiry for {owner}"
            ) from error
        write_private(
            cache,
            json.dumps({"token": token, "expires": expires}).encode(),
            self.platform,
        )
        return token

    def enrol(self, key: bytes, now: float) -> AppDescriptor:
        """Verify `key` signs as this App, then make it this machine's enrolment."""
        if (
            len(key) > MAX_KEY_BYTES
            or not key.startswith(b"-----BEGIN ")
            or b"PRIVATE KEY-----" not in key
        ):
            raise AppError("the file is not a PEM private key")
        app = AppDescriptor.from_github(
            self.fetch(f"/apps/{self.slug}", None, "GET"), self.slug
        )
        candidate = self.directory / f".candidate.{os.getpid()}.pem"
        write_private(candidate, key, self.platform)
        try:
            actual = self.fetch("/app", app.jwt(candidate, now, self.sign), "GET")
            if (
                not isinstance(actual, Mapping)
                or actual.get("id") != app.app_id
                or actual.get("slug") != self.slug
            ):
                raise AppError(f"the key does not authenticate GitHub App {self.slug}")
            candidate.replace(self.key)
        finally:
            candidate.unlink(missing_ok=True)
        write_private(self.descriptor_path, app.to_json(), self.platform)
        self.installations_path.unlink(missing_ok=True)
        if self.tokens.is_dir():
            for cached in self.tokens.iterdir():
                cached.unlink()
        return app


class AppFactory(Protocol):
    """Build the App a repository declares; injected so tests can fake GitHub."""

    def __call__(self, slug: str) -> GitHubApp: ...


@dataclass(frozen=True, slots=True)
class Apps:
    """The App enrolments kept under one gate home."""

    home: Path
    platform: Platform
    fetch: Fetch
    sign: Sign

    def __call__(self, slug: str) -> GitHubApp:
        return GitHubApp(
            slug, self.home / "apps" / slug, self.platform, self.fetch, self.sign
        )
