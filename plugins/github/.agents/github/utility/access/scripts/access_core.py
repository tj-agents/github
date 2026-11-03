from __future__ import annotations

import contextlib
import hashlib
import html
import http.client
import http.server
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class AccessError(Exception):
    pass


@dataclass(frozen=True)
class Profile:
    organization: str
    login: str
    verify_repository: str
    repositories: tuple[str, ...]
    permissions: Mapping[str, str]
    expires_in: int = 30

    @classmethod
    def load(cls, path: Path) -> Profile:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise AccessError(
                "profile-invalid: read the employer's JSON declaration"
            ) from error
        return cls.from_data(data)

    @classmethod
    def from_data(cls, data: object) -> Profile:
        fields = {
            "schema_version",
            "host",
            "organization",
            "login",
            "verify_repository",
            "repositories",
            "permissions",
            "expires_in",
        }
        if not isinstance(data, dict) or set(data) - fields:
            raise AccessError("profile-invalid: unknown declaration fields")
        if data.get("schema_version") != 1 or data.get("host") != "github.com":
            raise AccessError("profile-invalid: requires schema 1 and github.com")
        for key in ("organization", "login"):
            if not isinstance(data.get(key), str) or not re.fullmatch(
                r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", data[key]
            ):
                raise AccessError(f"profile-invalid: invalid {key}")
        repositories = data.get("repositories")
        if (
            not isinstance(repositories, list)
            or not repositories
            or len(repositories) > 100
        ):
            raise AccessError("profile-invalid: declare 1 to 100 repository names")
        if any(
            not isinstance(repo, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", repo)
            or repo in {".", ".."}
            or repo.endswith(".git")
            for repo in repositories
        ):
            raise AccessError("profile-invalid: invalid repository name")
        if len({repo.casefold() for repo in repositories}) != len(repositories):
            raise AccessError("profile-invalid: duplicate repositories")
        verify = data.get("verify_repository")
        if not isinstance(verify, str) or verify not in repositories:
            raise AccessError(
                "profile-invalid: verification repository must be allowlisted"
            )
        permissions = data.get("permissions")
        allowed = {"contents", "pull_requests", "actions", "issues", "workflows"}
        if (
            not isinstance(permissions, dict)
            or not permissions
            or set(permissions) - allowed
        ):
            raise AccessError("profile-invalid: unsupported or empty permissions")
        if any(
            not isinstance(value, str) or value not in {"read", "write"}
            for value in permissions.values()
        ):
            raise AccessError("profile-invalid: permissions must be read or write")
        if "workflows" in permissions and permissions["workflows"] != "write":
            raise AccessError(
                "profile-invalid: workflows permission supports write only"
            )
        expiry = data.get("expires_in", 30)
        if type(expiry) is not int or not 1 <= expiry <= 90:
            raise AccessError("profile-invalid: expiry must be 1 to 90 days")
        return cls(
            data["organization"],
            data["login"],
            verify,
            tuple(repositories),
            permissions,
            expiry,
        )

    @property
    def key(self) -> str:
        return hashlib.sha256(
            f"github.com/{self.organization}/{self.login}".casefold().encode()
        ).hexdigest()

    def token_url(self) -> str:
        fields = {
            "name": f"Employer {self.organization} {self.login}",
            "description": "Organization-scoped developer access",
            "target_name": self.organization,
            "expires_in": str(self.expires_in),
            **self.permissions,
        }
        return (
            "https://github.com/settings/personal-access-tokens/new?"
            + urllib.parse.urlencode(fields)
        )


def private_environment(source: Mapping[str, str]) -> dict[str, str]:
    excluded = {
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
        "GH_DEBUG",
        "GH_CONFIG_DIR",
        "GITHUB_PERSONAL_ACCESS_TOKEN",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG",
        "GIT_EXEC_PATH",
        "GIT_ASKPASS",
        "GIT_PROXY_COMMAND",
        "GIT_REDIRECT_STDERR",
        "SSH_ASKPASS",
        "GCM_INTERACTIVE",
        "GCM_PROVIDER",
        "GCM_NAMESPACE",
        "GCM_CREDENTIAL_STORE",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_INDEX_FILE",
        "GIT_CEILING_DIRECTORIES",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "SSLKEYLOGFILE",
        "CURL_SSL_BACKEND",
    }
    return {
        key: value
        for key, value in source.items()
        if key.upper() not in excluded
        and not key.upper().startswith(
            (
                "GIT_CONFIG_",
                "GIT_TRACE",
                "GIT_SSL_",
                "GIT_HTTP_",
                "GIT_CURL_",
                "GCM_",
                "GH_HOST",
            )
        )
        and not key.upper().endswith("_PROXY")
    }


class Vault(Protocol):
    def read(self, version: str) -> str: ...
    def write(self, version: str, token: str) -> None: ...
    def erase(self, version: str) -> None: ...


class GCMVault:
    def __init__(
        self, profile: Profile, environment: Mapping[str, str], git: Path
    ) -> None:
        self.profile = profile
        if not git.is_absolute() or not git.is_file():
            raise AccessError(
                "vault-unavailable: selected Git executable is unavailable"
            )
        git = git.resolve()
        self.environment = private_environment(environment)
        backend = (
            "wincredman"
            if sys.platform == "win32"
            else environment.get("GCM_CREDENTIAL_STORE", "")
        )
        if backend not in {"wincredman", "secretservice", "gpg"} or (
            sys.platform != "win32" and backend == "wincredman"
        ):
            raise AccessError(
                "vault-unavailable: configure GCM Secret Service or GPG/pass"
            )
        executable = shutil.which(
            "git-credential-manager", path=environment.get("PATH")
        )
        if not executable:
            raise AccessError("vault-unavailable: install Git Credential Manager")
        self.executable = executable
        path = next(
            (value for key, value in self.environment.items() if key.upper() == "PATH"),
            "",
        )
        for key in tuple(self.environment):
            if key.upper() == "PATH":
                self.environment.pop(key)
        self.environment.update(
            {
                "PATH": str(git.parent) + (os.pathsep + path if path else ""),
                "GCM_CREDENTIAL_STORE": backend,
                "GCM_NAMESPACE": "tj-agents-employer-" + profile.key,
                "GCM_PROVIDER": "generic",
                "GCM_INTERACTIVE": "0",
                "GCM_GUI_PROMPT": "0",
                "GCM_ALLOW_WINDOWSAUTH": "false",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_CONFIG_COUNT": "0",
                "GCM_TRACE": "0",
                "GCM_TRACE_SECRETS": "0",
                "GCM_TRACE_MSAUTH": "0",
                "GCM_DEBUG": "0",
                "GCM_AUTODETECT_TIMEOUT": "0",
                "GIT_TRACE": "0",
                "GIT_TRACE2": "0",
                "GIT_TRACE2_EVENT": "0",
                "GIT_TRACE2_PERF": "0",
            }
        )

    def _call(self, operation: str, version: str, token: str | None = None) -> str:
        if not re.fullmatch(r"[a-f0-9]{32}", version):
            raise AccessError("vault-invalid: invalid credential version")
        fields = {
            "protocol": "https",
            "host": "github.com",
            "path": f"{self.profile.organization}/{version}",
            "username": self.profile.login,
        }
        if token is not None:
            fields["password"] = token
        payload = "".join(f"{key}={value}\n" for key, value in fields.items()) + "\n"
        with tempfile.TemporaryDirectory(prefix="github-access-vault-") as directory:
            try:
                result = subprocess.run(
                    [self.executable, operation],
                    input=payload,
                    text=True,
                    capture_output=True,
                    env=self.environment,
                    cwd=directory,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise AccessError(
                    "vault-unavailable: unlock the secure backend and retry"
                ) from error
        if result.returncode:
            raise AccessError(
                "vault-unavailable: secure operation failed; unlock the backend"
            )
        for line in result.stdout.splitlines():
            if line.startswith("password="):
                return line.removeprefix("password=")
        return ""

    def read(self, version: str) -> str:
        token = self._call("get", version)
        validate_token(token)
        return token

    def write(self, version: str, token: str) -> None:
        self._call("store", version, token)

    def erase(self, version: str) -> None:
        self._call("erase", version)

    def check(self) -> None:
        version, probe = secrets.token_hex(16), secrets.token_hex(32)
        try:
            self.write(version, probe)
            if not secrets.compare_digest(self._call("get", version), probe):
                raise AccessError(
                    "vault-unavailable: secure backend did not round-trip its probe"
                )
        finally:
            self.erase(version)


def validate_token(token: str) -> None:
    if not re.fullmatch(r"github_pat_[A-Za-z0-9_]{40,245}", token):
        raise AccessError(
            "token-invalid: submit a newly created fine-grained PAT in the local page"
        )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def response_error(status: int, headers: Mapping[str, str]) -> AccessError:
    headers = {key.lower(): value for key, value in headers.items()}
    if status == 401:
        return AccessError(
            "authentication-invalid: rotate the revoked, expired or invalid credential"
        )
    if status == 429 or (
        status == 403
        and (headers.get("x-ratelimit-remaining") == "0" or headers.get("retry-after"))
    ):
        return AccessError("rate-limited: wait for GitHub's rate-limit reset and retry")
    if status == 403 and "required" in headers.get("x-github-sso", "").lower():
        return AccessError("sso-required: authorize employer SSO in GitHub and retry")
    if status in {403, 404}:
        return AccessError(
            "access-denied: check org approval, repository selection and permissions"
        )
    return AccessError(
        "github-unavailable: GitHub rejected the readiness probe; retry later"
    )


class GitHubAPI:
    def __init__(self, token: str) -> None:
        self.token = token
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect()
        )

    def read(self, endpoint: str) -> Any:
        if not re.fullmatch(
            r"/(?:user|repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_/?=&.-]+)?)",
            endpoint,
        ):
            raise AccessError("probe-invalid: invalid GitHub endpoint")
        request = urllib.request.Request(
            "https://api.github.com" + endpoint,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "User-Agent": "tj-agents-github-access",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with self.opener.open(request, timeout=20) as response:
                content = response.read(1024 * 1024 + 1)
        except urllib.error.HTTPError as error:
            raise response_error(error.code, dict(error.headers.items())) from None
        except (OSError, urllib.error.URLError, http.client.HTTPException) as error:
            raise AccessError(
                "network-unavailable: check connectivity to api.github.com"
            ) from error
        if len(content) > 1024 * 1024:
            raise AccessError("probe-invalid: GitHub returned an oversized response")
        try:
            return json.loads(content)
        except ValueError as error:
            raise AccessError("probe-invalid: GitHub returned invalid JSON") from error


class ReadAPI(Protocol):
    def read(self, endpoint: str) -> Any: ...


def verify(profile: Profile, api: ReadAPI) -> list[str]:
    user = api.read("/user")
    if (
        not isinstance(user, dict)
        or str(user.get("login", "")).casefold() != profile.login.casefold()
    ):
        raise AccessError("identity-mismatch: use the declared employer GitHub account")
    verified = ["identity"]
    for repository in profile.repositories:
        root = f"/repos/{profile.organization}/{repository}"
        value = api.read(root)
        if (
            not isinstance(value, dict)
            or str(value.get("full_name", "")).casefold()
            != root.removeprefix("/repos/").casefold()
        ):
            raise AccessError(
                "repository-mismatch: GitHub returned a different repository"
            )
        if repository == profile.verify_repository and value.get("private") is not True:
            raise AccessError(
                "verification-invalid: choose a private employer verification repo"
            )
        verified.append(f"repository:{repository}")
        probes = {
            "contents": "/branches?per_page=1",
            "pull_requests": "/pulls?state=open&per_page=1",
            "actions": "/actions/runs?per_page=1",
            "issues": "/issues?per_page=1",
        }
        for permission, endpoint in probes.items():
            if permission in profile.permissions:
                api.read(root + endpoint)
                verified.append(f"{permission}:read:{repository}")
    return verified


class State:
    def __init__(self, root: Path, profile: Profile) -> None:
        self.directory = root / "github-access" / profile.key
        self.path = self.directory / "active.json"

    def active(self) -> str | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise AccessError(
                "state-invalid: restore the public credential version record"
            ) from error
        version = data.get("version") if isinstance(data, dict) else None
        if not isinstance(version, str) or not re.fullmatch(r"[a-f0-9]{32}", version):
            raise AccessError("state-invalid: invalid credential version record")
        return version

    def select(self, version: str | None) -> None:
        if version is None:
            self.path.unlink(missing_ok=True)
            return
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, name = tempfile.mkstemp(prefix="active-", dir=self.directory)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"schema_version": 1, "version": version}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = self.directory / "rotation.lock"
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as error:
            raise AccessError(
                "rotation-busy: another onboarding owns this org; inspect its process"
            ) from error
        try:
            os.close(descriptor)
            yield
        finally:
            lock.unlink()


def rotate(
    profile: Profile,
    state: State,
    vault: Vault,
    token: str,
    api_factory: Callable[[str], ReadAPI] = GitHubAPI,
    after_select: Callable[[], None] | None = None,
) -> list[str]:
    validate_token(token)
    with state.lock():
        previous = state.active()
        evidence = verify(profile, api_factory(token))
        candidate = secrets.token_hex(16)
        vault.write(candidate, token)
        try:
            if not secrets.compare_digest(vault.read(candidate), token):
                raise AccessError(
                    "vault-unavailable: stored candidate verification failed"
                )
            state.select(candidate)
            if after_select is not None:
                after_select()
        except (AccessError, OSError):
            state.select(previous)
            vault.erase(candidate)
            raise
    return evidence


class TokenServer(http.server.HTTPServer):
    token: str | None = None
    nonce: str
    profile: Profile

    def get_request(self) -> tuple[Any, Any]:
        connection, address = super().get_request()
        connection.settimeout(3)
        return connection, address

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass


class TokenHandler(http.server.BaseHTTPRequestHandler):
    server: TokenServer

    def log_message(self, message: str, *args: Any) -> None:
        pass

    def _send(self, status: int, body: str) -> None:
        content = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; form-action 'self'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(content)

    def _allowed(self) -> bool:
        host = f"127.0.0.1:{self.server.server_port}"
        return self.headers.get_all("Host") == [host] and secrets.compare_digest(
            self.path, "/" + self.server.nonce
        )

    def do_GET(self) -> None:
        if not self._allowed():
            self._send(404, "Not found")
            return
        profile = self.server.profile
        owner, login = html.escape(profile.organization), html.escape(profile.login)
        repositories = ", ".join(html.escape(repo) for repo in profile.repositories)
        self._send(
            200,
            f"<!doctype html><title>Employer GitHub access</title><h1>{owner}</h1>"
            f"<p>Use GitHub account {login}. Select resource owner {owner} "
            "and only the declared repositories.</p>"
            f"<p>Repositories: {repositories}</p>"
            '<form method="post"><label>Fine-grained token '
            '<input name="token" type="password" autocomplete="off" required>'
            '</label><p><label><input type="checkbox" name="owner_confirmed" '
            f'value="yes" required>I confirmed resource owner {owner} '
            "and selected repositories in GitHub.</label></p>"
            "<button>Validate and save securely</button></form>",
        )

    def do_POST(self) -> None:
        origin = f"http://127.0.0.1:{self.server.server_port}"
        if not self._allowed() or self.headers.get_all("Origin") != [origin]:
            self._send(403, "Submission rejected")
            return
        lengths = self.headers.get_all("Content-Length") or []
        if (
            len(lengths) != 1
            or not lengths[0].isdigit()
            or not 1 <= int(lengths[0]) <= 4096
            or self.headers.get("Content-Type") != "application/x-www-form-urlencoded"
            or self.headers.get("Transfer-Encoding") is not None
        ):
            self._send(400, "Invalid submission")
            return
        self.connection.settimeout(3)
        try:
            body = self.rfile.read(int(lengths[0])).decode("ascii")
            fields = urllib.parse.parse_qs(body, strict_parsing=True, max_num_fields=2)
            if (
                set(fields) != {"token", "owner_confirmed"}
                or fields["owner_confirmed"] != ["yes"]
                or len(fields["token"]) != 1
            ):
                raise ValueError("Invalid fields")
            token = fields["token"][0]
            validate_token(token)
        except (ValueError, OSError, AccessError):
            self._send(
                400, "Submit a fine-grained token and confirm its resource owner"
            )
            return
        self.server.token = token
        self._send(
            200,
            "<!doctype html><title>Credential received</title>"
            "<p>Credential received. Check the terminal for validation results.</p>",
        )


def receive_token(profile: Profile, timeout: float = 900) -> str:
    with TokenServer(("127.0.0.1", 0), TokenHandler) as server:
        server.nonce, server.profile = secrets.token_urlsafe(32), profile
        server.timeout = 1
        local_url = f"http://127.0.0.1:{server.server_port}/{server.nonce}"
        print(
            f"Create a fine-grained token for {profile.organization}; "
            "submit it in the local page.",
            flush=True,
        )
        print("GitHub: " + profile.token_url(), flush=True)
        print("Local page: " + local_url, flush=True)
        webbrowser.open(profile.token_url())
        webbrowser.open(local_url)
        deadline = time.monotonic() + timeout
        while server.token is None and time.monotonic() < deadline:
            server.handle_request()
        if server.token is None:
            raise AccessError(
                "onboarding-timeout: no credential received; run onboarding again"
            )
        return server.token
