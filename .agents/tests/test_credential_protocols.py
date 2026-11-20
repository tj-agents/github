import contextlib
import http.client
import io
import json
import threading
import urllib.parse
import webbrowser
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from credentials import (
    github_classic,
    github_fine_grained,
    mcp,
    mcp_release,
    onboarding,
)
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
from credentials.vault import Enrollment, State
from playwright import sync_api
from test_declared_credentials import GitHub, MemoryVault

NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)
TOKEN = "ghp_" + "0" * 36


@pytest.fixture
def local_form(tmp_path: Path) -> Iterator[onboarding.Server]:
    credential = Credential(
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
    service = Service(
        credential, State(tmp_path, credential), MemoryVault(), github_classic, GitHub()
    )
    with onboarding.Server(("127.0.0.1", 0), onboarding.Handler) as server:
        server.nonce, server.service, server.now = "fixture-nonce", service, NOW
        server.request = onboarding.Request(credential, onboarding.Operation.ONBOARD)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=3)


def submit(
    server: onboarding.Server,
    *,
    host: str | None = None,
    origin: str | None = None,
    path: str = "/fixture-nonce",
    body: str | None = None,
    content_length: str | None = None,
) -> tuple[int, str]:
    local = f"127.0.0.1:{server.server_port}"
    headers = {
        "Host": host or local,
        "Origin": origin or "http://" + local,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if content_length is not None:
        headers["Content-Length"] = content_length
    if body is None:
        body = urllib.parse.urlencode(
            {
                "token": TOKEN,
                "expires_at": (NOW + timedelta(days=20)).date().isoformat(),
                "confirmed": "yes",
            }
        )
    with contextlib.closing(
        http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    ) as connection:
        connection.request("POST", path, body, headers)
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8")


@pytest.mark.allow_hosts(["127.0.0.1"])
def test_onboarding_receives_candidate_without_echoing_or_logging(
    local_form: onboarding.Server, capsys: pytest.CaptureFixture[str]
) -> None:
    status, body = submit(local_form)
    assert status == 200
    assert local_form.submission is not None
    assert local_form.submission.candidate is not None
    assert local_form.submission.candidate.secret.value == TOKEN
    assert TOKEN not in body + str(capsys.readouterr())
    assert local_form.service.state.active() is None


@pytest.mark.parametrize(
    "changes",
    [
        {"host": "evil.invalid", "body": ""},
        {"origin": "https://evil.invalid", "body": ""},
        {"origin": "null", "body": ""},
        {"path": "/other", "body": ""},
        {"body": "token=" + TOKEN},
        {"body": "token=" + TOKEN + "&confirmed=no"},
        {"body": "", "content_length": "9000"},
        {
            "body": "token="
            + TOKEN
            + "&token="
            + TOKEN
            + "&expires_at=2026-12-01&confirmed=yes"
        },
    ],
)
@pytest.mark.allow_hosts(["127.0.0.1"])
def test_onboarding_rejects_untrusted_or_malformed_submission(
    local_form: onboarding.Server, changes: dict[str, str]
) -> None:
    status, body = submit(local_form, **changes)
    assert status in {400, 403}
    assert local_form.submission is None
    assert TOKEN not in body


@pytest.mark.allow_hosts(["127.0.0.1"])
def test_onboarding_page_is_uncached_and_confirms_the_declared_scope(
    local_form: onboarding.Server,
) -> None:
    with contextlib.closing(
        http.client.HTTPConnection("127.0.0.1", local_form.server_port, timeout=3)
    ) as connection:
        connection.request("GET", "/fixture-nonce")
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        assert response.status == 200
        assert "Example" in body and "read:packages" in body
        assert "Configure SSO" in body
        assert response.getheader("Cache-Control") == "no-store"
        assert response.getheader("Referrer-Policy") == "strict-origin"


@pytest.fixture
def hosted_form(local_form: onboarding.Server) -> onboarding.Server:
    credential = Credential(
        "source-read",
        Provider.GITHUB_FINE_GRAINED,
        "Example",
        "example-user",
        (),
        (Endpoint(Client.GIT, "https://github.com/Example/private"),),
        expires_in=180,
        permissions=(("contents", "read"),),
        actions_secret=ActionsSecret("Example/catalog", "PRIVATE_SOURCE_READ"),
    )
    local_form.service = replace(
        local_form.service, credential=credential, provider=github_fine_grained
    )
    local_form.request = onboarding.Request(credential, onboarding.Operation.ONBOARD)
    return local_form


@pytest.mark.allow_hosts(["127.0.0.1"])
def test_chrome_can_submit_the_guarded_form(hosted_form: onboarding.Server) -> None:
    issuer = github_fine_grained.creation_url(hosted_form.service.credential)
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(issuer).query)["expires_in"] == [
        "180"
    ]
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome")
        except sync_api.Error as error:
            if "distribution 'chrome' is not found" in str(error):
                pytest.skip("Google Chrome is unavailable for browser acceptance")
            raise
        with browser:
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{hosted_form.server_port}/fixture-nonce")
            assert (
                page.get_by_label("Token expiry").input_value()
                == (NOW + timedelta(days=180)).date().isoformat()
            )
            page.get_by_label("Credential", exact=True).fill(TOKEN)
            page.get_by_label("I selected this account", exact=False).check()
            page.get_by_label("I authorize onboard", exact=False).check()
            with page.expect_navigation() as navigation:
                page.get_by_role("button", name="Verify and publish").click()
            response = navigation.value
            assert response is not None
            assert response.status == 200
            assert page.title() == "Submission received"
    assert hosted_form.submission is not None
    assert hosted_form.submission.candidate is not None
    assert hosted_form.submission.candidate.secret.value == TOKEN


@pytest.mark.parametrize("operation", tuple(onboarding.Operation))
@pytest.mark.allow_hosts(["127.0.0.1"])
def test_hosted_form_requires_publication_consent_and_rejects_replay(
    hosted_form: onboarding.Server, operation: onboarding.Operation
) -> None:
    local_form = hosted_form
    enrollment = Enrollment("a" * 32, NOW + timedelta(days=20))
    local_form.request = onboarding.Request(
        local_form.service.credential,
        operation,
        enrollment if operation == onboarding.Operation.PUBLISH else None,
    )
    fields = {"publication": "yes"}
    if operation != onboarding.Operation.PUBLISH:
        fields.update(token=TOKEN, expires_at="2026-12-01", confirmed="yes")
    for denied in (
        fields | {"publication": "no"},
        {key: value for key, value in fields.items() if key != "publication"},
        fields | {"repository": "Example/other"},
    ):
        status = submit(local_form, body=urllib.parse.urlencode(denied))[0]
        assert (status, local_form.submission) == (400, None)
    status = submit(
        local_form,
        origin="https://evil.invalid",
        body=urllib.parse.urlencode(fields),
    )[0]
    assert (status, local_form.submission) == (403, None)
    assert submit(local_form, body=urllib.parse.urlencode(fields))[0] == 200
    submission = local_form.submission
    assert submission is not None and submission.request == local_form.request
    assert submit(local_form, body=urllib.parse.urlencode(fields))[0] == 403
    assert local_form.submission is submission


@pytest.mark.allow_hosts(["127.0.0.1"])
def test_abandoned_form_expires_without_disclosing_its_url(
    local_form: onboarding.Server,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    opened: list[str] = []

    def open_browser(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(webbrowser, "open", open_browser)
    with pytest.raises(CredentialError, match="credential-onboarding-timeout"):
        onboarding.receive(local_form.service, NOW, timeout=0)
    assert len(opened) == 2
    output = capsys.readouterr()
    assert opened[-1] not in output.out + output.err
    assert urllib.parse.urlsplit(opened[-1]).path not in output.out + output.err
    assert local_form.service.state.active() is None


def mcp_context(tmp_path: Path) -> Context:
    credential = Credential(
        "work",
        Provider.GITHUB_FINE_GRAINED,
        "Example",
        "example-user",
        (),
        (Endpoint(Client.MCP, "https://github.com/Example/private"),),
    )
    return Context(tmp_path, {}, tmp_path / "state", False, Declaration((credential,)))


def frame(identifier: int, method: str, params: object | None = None) -> str:
    return (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "method": method,
                "params": params or {},
            }
        )
        + "\n"
    )


def session(*frames: str) -> io.StringIO:
    return io.StringIO(
        frame(1, "initialize", {"protocolVersion": "2026-07-28", "capabilities": {}})
        + '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
        + "".join(frames)
    )


def test_mcp_lists_only_the_scoped_read_tools_without_opening_a_vault(
    tmp_path: Path,
) -> None:
    output = io.StringIO()
    assert (
        mcp.serve(
            mcp_context(tmp_path),
            mcp_release.LINUX_X64,
            session(frame(2, "tools/list"), frame(3, "ping")),
            output,
            lambda: NOW,
        )
        == 0
    )
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert responses[0]["result"]["protocolVersion"] == "2026-07-28"
    assert {tool["name"] for tool in responses[1]["result"]["tools"]} == {
        "list_branches",
        "get_file_contents",
        "pull_request_read",
    }
    assert responses[2]["result"] == {}


@pytest.mark.parametrize(
    "params",
    [
        {
            "name": "delete_repository",
            "arguments": {"owner": "Example", "repo": "private"},
        },
        {"name": "list_branches", "arguments": {"owner": "Other", "repo": "private"}},
        {
            "name": "list_branches",
            "arguments": {"owner": "Example", "repo": "../private"},
        },
        {
            "name": "list_branches",
            "arguments": {"owner": "Example", "repo": "private", "perPage": 1000},
        },
        {
            "name": "pull_request_read",
            "arguments": {
                "owner": "Example",
                "repo": "private",
                "pullNumber": 1,
                "method": "merge",
            },
        },
    ],
)
def test_mcp_refuses_unsupported_or_out_of_scope_calls_before_reading_credentials(
    tmp_path: Path, params: dict[str, object]
) -> None:
    output = io.StringIO()
    mcp.serve(
        mcp_context(tmp_path),
        mcp_release.LINUX_X64,
        session(frame(2, "tools/call", params)),
        output,
        lambda: NOW,
    )
    response = json.loads(output.getvalue().splitlines()[-1])
    assert response["error"]["code"] == -32602
    assert not (tmp_path / "state").exists()


def test_mcp_recovers_after_bad_json_and_does_not_echo_input(tmp_path: Path) -> None:
    output = io.StringIO()
    source = io.StringIO(
        '{"private":"fixture-sensitive-value", broken}\n'
        + session(frame(2, "tools/list")).getvalue()
    )
    mcp.serve(mcp_context(tmp_path), mcp_release.LINUX_X64, source, output, lambda: NOW)
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert responses[0]["error"]["code"] == -32700
    assert "tools" in responses[-1]["result"]
    assert "fixture-sensitive-value" not in output.getvalue()
