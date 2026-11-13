import contextlib
import http.client
import json
import os
import shutil
import socket
import threading
import urllib.parse
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import pytest_socket
from access_core import (
    TOKEN_FORMS,
    AccessError,
    GCMVault,
    GitHubAPI,
    Profile,
    State,
    TokenHandler,
    TokenServer,
    private_environment,
    response_error,
    rotate,
    validate_token,
    verify,
)
from github_tokens import TokenKind

TOKEN = "github_pat_" + "a" * 70
PREVIOUS = "github_pat_" + "b" * 70
VERSION = "a" * 32


@pytest.fixture
def declaration() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "host": "github.com",
        "organization": "employer",
        "login": "developer",
        "verify_repository": "private-project",
        "repositories": ["private-project"],
        "permissions": {
            "contents": "write",
            "pull_requests": "write",
            "actions": "read",
        },
    }


@pytest.fixture
def profile(tmp_path: Path, declaration: dict[str, Any]) -> Profile:
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(declaration), encoding="utf-8")
    return Profile.load(path)


class MemoryVault:
    def __init__(self) -> None:
        self.values = {VERSION: PREVIOUS}
        self.writes: list[str] = []
        self.corrupt = False

    def read(self, version: str) -> str:
        return PREVIOUS if self.corrupt else self.values[version]

    def write(self, version: str, token: str) -> None:
        self.writes.append(version)
        self.values[version] = token

    def erase(self, version: str) -> None:
        self.values.pop(version, None)


class FakeAPI:
    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        self.calls: list[str] = []
        self.login = profile.login
        self.private = True
        self.fail: str | None = None

    def read(self, endpoint: str) -> Any:
        self.calls.append(endpoint)
        if endpoint == self.fail:
            raise AccessError("access-denied: organization approval required")
        if endpoint == "/user":
            return {"login": self.login}
        if endpoint.count("/") == 3:
            return {
                "full_name": endpoint.removeprefix("/repos/"),
                "private": self.private,
            }
        return []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("host", "evil.example"),
        ("organization", "employer\npassword=bad"),
        ("repositories", ["../other"]),
        ("repositories", ["project.git"]),
        ("repositories", ["private-project", "PRIVATE-PROJECT"]),
        ("verify_repository", "not-selected"),
        ("permissions", {"admin": "write"}),
        ("permissions", {"contents": "admin"}),
        ("permissions", {"workflows": "read"}),
        ("permissions", {"contents": ["write"]}),
        ("expires_in", True),
        ("expires_in", 365),
        ("unexpected", "value"),
        ("repositories", ["*", "private-project"]),
        ("token_kind", "oauth"),
        ("token_kind", []),
    ],
)
def test_profile_rejects_invalid_security_declaration(
    tmp_path: Path,
    declaration: dict[str, Any],
    key: str,
    value: Any,
) -> None:
    declaration[key] = value
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(declaration), encoding="utf-8")
    with pytest.raises(AccessError, match="profile-invalid"):
        Profile.load(path)


def test_profile_opens_org_specific_fine_grained_form(profile: Profile) -> None:
    url = urllib.parse.urlsplit(profile.token_url())
    fields = urllib.parse.parse_qs(url.query)
    assert url.netloc == "github.com"
    assert url.path == "/settings/personal-access-tokens/new"
    assert fields["target_name"] == ["employer"]
    assert fields["contents"] == ["write"]
    assert fields["expires_in"] == ["30"]
    assert "token" not in fields


def test_profile_accepts_private_organization_configuration_repository(
    tmp_path: Path,
    declaration: dict[str, Any],
) -> None:
    declaration["repositories"] = [".github-private"]
    declaration["verify_repository"] = ".github-private"
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(declaration), encoding="utf-8")
    assert Profile.load(path).repositories == (".github-private",)


@pytest.mark.parametrize(
    "token", ["ghp_" + "a" * 40, "bot-token", "", TOKEN + "\npassword=evil"]
)
def test_token_rejects_malformed_or_injected_credentials(token: str) -> None:
    with pytest.raises(AccessError, match="token-invalid"):
        validate_token(token)


def test_classic_form_requests_repository_and_selected_workflow_scopes(
    profile: Profile,
) -> None:
    selected = replace(
        profile, token_kind=TokenKind.CLASSIC, permissions={"workflows": "write"}
    )
    url = urllib.parse.urlsplit(selected.token_url())
    fields = urllib.parse.parse_qs(url.query)
    assert url.path == "/settings/tokens/new"
    assert fields["scopes"] == ["repo,workflow"]
    assert "target_name" not in fields
    assert urllib.parse.urlsplit(selected.token_url(TokenKind.FINE_GRAINED)).path == (
        "/settings/personal-access-tokens/new"
    )


@pytest.mark.parametrize(
    ("token", "kind"),
    [(TOKEN, TokenKind.FINE_GRAINED), ("ghp_" + "a" * 36, TokenKind.CLASSIC)],
)
def test_token_type_must_match_selected_enrollment(token: str, kind: TokenKind) -> None:
    validate_token(token)
    validate_token(token, kind)
    other = (
        TokenKind.CLASSIC if kind == TokenKind.FINE_GRAINED else TokenKind.FINE_GRAINED
    )
    with pytest.raises(AccessError, match="token-invalid"):
        validate_token(token, other)


def test_all_organization_repositories_preserves_owner_and_private_probe(
    declaration: dict[str, Any],
) -> None:
    declaration["repositories"] = ["*"]
    declaration["token_kind"] = "classic"
    selected = Profile.from_data(declaration)
    assert selected.allows_repository("EMPLOYER", "another-project")
    assert not selected.allows_repository("other", "another-project")
    assert not selected.allows_repository("employer", "..")
    assert not selected.allows_repository("employer", "project/other")
    api = FakeAPI(selected)
    verify(selected, api)
    assert "/repos/employer/private-project" in api.calls
    assert not any("*" in call for call in api.calls)


@pytest.mark.allow_hosts(["127.0.0.1"])
def test_classic_local_submission_requires_matching_token_and_sso_confirmation(
    token_server: TokenServer,
) -> None:
    token_server.form = TOKEN_FORMS[TokenKind.CLASSIC]
    with contextlib.closing(
        http.client.HTTPConnection("127.0.0.1", token_server.server_port, timeout=3)
    ) as connection:
        connection.request("GET", "/test-nonce")
        response = connection.getresponse()
        page = response.read().decode()
    assert "Configure SSO" in page
    assert "Classic tokens can reach other repositories" in page
    assert submit(token_server)[0] == 400
    candidate = "ghp_" + "a" * 36
    status, body = submit(
        token_server,
        body=urllib.parse.urlencode({"token": candidate, "owner_confirmed": "yes"}),
    )
    assert status == 200
    assert token_server.token == candidate
    assert candidate not in body


def test_environment_removes_conflicting_identity_and_traces() -> None:
    original = {
        "PATH": "tools",
        "KEEP": "yes",
        "GH_TOKEN": TOKEN,
        "GH_DEBUG": "api",
        "GITHUB_TOKEN": TOKEN,
        "GCM_TRACE_SECRETS": "1",
        "GIT_TRACE_CURL": "1",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "store",
        "GIT_DIR": "elsewhere",
        "GIT_WORK_TREE": "elsewhere",
    }
    assert private_environment(original) == {"PATH": "tools", "KEEP": "yes"}
    assert original["GH_TOKEN"] == TOKEN


def test_verify_checks_identity_private_access_and_enabled_reads(
    profile: Profile,
) -> None:
    api = FakeAPI(profile)
    evidence = verify(profile, api)
    assert "identity" in evidence
    assert "contents:read:private-project" in evidence
    assert api.calls == [
        "/user",
        "/repos/employer/private-project",
        "/repos/employer/private-project/branches?per_page=1",
        "/repos/employer/private-project/pulls?state=open&per_page=1",
        "/repos/employer/private-project/actions/runs?per_page=1",
    ]
    assert all("write" not in item for item in evidence)


def test_verify_rejects_wrong_identity_before_repository_access(
    profile: Profile,
) -> None:
    api = FakeAPI(profile)
    api.login = "someone-else"
    with pytest.raises(AccessError, match="identity-mismatch"):
        verify(profile, api)
    assert api.calls == ["/user"]


def test_verify_does_not_accept_public_access_as_approval_proof(
    profile: Profile,
) -> None:
    api = FakeAPI(profile)
    api.private = False
    with pytest.raises(AccessError, match="verification-invalid"):
        verify(profile, api)


class FakeResponse:
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *values: object) -> None:
        return None

    @staticmethod
    def read(size: int) -> bytes:
        return b"{}"


def test_github_api_read_sends_no_tool_identifying_user_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = GitHubAPI(TOKEN)
    captured: list[Any] = []

    def fake_open(request: Any, timeout: float) -> FakeResponse:
        captured.append(request)
        return FakeResponse()

    monkeypatch.setattr(api.opener, "open", fake_open)

    api.read("/user")

    assert len(captured) == 1
    request = captured[0]
    assert not request.has_header("User-agent")


@pytest.mark.parametrize(
    ("status", "headers", "message"),
    [
        (401, {}, "authentication-invalid"),
        (403, {"X-GitHub-SSO": "required; url=https://evil.example"}, "sso-required"),
        (403, {}, "access-denied"),
        (404, {}, "access-denied"),
        (403, {"X-RateLimit-Remaining": "0"}, "rate-limited"),
        (403, {"x-ratelimit-remaining": "0"}, "rate-limited"),
        (403, {"retry-after": "30"}, "rate-limited"),
        (403, {"x-github-sso": "required"}, "sso-required"),
        (429, {}, "rate-limited"),
        (500, {}, "github-unavailable"),
    ],
)
def test_failure_diagnostics_use_evidence_and_hide_response_data(
    status: int,
    headers: dict[str, str],
    message: str,
) -> None:
    error = response_error(status, headers)
    assert str(error).startswith(message)
    assert "evil.example" not in str(error)


def test_rotation_verifies_before_vault_write(profile: Profile, tmp_path: Path) -> None:
    state, vault, api = State(tmp_path, profile), MemoryVault(), FakeAPI(profile)
    state.select(VERSION)
    api.fail = "/repos/employer/private-project/pulls?state=open&per_page=1"
    with pytest.raises(AccessError, match="access-denied"):
        rotate(profile, state, vault, TOKEN, lambda _: api)
    assert state.active() == VERSION
    assert vault.values == {VERSION: PREVIOUS}
    assert vault.writes == []


def test_rotation_selects_verified_version_without_revoking_previous(
    profile: Profile,
    tmp_path: Path,
) -> None:
    state, vault = State(tmp_path, profile), MemoryVault()
    state.select(VERSION)
    rotate(profile, state, vault, TOKEN, lambda _: FakeAPI(profile))
    selected = state.active()
    assert selected is not None and selected != VERSION
    assert vault.values[selected] == TOKEN
    assert vault.values[VERSION] == PREVIOUS
    assert TOKEN not in state.path.read_text(encoding="utf-8")


@pytest.mark.parametrize("failure", ["readback", "tool-probe"])
def test_rotation_rolls_back_after_storage_or_tool_failure(
    profile: Profile,
    tmp_path: Path,
    failure: str,
) -> None:
    state, vault = State(tmp_path, profile), MemoryVault()
    state.select(VERSION)
    vault.corrupt = failure == "readback"

    def tool_probe() -> None:
        raise AccessError("tool-failed")

    with pytest.raises(AccessError):
        rotate(profile, state, vault, TOKEN, lambda _: FakeAPI(profile), tool_probe)
    assert state.active() == VERSION
    assert vault.values == {VERSION: PREVIOUS}


def test_rotation_serializes_organization_and_retains_lock_owner(
    profile: Profile,
    tmp_path: Path,
) -> None:
    state = State(tmp_path, profile)
    with state.lock():
        with pytest.raises(AccessError, match="rotation-busy"), state.lock():
            pytest.fail("Second writer entered")
        assert (state.directory / "rotation.lock").exists()
    assert not (state.directory / "rotation.lock").exists()


def test_different_organizations_do_not_share_vault_or_state_keys(
    profile: Profile,
) -> None:
    other = Profile(
        "other-employer",
        profile.login,
        profile.verify_repository,
        profile.repositories,
        profile.permissions,
    )
    assert other.key != profile.key


def test_gcm_never_accepts_plaintext_backend(
    profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("access_core.sys.platform", "linux")
    with pytest.raises(AccessError, match="vault-unavailable"):
        GCMVault(
            profile,
            {"GCM_CREDENTIAL_STORE": "plaintext"},
            Path(shutil.which("git") or ".").resolve(),
        )


def test_gcm_pins_selected_git_before_activated_launchers(
    profile: Profile, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git = tmp_path / "Git" / "cmd" / "git.exe"
    git.parent.mkdir(parents=True)
    git.write_text("", encoding="utf-8")
    monkeypatch.setattr("access_core.sys.platform", "win32")
    monkeypatch.setattr("access_core.shutil.which", lambda *values, **keywords: "gcm")

    vault = GCMVault(profile, {"Path": str(tmp_path / "launchers")}, git)

    assert vault.environment["PATH"].split(os.pathsep)[0] == str(git.parent)
    assert "Path" not in vault.environment


@pytest.fixture
def token_server(profile: Profile) -> Iterator[TokenServer]:
    with TokenServer(("127.0.0.1", 0), TokenHandler) as server:
        server.profile, server.nonce = profile, "test-nonce"
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}
        )
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=3)


def submit(
    server: TokenServer,
    *,
    host: str | None = None,
    origin: str | None = None,
    path: str = "/test-nonce",
    body: str | None = None,
) -> tuple[int, str]:
    host = host or f"127.0.0.1:{server.server_port}"
    origin = origin or f"http://127.0.0.1:{server.server_port}"
    headers = {
        "Host": host,
        "Origin": origin,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    body = body or urllib.parse.urlencode({"token": TOKEN, "owner_confirmed": "yes"})
    with contextlib.closing(
        http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    ) as connection:
        connection.request("POST", path, body, headers)
        response = connection.getresponse()
        return response.status, response.read().decode()


@pytest.mark.allow_hosts(["127.0.0.1"])
def test_local_submission_receives_token_without_echo_or_logs(
    token_server: TokenServer,
    capsys: pytest.CaptureFixture[str],
) -> None:
    status, body = submit(token_server)
    assert status == 200
    assert token_server.token == TOKEN
    assert TOKEN not in body
    assert TOKEN not in str(capsys.readouterr())


@pytest.mark.allow_hosts(["127.0.0.1"])
def test_local_page_displays_exact_organization_and_repository_allowlist(
    token_server: TokenServer,
) -> None:
    with contextlib.closing(
        http.client.HTTPConnection("127.0.0.1", token_server.server_port, timeout=3)
    ) as connection:
        connection.request("GET", "/test-nonce")
        response = connection.getresponse()
        body = response.read().decode()
        assert response.status == 200
        assert "private-project" in body
        assert "employer" in body
        assert response.getheader("Cache-Control") == "no-store"
        assert response.getheader("Referrer-Policy") == "same-origin"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"host": "evil.example"},
        {"origin": "https://evil.example"},
        {"origin": "null"},
        {"path": "/wrong-nonce"},
        {"body": "token=" + TOKEN},
        {"body": "token=" + TOKEN + "&owner_confirmed=no"},
        {"body": "token=" + TOKEN + "&token=" + TOKEN + "&owner_confirmed=yes"},
        {"body": "a" * 5000},
    ],
)
@pytest.mark.allow_hosts(["127.0.0.1"])
def test_local_submission_rejects_cross_origin_or_malformed_input(
    token_server: TokenServer,
    kwargs: dict[str, str],
) -> None:
    status, body = submit(token_server, **kwargs)
    assert status in {400, 403}
    assert token_server.token is None
    assert TOKEN not in body


@pytest.mark.allow_hosts(["127.0.0.1"])
def test_local_server_socket_exception_still_blocks_external_connections() -> None:
    with (
        socket.socket() as connection,
        pytest.warns(UserWarning, match="test tried to use"),
        pytest.raises(pytest_socket.SocketConnectBlockedError, match=r"192\.0\.2\.1"),
    ):
        connection.connect(("192.0.2.1", 80))
