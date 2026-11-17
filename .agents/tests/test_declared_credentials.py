import dataclasses
import io
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from credentials import github_classic, github_fine_grained, nuget, providers
from credentials.http import Response, Transport
from credentials.model import (
    Client,
    Credential,
    CredentialError,
    Declaration,
    Endpoint,
    Provider,
    committed,
)
from credentials.service import Service
from credentials.vault import Secret, State
from gate_sandbox import Sandbox

NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)
TOKEN = Secret("ghp_" + "0" * 36)


@pytest.fixture
def credential() -> Credential:
    return Credential(
        "work",
        Provider.GITHUB_CLASSIC,
        "Example",
        "example-user",
        ("read:packages",),
        (
            Endpoint(
                Client.NUGET,
                "https://nuget.pkg.github.com/Example/index.json",
                "https://nuget.pkg.github.com/Example/download/sample/index.json",
            ),
        ),
    )


class MemoryVault:
    def __init__(self) -> None:
        self.values: dict[str, Secret] = {}
        self.corrupt = False
        self.refuse_write = False

    def read(self, version: str) -> Secret:
        return Secret("corrupted") if self.corrupt else self.values[version]

    def write(self, version: str, secret: Secret) -> None:
        if self.refuse_write:
            raise CredentialError("vault-unavailable: fixture write refused")
        self.values[version] = secret

    def erase(self, version: str) -> None:
        self.values.pop(version, None)


class GitHub:
    def __init__(
        self,
        *,
        login: str = "example-user",
        scopes: str = "read:packages",
        status: int = 200,
        expires_at: datetime | None = None,
    ) -> None:
        self.login = login
        self.scopes = scopes
        self.status = status
        self.expires_at = expires_at
        self.urls: list[str] = []

    def __call__(self, url: str, headers: Mapping[str, str]) -> Response:
        self.urls.append(url)
        response_headers = {"x-oauth-scopes": self.scopes}
        if self.expires_at is not None:
            response_headers["github-authentication-token-expiration"] = (
                self.expires_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            )
        body = (
            {"login": self.login} if url.endswith("/user") else {"versions": ["1.0.0"]}
        )
        assert headers["Authorization"].startswith(("Bearer ", "Basic "))
        return Response(self.status, response_headers, json.dumps(body).encode())


def document(credential: Credential) -> str:
    entry = dataclasses.asdict(credential)
    entry["permissions"] = dict(credential.permissions)
    return json.dumps({"schema_version": 1, "credentials": [entry]})


def service(
    tmp_path: Path, credential: Credential, vault: MemoryVault, transport: Transport
) -> Service:
    return Service(
        credential, State(tmp_path, credential), vault, github_classic, transport
    )


def test_declaration_roundtrips_declared_endpoints(credential: Credential) -> None:
    result = Declaration.parse(document(credential))
    providers.validate(result)
    assert result.named("work") == credential


@pytest.mark.parametrize(
    "text",
    [
        "{}",
        "[]",
        '{"schema_version":true,"credentials":[]}',
        '{"schema_version":1,"schema_version":1,"credentials":[]}',
        '{"schema_version":1,"credentials":[],"token":"secret"}',
    ],
)
def test_declaration_rejects_ambiguous_or_unsupported_documents(text: str) -> None:
    with pytest.raises(CredentialError, match="credentials-invalid"):
        Declaration.parse(text)


def test_declaration_accepts_an_explicit_opt_out() -> None:
    assert Declaration.parse('{"schema_version":1,"credentials":[]}').credentials == ()


@pytest.mark.parametrize(
    "url",
    [
        "https://nuget.pkg.github.com/ExampleOther/index.json",
        "https://nuget.pkg.github.com.evil.invalid/Example/index.json",
        "https://example-user@nuget.pkg.github.com/Example/index.json",
        "https://nuget.pkg.github.com/Example/../Other/index.json",
        "https://nuget.pkg.github.com/Example/%2e%2e/Other/index.json",
        "http://nuget.pkg.github.com/Example/index.json",
        "https://nuget.pkg.github.com:443/Example/index.json",
    ],
)
def test_routing_never_answers_other_urls(credential: Credential, url: str) -> None:
    assert Declaration((credential,)).route(Client.NUGET, url) is None


def test_routing_requires_the_declared_client(credential: Credential) -> None:
    declared = Declaration((credential,))
    assert declared.route(Client.NUGET, credential.endpoints[0].url) == credential
    assert declared.route(Client.GH, credential.endpoints[0].url) is None


def test_overlapping_credentials_fail_instead_of_choosing_an_account(
    credential: Credential,
) -> None:
    other = dataclasses.replace(credential, name="other")
    with pytest.raises(CredentialError, match="two credentials"):
        Declaration((credential, other))


def test_fine_grained_token_cannot_be_declared_for_packages(
    credential: Credential,
) -> None:
    fine = dataclasses.replace(
        credential, provider=Provider.GITHUB_FINE_GRAINED, scopes=()
    )
    with pytest.raises(CredentialError, match="GitHub Packages"):
        github_fine_grained.validate(fine)


def test_github_rejects_an_endpoint_in_another_account(credential: Credential) -> None:
    wrong = dataclasses.replace(
        credential,
        endpoints=(Endpoint(Client.GIT, "https://github.com/Other/repository"),),
    )
    with pytest.raises(CredentialError, match="outside"):
        github_classic.validate(wrong)


def test_classic_token_needs_the_declared_package_scope(credential: Credential) -> None:
    transport = GitHub(scopes="repo, workflow")
    with pytest.raises(CredentialError, match="credential-scope-missing"):
        github_classic.verify(credential, TOKEN, transport)
    assert transport.urls == ["https://api.github.com/user"]


def test_verification_detects_wrong_account_before_querying_packages(
    credential: Credential,
) -> None:
    transport = GitHub(login="other-user")
    with pytest.raises(CredentialError, match="account-mismatch"):
        github_classic.verify(credential, TOKEN, transport)
    assert len(transport.urls) == 1


def test_verification_probes_the_actual_declared_package(
    credential: Credential,
) -> None:
    transport = GitHub()
    github_classic.verify(credential, TOKEN, transport)
    assert transport.urls == [
        "https://api.github.com/user",
        credential.endpoints[0].probe,
    ]


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (401, "credential-invalid"),
        (403, "credential-access-denied"),
        (404, "credential-access-denied"),
        (429, "credential-rate-limited"),
        (500, "credential-service-unavailable"),
        (302, "credential-service-unavailable"),
    ],
)
def test_verification_classifies_failures_without_raw_diagnostics(
    credential: Credential, status: int, reason: str
) -> None:
    with pytest.raises(CredentialError, match=reason) as failure:
        github_classic.verify(credential, TOKEN, GitHub(status=status))
    assert TOKEN.value not in str(failure.value)


def test_failed_candidate_does_not_replace_a_working_enrollment(
    tmp_path: Path, credential: Credential
) -> None:
    vault = MemoryVault()
    original = service(tmp_path, credential, vault, GitHub())
    original.onboard(TOKEN, NOW + timedelta(days=20), NOW)
    previous = original.state.active()
    values = dict(vault.values)
    rejected = service(tmp_path, credential, vault, GitHub(login="wrong-account"))
    with pytest.raises(CredentialError, match="account-mismatch"):
        rejected.onboard(TOKEN, NOW + timedelta(days=30), NOW)
    assert rejected.state.active() == previous
    assert vault.values == values


@pytest.mark.parametrize("failure", ["write", "readback"])
def test_secure_store_failure_keeps_the_previous_credential(
    tmp_path: Path, credential: Credential, failure: str
) -> None:
    vault = MemoryVault()
    state = State(tmp_path, credential)
    previous = state.save(vault, TOKEN, NOW + timedelta(days=20))
    values = dict(vault.values)
    vault.refuse_write = failure == "write"
    vault.corrupt = failure == "readback"
    with pytest.raises(CredentialError, match="vault-unavailable"):
        state.save(vault, Secret("candidate"), NOW + timedelta(days=30))
    assert state.active() == previous
    assert vault.values == values


def test_public_state_contains_only_version_and_expiry(
    tmp_path: Path, credential: Credential
) -> None:
    state = State(tmp_path, credential)
    state.save(MemoryVault(), TOKEN, NOW + timedelta(days=20))
    content = state.path.read_text(encoding="utf-8")
    assert TOKEN.value not in content
    assert set(json.loads(content)) == {"schema_version", "version", "expires_at"}
    assert TOKEN.value not in repr(TOKEN)


def test_check_warns_a_week_before_expiry(
    tmp_path: Path, credential: Credential
) -> None:
    current = service(tmp_path, credential, MemoryVault(), GitHub())
    current.onboard(TOKEN, NOW + timedelta(days=20), NOW)
    result = current.check(NOW + timedelta(days=14))
    assert result.status == "expiring"
    assert result.action == "tj-credentials onboard --name work"


def test_check_uses_the_issuer_expiry_when_it_is_earlier(
    tmp_path: Path, credential: Credential
) -> None:
    vault = MemoryVault()
    current = service(tmp_path, credential, vault, GitHub())
    current.onboard(TOKEN, NOW + timedelta(days=20), NOW)
    server_expiry = NOW + timedelta(days=2)
    actual = service(tmp_path, credential, vault, GitHub(expires_at=server_expiry))
    assert actual.check(NOW).expires_at == server_expiry.isoformat()
    assert actual.check(NOW).status == "expiring"


def test_expired_credentials_fail_before_network_or_client_start(
    tmp_path: Path, credential: Credential
) -> None:
    transport = GitHub()
    current = service(tmp_path, credential, MemoryVault(), transport)
    current.onboard(TOKEN, NOW + timedelta(days=1), NOW)
    transport.urls.clear()
    with pytest.raises(CredentialError, match="credential-expired"):
        current.check(NOW + timedelta(days=2))
    assert transport.urls == []


def test_worktrees_inherit_the_fetched_default_declaration(
    sandbox: Sandbox, credential: Credential
) -> None:
    root = sandbox.repository("credential-inheritance")
    selection = root / ".agents/credentials.json"
    selection.parent.mkdir()
    selection.write_text(document(credential), encoding="utf-8")
    sandbox.git(root, "add", ".agents/credentials.json")
    sandbox.git(root, "commit", "-qm", "declare credentials")
    sandbox.git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    sandbox.git(
        root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"
    )
    sandbox.git(root, "checkout", "-q", "--orphan", "older-branch")
    sandbox.git(root, "rm", "-q", "-r", "--cached", ".")
    sandbox.git(root, "commit", "-q", "--allow-empty", "-m", "older branch")
    assert committed(root, sandbox.environment()) == Declaration((credential,))


def test_nuget_protocol_negotiates_authentication_without_reading_a_vault() -> None:
    frames = [
        {
            "RequestId": "tj-credentials-handshake",
            "Type": "Response",
            "Method": "Handshake",
            "Payload": {"ResponseCode": "Success", "ProtocolVersion": "2.0.0"},
        },
        {
            "RequestId": "1",
            "Type": "Request",
            "Method": "Handshake",
            "Payload": {"ProtocolVersion": "2.0.0", "MinimumProtocolVersion": "1.0.0"},
        },
        {"RequestId": "2", "Type": "Request", "Method": "Initialize", "Payload": {}},
        {
            "RequestId": "3",
            "Type": "Request",
            "Method": "GetOperationClaims",
            "Payload": {},
        },
        {"RequestId": "4", "Type": "Request", "Method": "Close"},
    ]
    output = io.StringIO()
    assert (
        nuget.serve(
            io.StringIO("".join(json.dumps(frame) + "\n" for frame in frames)),
            output,
            lambda client, url: None,
            NOW,
            declared=True,
        )
        == 0
    )
    responses = [
        value
        for line in output.getvalue().splitlines()
        if (value := json.loads(line)).get("Type") == "Response"
    ]
    assert responses[0]["Payload"]["ProtocolVersion"] == "2.0.0"
    assert responses[2]["Payload"]["Claims"] == ["Authentication"]


def test_nuget_returns_credentials_only_for_a_declared_source(
    tmp_path: Path, credential: Credential
) -> None:
    current = service(tmp_path, credential, MemoryVault(), GitHub())
    current.onboard(TOKEN, NOW + timedelta(days=20), NOW)

    def resolve(client: Client, url: str) -> Service | None:
        return current if credential.endpoints[0].matches(client, url) else None

    accepted = nuget.authentication({"Uri": credential.endpoints[0].url}, resolve, NOW)
    refused = nuget.authentication(
        {"Uri": "https://other.invalid/index.json"}, resolve, NOW
    )
    assert accepted["Password"] == TOKEN.value
    assert refused["ResponseCode"] == "NotFound"
    assert refused["Password"] is None
