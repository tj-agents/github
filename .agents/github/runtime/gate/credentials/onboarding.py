from __future__ import annotations

import html
import http.server
import secrets
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, override

from credentials.model import Credential, CredentialError
from credentials.service import Service
from credentials.vault import Enrollment, Secret, State

FORM_STYLE = """<style>
  :root { color-scheme: light dark; }
  body {
    max-width: 42rem;
    margin: 3rem auto;
    padding: 0 1rem;
    font: 1rem/1.5 system-ui, sans-serif;
  }
  h1 { font-size: 1.5rem; }
  form, .field { display: grid; gap: .5rem; }
  form { gap: 1rem; }
  input, button { font: inherit; }
  .field input { box-sizing: border-box; max-width: 100%; padding: .4rem; }
  .confirmation {
    display: grid;
    grid-template-columns: auto 1fr;
    align-items: baseline;
    gap: .5rem;
  }
  .confirmation input { margin: 0; }
  button { justify-self: start; padding: .4rem .8rem; }
</style>"""


class Operation(StrEnum):
    ONBOARD = "onboard"
    ROTATE = "rotate"
    PUBLISH = "publish"


@dataclass(frozen=True, slots=True)
class Candidate:
    secret: Secret
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Request:
    credential: Credential
    operation: Operation
    enrollment: Enrollment | None = None

    def __post_init__(self) -> None:
        if self.operation == Operation.PUBLISH and (
            self.credential.actions_secret is None or self.enrollment is None
        ):
            raise CredentialError(f"credential-missing: {self.credential.action}")

    def check_enrollment(self, state: State) -> None:
        if self.operation == Operation.PUBLISH and state.active() != self.enrollment:
            raise CredentialError("actions-enrollment-changed: reopen onboarding")


@dataclass(frozen=True, slots=True)
class Submission:
    request: Request
    candidate: Candidate | None

    def __post_init__(self) -> None:
        if (self.request.operation == Operation.PUBLISH) != (self.candidate is None):
            raise CredentialError("credential-submission-invalid: reopen onboarding")


class Server(http.server.HTTPServer):
    nonce: str
    service: Service
    now: datetime
    request: Request
    submission: Submission | None = None

    def get_request(self) -> tuple[Any, Any]:
        connection, address = super().get_request()
        connection.settimeout(3)
        return connection, address

    @override
    def handle_error(self, request: object, client_address: object) -> None:
        pass


class Handler(http.server.BaseHTTPRequestHandler):
    server: Server

    @override
    def log_message(self, message: str, *args: object) -> None:
        pass

    def reply(self, status: int, body: str) -> None:
        content = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        # A form POST needs its Origin header. The referrer omits the nonce path.
        self.send_header("Referrer-Policy", "strict-origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; "
            "form-action 'self'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(content)

    def allowed(self) -> bool:
        host = f"127.0.0.1:{self.server.server_port}"
        return self.headers.get_all("Host") == [host] and secrets.compare_digest(
            self.path, "/" + self.server.nonce
        )

    def do_GET(self) -> None:
        if not self.allowed():
            self.reply(404, "Not found")
            return
        service = self.server.service
        last = (
            (self.server.now + timedelta(days=service.credential.expires_in))
            .date()
            .isoformat()
        )
        first = (self.server.now + timedelta(days=1)).date().isoformat()
        instructions = html.escape(service.provider.instructions(service.credential))
        target = service.credential.actions_secret
        button = (
            "Verify and publish to GitHub Actions"
            if service.credential.actions_secret is not None
            else "Verify and save in the OS vault"
        )
        fields = (
            '<label class="field">Credential'
            '<input type="password" name="token" autocomplete="off" required>'
            '</label><label class="field">Token expiry'
            '<input type="date" name="expires_at" '
            f'min="{first}" max="{last}" value="{last}" required></label>'
            '<label class="confirmation">'
            '<input type="checkbox" name="confirmed" value="yes" required>'
            "<span>"
            "I selected this account, organization, scopes and expiry, "
            "and completed required SSO authorization.</span></label>"
        )
        if self.server.request.operation == Operation.PUBLISH:
            fields = (
                "<p>Recheck and publish the credential "
                "already saved in the OS vault.</p>"
            )
        if target is not None:
            fields += (
                '<label class="confirmation">'
                '<input type="checkbox" name="publication" value="yes" required>'
                "<span>"
                f"I authorize {html.escape(self.server.request.operation.value)} "
                f"using GitHub account {html.escape(service.credential.login)} "
                f"to replace Actions secret {html.escape(target.name)} in "
                f"{html.escape(target.repository)} with the credential for "
                "the source repositories and read permissions listed above."
                "</span></label>"
            )
        self.reply(
            200,
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            "<title>Credential enrollment</title>"
            f"{FORM_STYLE}<main>"
            f"<h1>{html.escape(service.credential.name)}</h1><p>{instructions}</p>"
            f'<form method="post">{fields}'
            f"<button>{button}</button></form></main></html>",
        )

    def do_POST(self) -> None:
        origin = f"http://127.0.0.1:{self.server.server_port}"
        if (
            not self.allowed()
            or self.headers.get_all("Origin") != [origin]
            or self.server.submission is not None
        ):
            self.reply(403, "Submission rejected")
            return
        lengths = self.headers.get_all("Content-Length") or []
        if (
            len(lengths) != 1
            or not lengths[0].isdigit()
            or not 1 <= int(lengths[0]) <= 8192
            or self.headers.get_all("Content-Type")
            != ["application/x-www-form-urlencoded"]
            or self.headers.get("Transfer-Encoding") is not None
        ):
            self.reply(400, "Invalid submission")
            return
        try:
            body = self.rfile.read(int(lengths[0])).decode("ascii")
            fields = urllib.parse.parse_qs(body, strict_parsing=True, max_num_fields=4)
            requested = self.server.request
            expected = {"token", "expires_at", "confirmed"}
            if requested.operation == Operation.PUBLISH:
                expected = set()
            if requested.credential.actions_secret is not None:
                expected.add("publication")
            if (
                set(fields) != expected
                or any(len(values) != 1 for values in fields.values())
                or any(
                    fields[key] != ["yes"]
                    for key in expected & {"confirmed", "publication"}
                )
            ):
                raise ValueError
            candidate = None
            if requested.operation != Operation.PUBLISH:
                secret = Secret(fields["token"][0])
                expires_at = datetime.strptime(
                    fields["expires_at"][0], "%Y-%m-%d"
                ).replace(tzinfo=UTC)
                if (
                    not self.server.now
                    < expires_at
                    <= self.server.now
                    + timedelta(days=self.server.service.credential.expires_in)
                ):
                    raise ValueError
                candidate = Candidate(secret, expires_at)
        except (OSError, ValueError, CredentialError):
            self.reply(
                400, "Complete the requested fields and authorization confirmation"
            )
            return
        self.server.submission = Submission(requested, candidate)
        self.reply(
            200,
            "<!doctype html><title>Submission received</title>"
            "<p>Submission received. The broker will report verification "
            "and publication results to the calling session.</p>",
        )


def receive(
    service: Service,
    now: datetime,
    *,
    operation: Operation = Operation.ONBOARD,
    timeout: float = 900,
) -> Submission:
    request = Request(
        service.credential,
        operation,
        service.state.active() if operation == Operation.PUBLISH else None,
    )
    with Server(("127.0.0.1", 0), Handler) as server:
        server.nonce, server.service, server.now = (
            secrets.token_urlsafe(32),
            service,
            now,
        )
        server.request = request
        server.timeout = 1
        local = f"http://127.0.0.1:{server.server_port}/{server.nonce}"
        try:
            if operation != Operation.PUBLISH and not webbrowser.open(
                service.provider.creation_url(service.credential)
            ):
                raise CredentialError(
                    "credential-browser-unavailable: configure a default browser"
                )
            if not webbrowser.open(local):
                raise CredentialError(
                    "credential-browser-unavailable: configure a default browser"
                )
        except (OSError, webbrowser.Error):
            raise CredentialError(
                "credential-browser-unavailable: could not open onboarding"
            ) from None
        print(
            "Credential browser flow opened; waiting for human submission.", flush=True
        )
        deadline = time.monotonic() + timeout
        while server.submission is None and time.monotonic() < deadline:
            server.handle_request()
        if server.submission is None:
            raise CredentialError("credential-onboarding-timeout: run onboarding again")
        return server.submission
