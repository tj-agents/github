from __future__ import annotations

import base64
import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Never

import pytest
from credentials import azure_cli, children, docker, git, npm, providers
from credentials.context import Context
from credentials.http import Response
from credentials.model import (
    Client,
    Credential,
    CredentialError,
    Declaration,
    Endpoint,
    Provider,
)
from credentials.service import Service
from credentials.vault import Secret, State
from gate_access import Declarations
from gate_credential import CredentialRequest, answer
from gate_sandbox import Sandbox
from test_declared_credentials import MemoryVault, document

NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)
NPM_SECRET = Secret("ghp_" + "0" * 36)
AZURE_SECRET = Secret("A" * 52)
REGISTRY = "ghcr.io"


@dataclass(frozen=True, slots=True)
class ChildCall:
    executable: Path
    arguments: tuple[str, ...]
    cwd: Path
    environment: dict[str, str]


@pytest.fixture
def child_calls(monkeypatch: pytest.MonkeyPatch) -> list[ChildCall]:
    calls: list[ChildCall] = []

    def execute(
        executable: Path,
        arguments: Sequence[str],
        cwd: Path,
        environment: Mapping[str, str],
    ) -> int:
        calls.append(ChildCall(executable, tuple(arguments), cwd, dict(environment)))
        return 29

    monkeypatch.setattr(children, "execute", execute)
    return calls


@pytest.fixture
def client_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Context:
    work = Credential(
        "work",
        Provider.GITHUB_CLASSIC,
        "Example",
        "example-user",
        ("read:packages",),
        (
            Endpoint(
                Client.NPM,
                "https://npm.pkg.github.com",
                "https://npm.pkg.github.com/@example/sample",
                "@example",
            ),
            Endpoint(Client.GIT, "https://github.com/Example/private"),
        ),
    )
    azure = Credential(
        "azure",
        Provider.AZURE_DEVOPS_PAT,
        "Example",
        "example-user",
        ("vso.code",),
        (Endpoint(Client.AZURE_DEVOPS, "https://dev.azure.com/Example"),),
    )
    declaration = Declaration((work, azure))
    providers.validate(declaration)
    responses = {
        "https://api.github.com/user": Response(
            200, {"x-oauth-scopes": "read:packages"}, b'{"login":"example-user"}'
        ),
        "https://api.github.com/repos/Example/private": Response(
            200, {}, b'{"full_name":"Example/private"}'
        ),
        "https://npm.pkg.github.com/@example%2fsample": Response(200, {}, b"{}"),
        "https://dev.azure.com/Example/_apis/connectionData?api-version=7.1": Response(
            200, {}, b'{"authenticatedUser":{"uniqueName":"example-user"}}'
        ),
    }

    def transport(url: str, headers: Mapping[str, str]) -> Response:
        assert "Authorization" in headers
        return responses[url]

    services = {}
    for credential, secret in ((work, NPM_SECRET), (azure, AZURE_SECRET)):
        state = State(tmp_path / "state", credential)
        vault = MemoryVault()
        state.save(vault, secret, NOW + timedelta(days=20))
        services[credential.key] = Service(
            credential,
            state,
            vault,
            providers.PROVIDERS[credential.provider],
            transport,
        )

    def service(context: Context, credential: Credential) -> Service:
        return services[credential.key]

    monkeypatch.setattr(Context, "service", service)
    return Context(
        tmp_path,
        {
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
            "NODE_AUTH_TOKEN": "fixture-inherited-npm",
            "AZURE_DEVOPS_EXT_PAT": "fixture-inherited-azure",
        },
        tmp_path / "state",
        False,
        declaration,
    )


@pytest.fixture
def unopened_context(
    client_context: Context, monkeypatch: pytest.MonkeyPatch
) -> Context:
    def service(context: Context, credential: Credential) -> Service:
        pytest.fail("rejected client request accessed a credential")

    monkeypatch.setattr(Context, "service", service)
    return client_context


@pytest.mark.parametrize("child_fails", [False, True])
def test_npm_keeps_secret_in_child_environment_and_removes_temporary_config(
    client_context: Context,
    monkeypatch: pytest.MonkeyPatch,
    child_fails: bool,
) -> None:
    userconfig = client_context.home / ".npmrc"
    original = "strict-ssl=true\n//npm.pkg.github.com/:_authToken=fixture-old-token\n"
    userconfig.write_text(original, encoding="utf-8")
    inherited = dict(client_context.environment)
    config_paths: list[Path] = []

    def execute(
        executable: Path,
        arguments: Sequence[str],
        cwd: Path,
        environment: Mapping[str, str],
    ) -> int:
        assert executable == client_context.cwd / "npm"
        assert tuple(arguments) == ("install", "@example/sample")
        assert cwd == client_context.cwd
        config = Path(environment["NPM_CONFIG_USERCONFIG"])
        config_paths.append(config)
        content = config.read_text(encoding="utf-8")
        assert "strict-ssl=true" in content
        assert "@example:registry=https://npm.pkg.github.com" in content
        assignment = next(
            line for line in content.splitlines() if ":_authToken=" in line
        )
        reference = assignment.partition("=")[2]
        assert reference.startswith("${") and reference.endswith("}")
        assert environment[reference[2:-1]] == NPM_SECRET.value
        assert NPM_SECRET.value not in content
        assert "fixture-old-token" not in content
        assert "NODE_AUTH_TOKEN" not in environment
        assert "AZURE_DEVOPS_EXT_PAT" not in environment
        assert NPM_SECRET.value not in " ".join(arguments)
        if child_fails:
            raise CredentialError("credential-client-unavailable: fixture child failed")
        return 29

    monkeypatch.setattr(children, "execute", execute)
    if child_fails:
        with pytest.raises(CredentialError, match="credential-client-unavailable"):
            npm.run(
                client_context,
                client_context.cwd / "npm",
                ("install", "@example/sample"),
                NOW,
            )
    else:
        assert (
            npm.run(
                client_context,
                client_context.cwd / "npm",
                ("install", "@example/sample"),
                NOW,
            )
            == 29
        )
    assert config_paths and all(not path.parent.exists() for path in config_paths)
    assert userconfig.read_text(encoding="utf-8") == original
    assert dict(client_context.environment) == inherited


class DockerHelper:
    def __init__(self) -> None:
        self.values: dict[str, docker.Saved] = {}
        self.writes = 0
        self.corrupt_readback = False
        self.refuse_rollback = False

    def __call__(
        self,
        program: Path,
        operation: str,
        payload: str,
        environment: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]:
        if operation == "get":
            saved = self.values.get(payload.strip())
            if saved is None:
                return subprocess.CompletedProcess(
                    [str(program), operation], 1, "credentials not found", ""
                )
            secret = (
                "fixture-corrupt-readback"
                if self.corrupt_readback and self.writes == 1
                else saved.secret.value
            )
            output = json.dumps({"Username": saved.login, "Secret": secret})
        elif operation == "store":
            if self.refuse_rollback and self.writes:
                return subprocess.CompletedProcess(
                    [str(program), operation], 1, "", "fixture locked"
                )
            data = json.loads(payload)
            self.values[data["ServerURL"]] = docker.Saved(
                data["Username"], Secret(data["Secret"])
            )
            self.writes += 1
            output = ""
        else:
            assert operation == "erase"
            if self.refuse_rollback:
                return subprocess.CompletedProcess(
                    [str(program), operation], 1, "", "fixture locked"
                )
            self.values.pop(payload.strip(), None)
            output = ""
        return subprocess.CompletedProcess([str(program), operation], 0, output, "")


@pytest.fixture
def docker_helper(monkeypatch: pytest.MonkeyPatch) -> DockerHelper:
    helper = DockerHelper()
    monkeypatch.setattr(docker, "call", helper)
    return helper


def test_docker_account_conflict_preserves_the_existing_account(
    tmp_path: Path,
    docker_helper: DockerHelper,
) -> None:
    previous = docker.Saved("other-user", Secret("fixture-other-secret"))
    docker_helper.values[REGISTRY] = previous
    with pytest.raises(CredentialError, match="docker-account-conflict"):
        docker.synchronize(
            tmp_path / "helper", REGISTRY, docker.Saved("example-user", NPM_SECRET), {}
        )
    assert docker_helper.values == {REGISTRY: previous}
    assert docker_helper.writes == 0


@pytest.mark.parametrize("already_enrolled", [False, True])
def test_docker_failed_readback_restores_the_previous_secure_store(
    tmp_path: Path,
    docker_helper: DockerHelper,
    already_enrolled: bool,
) -> None:
    if already_enrolled:
        docker_helper.values[REGISTRY] = docker.Saved(
            "example-user", Secret("fixture-previous-secret")
        )
    previous = dict(docker_helper.values)
    docker_helper.corrupt_readback = True
    with pytest.raises(
        CredentialError, match="secure-helper readback failed"
    ) as failure:
        docker.synchronize(
            tmp_path / "helper", REGISTRY, docker.Saved("example-user", NPM_SECRET), {}
        )
    assert docker_helper.values == previous
    assert NPM_SECRET.value not in str(failure.value)


@pytest.mark.parametrize("already_enrolled", [False, True])
def test_docker_failed_rollback_reports_uncertain_state(
    tmp_path: Path,
    docker_helper: DockerHelper,
    already_enrolled: bool,
) -> None:
    if already_enrolled:
        docker_helper.values[REGISTRY] = docker.Saved(
            "example-user", Secret("fixture-previous-secret")
        )
    docker_helper.corrupt_readback = True
    docker_helper.refuse_rollback = True
    with pytest.raises(CredentialError, match="docker-state-uncertain") as failure:
        docker.synchronize(
            tmp_path / "helper", REGISTRY, docker.Saved("example-user", NPM_SECRET), {}
        )
    assert NPM_SECRET.value not in str(failure.value)


@pytest.mark.parametrize(
    "organization",
    [
        (),
        ("--org", "https://dev.azure.com/Example"),
        ("--organization=https://dev.azure.com/example/",),
    ],
)
def test_azure_cli_uses_only_the_declared_organization_and_child_pat(
    client_context: Context,
    child_calls: list[ChildCall],
    organization: tuple[str, ...],
) -> None:
    original = dict(client_context.environment)
    arguments = ("repos", "list", *organization)
    assert (
        azure_cli.run(client_context, client_context.cwd / "az", arguments, NOW) == 29
    )
    assert len(child_calls) == 1
    call = child_calls[0]
    assert call.arguments == (
        arguments
        if organization
        else (*arguments, "--organization", "https://dev.azure.com/Example")
    )
    assert call.environment["AZURE_DEVOPS_EXT_PAT"] == AZURE_SECRET.value
    assert "NODE_AUTH_TOKEN" not in call.environment
    assert AZURE_SECRET.value not in " ".join(call.arguments)
    assert dict(client_context.environment) == original


@pytest.mark.parametrize(
    "organization",
    [
        ("--org", "https://dev.azure.com/Other"),
        ("--organization=https://dev.azure.com/ExampleOther",),
        ("--org", "https://dev.azure.com/Example", "--org=https://dev.azure.com/Other"),
        ("--organization",),
    ],
)
def test_azure_cli_rejects_other_or_missing_organizations_before_opening_credentials(
    unopened_context: Context,
    child_calls: list[ChildCall],
    organization: tuple[str, ...],
) -> None:
    with pytest.raises(
        CredentialError, match=r"credential-(target-denied|request-invalid)"
    ):
        azure_cli.run(
            unopened_context,
            unopened_context.cwd / "az",
            ("repos", "list", *organization),
            NOW,
        )
    assert child_calls == []


def test_azure_cli_refuses_to_choose_between_declared_organizations(
    unopened_context: Context,
    child_calls: list[ChildCall],
) -> None:
    assert unopened_context.declaration is not None
    azure = unopened_context.declaration.named("azure")
    other = replace(
        azure,
        name="other",
        owner="Other",
        endpoints=(Endpoint(Client.AZURE_DEVOPS, "https://dev.azure.com/Other"),),
    )
    context = replace(unopened_context, declaration=Declaration((azure, other)))
    with pytest.raises(CredentialError, match="credential-client-conflict"):
        azure_cli.run(context, context.cwd / "az", ("repos", "list"), NOW)
    assert child_calls == []


@pytest.mark.parametrize(
    "path",
    [
        "Other/private",
        "Example/unlisted",
        "Example/private-other",
        "Example/%70rivate",
        "Example/private/../other",
    ],
)
def test_git_declared_routing_stops_helper_fallback_for_undeclared_targets(
    unopened_context: Context,
    sandbox: Sandbox,
    declarations: Declarations,
    path: str,
) -> None:
    assert unopened_context.declaration is not None
    configuration = json.loads(document(unopened_context.declaration.named("work")))
    repository = sandbox.repository(
        "credential-clients", {**configuration, "access": {"method": "human"}}
    )
    response = answer(
        "get",
        CredentialRequest("https", "github.com", path, frozenset({"authtype"})),
        sandbox.environment(CLAUDECODE="1"),
        repository,
        declarations,
        NOW.timestamp(),
    )
    assert response.text == "quit=1\n"
    assert (
        response.message is not None and "credential-target-denied" in response.message
    )
    assert NPM_SECRET.value not in response.text + response.message


@pytest.mark.parametrize("origin", [None, "https://github.com/Example/private.git"])
def test_git_declared_routing_requires_the_requested_path_even_with_a_known_origin(
    unopened_context: Context,
    sandbox: Sandbox,
    origin: str | None,
) -> None:
    repository = sandbox.repository("missing-target", origin=origin)
    context = replace(
        unopened_context, cwd=repository, environment=sandbox.environment()
    )
    with pytest.raises(CredentialError, match="credential-target-unknown"):
        git.reply(
            CredentialRequest("https", "github.com", "", frozenset({"authtype"})),
            context,
            NOW,
        )


def test_npm_preserves_proxy_connectivity_without_exporting_other_credentials(
    client_context: Context, child_calls: list[ChildCall]
) -> None:
    context = replace(
        client_context,
        environment={
            **client_context.environment,
            "HTTPS_PROXY": "http://proxy.example.invalid:8080",
            "NO_PROXY": "localhost,127.0.0.1",
        },
    )
    npm.run(context, context.cwd / "npm", ("install", "@example/sample"), NOW)
    environment = child_calls[0].environment
    assert environment["HTTPS_PROXY"] == "http://proxy.example.invalid:8080"
    assert environment["NO_PROXY"] == "localhost,127.0.0.1"
    assert "AZURE_DEVOPS_EXT_PAT" not in environment


def test_git_declared_routing_requires_ephemeral_authentication_before_vault_access(
    client_context: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def read(vault: MemoryVault, version: str) -> Never:
        pytest.fail("unsupported Git client read a secret")

    monkeypatch.setattr(MemoryVault, "read", read)
    with pytest.raises(CredentialError, match="credential-client-unsupported"):
        git.reply(
            CredentialRequest("https", "github.com", "Example/private", frozenset()),
            client_context,
            NOW,
        )


def test_git_declared_target_receives_an_ephemeral_credential_without_a_password(
    client_context: Context,
) -> None:
    response = git.reply(
        CredentialRequest(
            "https", "github.com", "Example/private.git", frozenset({"authtype"})
        ),
        client_context,
        NOW,
    )
    fields = {}
    for line in response.splitlines():
        key, _, value = line.partition("=")
        fields[key] = value
    assert fields["authtype"] == "Basic"
    assert fields["ephemeral"] == "1"
    assert (
        base64.b64decode(fields["credential"]).decode()
        == f"example-user:{NPM_SECRET.value}"
    )
    assert "password" not in fields
    assert NPM_SECRET.value not in response
