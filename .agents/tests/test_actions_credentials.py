from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import tj_credentials
from credentials import children, context, http, onboarding
from credentials.context import Context
from credentials.model import (
    ActionsSecret,
    Client,
    Credential,
    CredentialError,
    Declaration,
    Endpoint,
    Provider,
)
from credentials.service import Service
from credentials.vault import Secret
from gate_sandbox import CURRENT_PLATFORM, Sandbox

TOKEN = Secret("github_pat_" + "x" * 45)


@pytest.fixture
def hosted() -> Credential:
    return Credential(
        "ci-read",
        Provider.GITHUB_FINE_GRAINED,
        "sample-org",
        "sample-user",
        (),
        (Endpoint(Client.GIT, "https://github.com/sample-org/private"),),
        permissions=(("contents", "read"),),
        actions_secret=ActionsSecret("sample-org/project", "SOURCE_READ"),
    )


def test_hosted_and_local_tokens_route_and_store_independently(
    hosted: Credential,
) -> None:
    local = replace(hosted, name="local", actions_secret=None)
    declarations = Declaration((hosted, local))
    assert hosted.key != local.key
    assert not hosted.supports(Client.GIT)
    assert declarations.route(Client.GIT, hosted.endpoints[0].url) == local
    with pytest.raises(CredentialError, match="duplicate Actions secret"):
        Declaration((hosted, replace(hosted, name="other")))
    with pytest.raises(CredentialError, match="only Contents read"):
        replace(hosted, permissions=(("contents", "write"),))
    with pytest.raises(CredentialError, match="same-owner"):
        replace(
            hosted, actions_secret=ActionsSecret("elsewhere/project", "SOURCE_READ")
        )


@pytest.fixture
def checkout(
    sandbox: Sandbox, hosted: Credential, monkeypatch: pytest.MonkeyPatch
) -> Path:
    entry = asdict(hosted) | {"permissions": dict(hosted.permissions)}
    repo = sandbox.repository(
        "project",
        {"schema_version": 1, "credentials": [entry]},
        "https://github.com/sample-org/project.git",
    )
    monkeypatch.chdir(repo)
    return repo


def test_agent_refused_before_issuer_vault_or_owner_cli(
    sandbox: Sandbox,
    checkout: Path,
    hosted: Credential,
) -> None:
    from credentials.actions import Publisher

    for marker in ("CODEX_THREAD_ID", "CLAUDECODE", "GH_TOKEN", "TJ_AGENT"):
        env = sandbox.environment(**{marker: "1"})
        current = Context.load(checkout, env, sandbox.state, windows=False)
        with pytest.raises(CredentialError, match="actions-owner-required"):
            Publisher.prepare(current, hosted)
    sandbox.git(
        checkout, "remote", "set-url", "origin", "https://github.com/sample-org/fork"
    )
    current = Context.load(
        checkout, sandbox.environment(), sandbox.state, windows=False
    )
    with pytest.raises(CredentialError, match="actions-origin-mismatch"):
        Publisher.prepare(current, hosted)


class FixtureVault(dict[str, Secret]):
    def write(self, version: str, secret: Secret) -> None:
        self[version] = secret

    def read(self, version: str) -> Secret:
        return self[version]

    def erase(self, version: str) -> None:
        del self[version]


def test_owner_onboards_then_retries_publication_without_reentering_token(
    sandbox: Sandbox,
    checkout: Path,
    hosted: Credential,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # All remote calls and the OS vault belong to the fixture.
    vault = FixtureVault()
    monkeypatch.setattr(context, "GCMVault", lambda *args, **kwargs: vault)
    for key in (
        "CODEX_SESSION_ID",
        "GITHUB_ACTIONS",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    fixture = sandbox.root / "fake_gh.py"
    fixture.write_text(
        """import os
import sys
from pathlib import Path

args = sys.argv[1:]
root = Path(__file__).parent
if args[:1] == ["secret"]:
    assert args == ["secret", "set", "SOURCE_READ", "--app", "actions",
                    "--repo", "github.com/sample-org/project"]
    token = sys.stdin.buffer.read()
    assert token == b"github_pat_" + b"x" * 45
    assert token.decode() not in str(os.environ) + str(args)
    if not (root / "allow").exists():
        sys.stderr.buffer.write(token)
        sys.exit(1)
    (root / "sent").touch()
else:
    assert not sys.stdin.buffer.read()
    print({"user": "sample-user", "repos/sample-org/project": "sample-org/project",
           "repos/sample-org/project/actions/secrets/public-key": "1",
           "repos/sample-org/project/actions/secrets/SOURCE_READ":
               "SOURCE_READ"}[args[1]])
""",
        encoding="utf-8",
    )
    native = sandbox.root / CURRENT_PLATFORM.command_launcher_name("gh")
    CURRENT_PLATFORM.write_launcher(
        native,
        CURRENT_PLATFORM.command_launcher_bytes(Path(sys.executable), fixture, ()),
    )
    monkeypatch.setattr(children, "real_tool", lambda name, environment: native)
    source_status = 403
    attempt = sandbox.root / "onboard.json"

    def request(
        url: str, headers: Mapping[str, str], *, proxies: Mapping[str, str]
    ) -> http.Response:
        assert headers["Authorization"] == "Bearer " + TOKEN.value
        if url.endswith("/user"):
            return http.Response(200, {}, b'{"login":"sample-user"}')
        if url.endswith("/private"):
            return http.Response(200, {}, b'{"full_name":"sample-org/private"}')
        assert (
            url == "https://api.github.com/repos/sample-org/private/commits?per_page=1"
        )
        return http.Response(
            source_status, {}, json.dumps([{"sha": "a" * 40}]).encode()
        )

    def receive(service: Service, now: datetime) -> onboarding.Candidate:
        assert service.credential == hosted
        if attempt.exists():
            running = json.loads(attempt.read_text(encoding="utf-8"))
            assert running["state"] == "running"
            assert running["pid"] > 0
        return onboarding.Candidate(TOKEN, now + timedelta(days=25))

    monkeypatch.setattr(http, "request", request)
    monkeypatch.setattr(onboarding, "receive", receive)
    onboard = ("onboard", "--name", "ci-read")
    assert tj_credentials.main(onboard) == 1
    assert "credential-access-denied" in capsys.readouterr().err
    assert not vault
    source_status = 200
    assert tj_credentials.main((*onboard, "--result-file", str(attempt))) == 1
    failed = capsys.readouterr()
    assert "actions-owner-operation-failed" in failed.err
    assert TOKEN.value not in failed.err + failed.out
    receipt = attempt.read_text(encoding="utf-8")
    assert TOKEN.value not in receipt
    assert json.loads(receipt)["state"] == "failed"
    assert tj_credentials.main((*onboard, "--result-file", str(attempt))) == 1
    assert "owner-attempt-exists" in capsys.readouterr().err
    assert attempt.read_text(encoding="utf-8") == receipt
    assert not (sandbox.root / "sent").exists()
    assert tj_credentials.main(("check", "--name", "ci-read")) == 0
    row = json.loads(capsys.readouterr().out)["credentials"][0]
    assert row["workflow_status"] == "unverified"
    assert row["owner_action"] == "tj-credentials publish --name ci-read"
    (sandbox.root / "allow").touch()
    retry = sandbox.root / "publish.json"
    assert (
        tj_credentials.main(
            ("publish", "--name", "ci-read", "--result-file", str(retry))
        )
        == 0
    )
    assert (sandbox.root / "sent").exists()
    assert json.loads(capsys.readouterr().out)["status"] == "published"
    finished = json.loads(retry.read_text(encoding="utf-8"))
    assert (finished["state"], finished["exit_code"], finished["status"]) == (
        "complete",
        0,
        "published",
    )
    assert finished["secret"] == "SOURCE_READ"
    assert finished["repository"] == "sample-org/project"


def test_missing_named_workflow_credential_reports_declaration_action(
    sandbox: Sandbox,
    capsys: pytest.CaptureFixture[str],
) -> None:
    empty = Context(sandbox.root, {}, sandbox.state, False, None)
    assert (
        tj_credentials.check(empty, "ci-read", datetime(2026, 11, 20, tzinfo=UTC)) == 1
    )
    row = json.loads(capsys.readouterr().out)
    assert row["status"] == "not-declared"
    assert "actions_secret" in row["action"]
