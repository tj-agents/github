"""Nonsecret provider used only by the real NuGet client acceptance fixture."""

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from credentials import http, nuget
from credentials.model import Client, Credential, Endpoint, Provider
from credentials.service import Service
from credentials.vault import Enrollment, Secret, State


@dataclass(frozen=True, slots=True)
class FixtureProvider:
    name: str = "fixture"

    def validate(self, credential: Credential) -> None:
        if credential.name != self.name:
            raise AssertionError("fixture credential required")

    def creation_url(self, credential: Credential) -> str:
        return "https://example.invalid/" + self.name

    def instructions(self, credential: Credential) -> str:
        return self.name

    def verify(
        self, credential: Credential, secret: Secret, transport: http.Transport
    ) -> datetime | None:
        if credential.name != self.name or secret.value != "fixture-only":
            raise AssertionError("only the nonsecret fixture credential is accepted")
        return None


class FixtureVault:
    def __init__(self) -> None:
        self.values = {"a" * 32: Secret("fixture-only")}

    def read(self, version: str) -> Secret:
        return self.values[version]

    def write(self, version: str, secret: Secret) -> None:
        self.values[version] = secret

    def erase(self, version: str) -> None:
        self.values.pop(version, None)


def no_network(url: str, headers: object) -> http.Response:
    raise AssertionError("the fixture provider must never contact an issuer")


def main() -> int:
    credential = Credential(
        "fixture",
        Provider.GITHUB_CLASSIC,
        "Example",
        "fixture-user",
        ("read:packages",),
        (
            Endpoint(
                Client.NUGET,
                "https://nuget.pkg.github.com/Example/index.json",
                "https://nuget.pkg.github.com/Example/download/sample/index.json",
            ),
        ),
    )
    state = State(Path(__file__).parent / "fixture-state", credential)
    state.select(Enrollment("a" * 32, datetime(2100, 1, 1, tzinfo=UTC)))
    service = Service(credential, state, FixtureVault(), FixtureProvider(), no_network)
    return nuget.serve(
        sys.stdin,
        sys.stdout,
        lambda client, url: service,
        datetime(2026, 11, 17, tzinfo=UTC),
        declared=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
