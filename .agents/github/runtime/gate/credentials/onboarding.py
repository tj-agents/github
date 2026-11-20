from __future__ import annotations

import html
import http.server
import secrets
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, override

from credentials.model import CredentialError
from credentials.service import Service
from credentials.vault import Secret


@dataclass(frozen=True, slots=True)
class Candidate:
    secret: Secret
    expires_at: datetime


class Server(http.server.HTTPServer):
    nonce: str
    service: Service
    now: datetime
    candidate: Candidate | None = None

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
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; form-action 'self'; frame-ancestors 'none'",
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
        button = (
            "Verify and publish to GitHub Actions"
            if service.credential.actions_secret is not None
            else "Verify and save in the OS vault"
        )
        self.reply(
            200,
            "<!doctype html><title>Credential enrollment</title>"
            f"<h1>{html.escape(service.credential.name)}</h1><p>{instructions}</p>"
            '<form method="post"><label>Credential '
            '<input type="password" name="token" autocomplete="off" required>'
            '</label><label>Token expiry <input type="date" name="expires_at" '
            f'min="{first}" max="{last}" value="{last}" required></label>'
            '<label><input type="checkbox" name="confirmed" value="yes" required>'
            "I selected this account, organization, scopes and expiry, "
            "and completed required SSO authorization.</label>"
            f"<button>{button}</button></form>",
        )

    def do_POST(self) -> None:
        origin = f"http://127.0.0.1:{self.server.server_port}"
        if (
            not self.allowed()
            or self.headers.get_all("Origin") != [origin]
            or self.server.candidate is not None
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
            fields = urllib.parse.parse_qs(body, strict_parsing=True, max_num_fields=3)
            if (
                set(fields) != {"token", "expires_at", "confirmed"}
                or any(len(values) != 1 for values in fields.values())
                or fields["confirmed"] != ["yes"]
            ):
                raise ValueError
            secret = Secret(fields["token"][0])
            expires_at = datetime.strptime(fields["expires_at"][0], "%Y-%m-%d").replace(
                tzinfo=UTC
            )
            if (
                not self.server.now
                < expires_at
                <= self.server.now
                + timedelta(days=self.server.service.credential.expires_in)
            ):
                raise ValueError
        except (OSError, ValueError, CredentialError):
            self.reply(
                400, "Submit the credential, its expiry and authorization confirmation"
            )
            return
        self.server.candidate = Candidate(secret, expires_at)
        self.reply(
            200,
            "<!doctype html><title>Credential received</title>"
            "<p>Credential received. Check the terminal for verification "
            "and secure storage results.</p>",
        )


def receive(service: Service, now: datetime, timeout: float = 900) -> Candidate:
    with Server(("127.0.0.1", 0), Handler) as server:
        server.nonce, server.service, server.now = (
            secrets.token_urlsafe(32),
            service,
            now,
        )
        server.timeout = 1
        creation = service.provider.creation_url(service.credential)
        local = f"http://127.0.0.1:{server.server_port}/{server.nonce}"
        print("Create credential: " + creation, flush=True)
        print("Submit locally: " + local, flush=True)
        webbrowser.open(creation)
        webbrowser.open(local)
        deadline = time.monotonic() + timeout
        while server.candidate is None and time.monotonic() < deadline:
            server.handle_request()
        if server.candidate is None:
            raise CredentialError("credential-onboarding-timeout: run onboarding again")
        return server.candidate
