import contextlib
import http.client
import json
import threading
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from github_access import (
    AccessError,
    GCMVault,
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
def test_token_rejects_broad_or_injected_credentials(token: str) -> None:
    with pytest.raises(AccessError, match="token-invalid"):
        validate_token(token)


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
    monkeypatch.setattr("github_access.sys.platform", "linux")
    with pytest.raises(AccessError, match="vault-unavailable"):
        GCMVault(profile, {"GCM_CREDENTIAL_STORE": "plaintext"})


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


def test_local_submission_receives_token_without_echo_or_logs(
    token_server: TokenServer,
    capsys: pytest.CaptureFixture[str],
) -> None:
    status, body = submit(token_server)
    assert status == 200
    assert token_server.token == TOKEN
    assert TOKEN not in body
    assert TOKEN not in str(capsys.readouterr())


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


@pytest.mark.parametrize(
    "kwargs",
    [
        {"host": "evil.example"},
        {"origin": "https://evil.example"},
        {"path": "/wrong-nonce"},
        {"body": "token=" + TOKEN},
        {"body": "token=" + TOKEN + "&owner_confirmed=no"},
        {"body": "token=" + TOKEN + "&token=" + TOKEN + "&owner_confirmed=yes"},
        {"body": "a" * 5000},
    ],
)
def test_local_submission_rejects_cross_origin_or_malformed_input(
    token_server: TokenServer,
    kwargs: dict[str, str],
) -> None:
    status, body = submit(token_server, **kwargs)
    assert status in {400, 403}
    assert token_server.token is None
    assert TOKEN not in body
