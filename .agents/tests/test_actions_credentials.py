from __future__ import annotations

import contextlib
import http.client
import json
import sys
import urllib.parse
import webbrowser
from collections.abc import Iterator, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

import credentials.http
import pytest
import tj_credentials
from credentials import children, context, onboarding
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


def test_credential_overrides_and_wrong_origin_refused_before_owner_cli(
    sandbox: Sandbox,
    checkout: Path,
    hosted: Credential,
) -> None:
    from credentials.actions import Publisher

    for marker in (
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
        "GITHUB_ACTIONS",
    ):
        env = sandbox.environment(**{marker: "1"})
        current = Context.load(checkout, env, sandbox.state, windows=False)
        with pytest.raises(CredentialError, match="actions-credential-override"):
            Publisher.prepare(current, hosted, onboarding.Operation.ONBOARD)
    sandbox.git(
        checkout, "remote", "set-url", "origin", "https://github.com/sample-org/fork"
    )
    current = Context.load(
        checkout, sandbox.environment(), sandbox.state, windows=False
    )
    with pytest.raises(CredentialError, match="actions-origin-mismatch"):
        Publisher.prepare(current, hosted, onboarding.Operation.ONBOARD)


class FixtureVault(dict[str, Secret]):
    def __init__(self) -> None:
        super().__init__()
        self.accesses = 0

    def write(self, version: str, secret: Secret) -> None:
        self.accesses += 1
        self[version] = secret

    def read(self, version: str) -> Secret:
        self.accesses += 1
        return self[version]

    def erase(self, version: str) -> None:
        del self[version]


@pytest.fixture
def browser_executor() -> Iterator[ThreadPoolExecutor]:
    with ThreadPoolExecutor(max_workers=1) as executor:
        yield executor


@pytest.mark.allow_hosts(["127.0.0.1"])
def test_agent_opens_guarded_form_then_retries_publication_with_fresh_human_consent(
    sandbox: Sandbox,
    checkout: Path,
    hosted: Credential,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    browser_executor: ThreadPoolExecutor,
) -> None:
    # All remote calls and the OS vault belong to the fixture.
    vault = FixtureVault()
    monkeypatch.setattr(context, "GCMVault", lambda *args, **kwargs: vault)
    for key in ("CODEX_THREAD_ID", "CLAUDECODE", "CODEX_SESSION_ID", "TJ_AGENT"):
        monkeypatch.setenv(key, "fixture-agent")
    monkeypatch.setenv("GH_CONFIG_DIR", str(sandbox.root / "native-profile"))
    for key in (
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
assert os.environ["GH_CONFIG_DIR"] == str(root / "native-profile")
for marker in ("CODEX_THREAD_ID", "CLAUDECODE", "CODEX_SESSION_ID", "TJ_AGENT"):
    assert os.environ[marker] == "fixture-agent"
with (root / "owner-calls").open("a", encoding="utf-8") as log:
    log.write(args[0] + "\\n")
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
    actor = "other-user" if (root / "wrong-owner").exists() else "sample-user"
    print({"user": actor, "repos/sample-org/project": "sample-org/project",
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
    ) -> credentials.http.Response:
        assert headers["Authorization"] == "Bearer " + TOKEN.value
        if url.endswith("/user"):
            return credentials.http.Response(200, {}, b'{"login":"sample-user"}')
        if url.endswith("/private"):
            return credentials.http.Response(
                200, {}, b'{"full_name":"sample-org/private"}'
            )
        assert (
            url == "https://api.github.com/repos/sample-org/private/commits?per_page=1"
        )
        return credentials.http.Response(
            source_status, {}, json.dumps([{"sha": "a" * 40}]).encode()
        )

    opened: list[str] = []
    responses: list[Future[None]] = []
    publish = False
    change_declaration = False
    change_enrollment = False

    def browser_submit(url: str, accesses: int, calls: str) -> None:
        parsed = urllib.parse.urlsplit(url)
        with contextlib.closing(
            http.client.HTTPConnection("127.0.0.1", parsed.port, timeout=5)
        ) as connection:
            connection.request("GET", parsed.path)
            response = connection.getresponse()
            page = response.read().decode()
            assert response.status == 200
        for text in (
            "sample-user",
            "sample-org/project",
            "SOURCE_READ",
            "sample-org/private",
            "Contents: Read-only",
            "publish" if publish else "onboard",
        ):
            assert text in page
        assert ('name="token"' in page) is not publish
        assert vault.accesses == accesses
        call_log = sandbox.root / "owner-calls"
        assert (
            call_log.read_text(encoding="utf-8") if call_log.exists() else ""
        ) == calls
        if attempt.exists():
            running = json.loads(attempt.read_text(encoding="utf-8"))
            assert running["pid"] > 0
        fields = {"publication": "yes"}
        if not publish:
            # The date is read from the form to exercise its actual expiry limits.
            last = page.split('max="', 1)[1].split('"', 1)[0]
            fields.update(token=TOKEN.value, expires_at=last, confirmed="yes")
        if change_declaration:
            declaration = checkout / ".agents/repository.json"
            data = json.loads(declaration.read_text(encoding="utf-8"))
            data["credentials"][0]["actions_secret"]["name"] = "OTHER_SECRET"
            declaration.write_text(json.dumps(data), encoding="utf-8")
            sandbox.git(checkout, "add", ".agents/repository.json")
            sandbox.git(checkout, "commit", "-m", "Change destination during consent")
        if change_enrollment:
            active = sandbox.state / "credentials" / hosted.key / "active.json"
            data = json.loads(active.read_text(encoding="utf-8"))
            data["version"] = "b" * 32
            active.write_text(json.dumps(data), encoding="utf-8")
        with contextlib.closing(
            http.client.HTTPConnection("127.0.0.1", parsed.port, timeout=5)
        ) as connection:
            connection.request(
                "POST",
                parsed.path,
                urllib.parse.urlencode(fields),
                {
                    "Origin": "http://" + parsed.netloc,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            response = connection.getresponse()
            assert response.status == 200
            assert TOKEN.value not in response.read().decode()

    def open_browser(url: str) -> bool:
        opened.append(url)
        if url.startswith("http://127.0.0.1"):
            call_log = sandbox.root / "owner-calls"
            calls = call_log.read_text(encoding="utf-8") if call_log.exists() else ""
            responses.append(
                browser_executor.submit(browser_submit, url, vault.accesses, calls)
            )
        return True

    receive = onboarding.receive

    def bounded_receive(
        service: Service, now: datetime, *, operation: onboarding.Operation
    ) -> onboarding.Submission:
        try:
            return receive(service, now, operation=operation, timeout=10)
        finally:
            responses[-1].result(timeout=5)

    monkeypatch.setattr(credentials.http, "request", request)
    monkeypatch.setattr(webbrowser, "open", open_browser)
    monkeypatch.setattr(onboarding, "receive", bounded_receive)
    onboard = ("onboard", "--name", "ci-read")
    wrong_owner = sandbox.root / "wrong-owner"
    wrong_owner.touch()
    assert tj_credentials.main(onboard) == 1
    assert "actions-owner-mismatch" in capsys.readouterr().err
    assert vault.accesses == 0
    assert not (sandbox.root / "sent").exists()
    wrong_owner.unlink()
    assert tj_credentials.main(onboard) == 1
    assert "credential-access-denied" in capsys.readouterr().err
    assert not vault
    source_status = 200
    assert tj_credentials.main((*onboard, "--result-file", str(attempt))) == 1
    failed = capsys.readouterr()
    assert "actions-owner-operation-failed" in failed.err
    assert TOKEN.value not in failed.err + failed.out
    assert "127.0.0.1" not in failed.err + failed.out
    for url in opened:
        assert urllib.parse.urlsplit(url).path not in failed.err + failed.out
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
    publish = True
    opened.clear()
    assert (
        tj_credentials.main(
            ("publish", "--name", "ci-read", "--result-file", str(retry))
        )
        == 0
    )
    assert (sandbox.root / "sent").exists()
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["status"] == "published"
    finished = json.loads(retry.read_text(encoding="utf-8"))
    assert (finished["state"], finished["exit_code"], finished["status"]) == (
        "complete",
        0,
        "published",
    )
    assert finished["secret"] == "SOURCE_READ"
    assert finished["repository"] == "sample-org/project"
    assert len(opened) == 1
    assert "127.0.0.1" not in retry.read_text(encoding="utf-8")
    change_enrollment = True
    accesses = vault.accesses
    calls = (sandbox.root / "owner-calls").read_text(encoding="utf-8")
    assert tj_credentials.main(("publish", "--name", "ci-read")) == 1
    assert "actions-enrollment-changed" in capsys.readouterr().err
    assert vault.accesses == accesses
    assert (sandbox.root / "owner-calls").read_text(encoding="utf-8") == calls
    change_enrollment = False
    change_declaration = True
    accesses = vault.accesses
    calls = (sandbox.root / "owner-calls").read_text(encoding="utf-8")
    assert tj_credentials.main(("publish", "--name", "ci-read")) == 1
    assert "actions-undeclared" in capsys.readouterr().err
    assert vault.accesses == accesses
    assert (sandbox.root / "owner-calls").read_text(encoding="utf-8") == calls


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
