from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from credentials import children, github_classic, npm
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
from credentials.vault import Enrollment, Secret, State

NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)
VERIFIED = Secret("ghp_" + "0" * 36)
REPLACEMENT = Secret("ghp_" + "1" * 36)
EXPIRY = NOW + timedelta(days=20)


class RotationVault:
    def __init__(self) -> None:
        self.values: dict[str, Secret] = {}
        self.reads: list[str] = []
        self.on_read: Callable[[str], None] | None = None

    def read(self, version: str) -> Secret:
        self.reads.append(version)
        if self.on_read is not None:
            self.on_read(version)
        try:
            return self.values[version]
        except KeyError:
            raise CredentialError(
                "credential-missing: fixture version retired"
            ) from None

    def write(self, version: str, secret: Secret) -> None:
        self.values[version] = secret

    def erase(self, version: str) -> None:
        self.values.pop(version, None)


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
                Client.NPM,
                "https://npm.pkg.github.com",
                "https://npm.pkg.github.com/@example/sample",
                "@example",
            ),
        ),
    )


def test_npm_uses_the_verified_token_when_rotation_publishes_during_package_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    credential: Credential,
) -> None:
    vault = RotationVault()
    state = State(tmp_path / "state", credential)
    previous = state.save(vault, VERIFIED, EXPIRY)
    probes: list[str] = []

    def transport(url: str, headers: Mapping[str, str]) -> Response:
        assert headers["Authorization"] == "Bearer " + VERIFIED.value
        if url == "https://api.github.com/user":
            return Response(
                200, {"x-oauth-scopes": "read:packages"}, b'{"login":"example-user"}'
            )
        assert url == "https://npm.pkg.github.com/@example%2fsample"
        probes.append(url)
        state.save(vault, REPLACEMENT, EXPIRY)
        return Response(200, {}, b"{}")

    current = Service(credential, state, vault, github_classic, transport)

    def service(context: Context, selected: Credential) -> Service:
        assert selected == credential
        return current

    received: list[str] = []

    def execute(
        executable: Path,
        arguments: Sequence[str],
        cwd: Path,
        environment: Mapping[str, str],
    ) -> int:
        public = Path(environment["NPM_CONFIG_USERCONFIG"]).read_text(encoding="utf-8")
        assignment = next(
            line for line in public.splitlines() if ":_authToken=" in line
        )
        reference = assignment.partition("=")[2]
        assert reference.startswith("${") and reference.endswith("}")
        received.append(environment[reference[2:-1]])
        return 0

    monkeypatch.setattr(Context, "service", service)
    monkeypatch.setattr(children, "execute", execute)
    context = Context(
        tmp_path,
        {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
        tmp_path / "state",
        False,
        Declaration((credential,)),
    )
    assert npm.run(context, tmp_path / "npm", ("install", "@example/sample"), NOW) == 0
    assert probes == ["https://npm.pkg.github.com/@example%2fsample"]
    assert previous.version not in vault.values
    assert state.read(vault, NOW) == REPLACEMENT
    assert received == [VERIFIED.value]


def test_state_read_recovers_when_rotation_retires_the_selected_version(
    tmp_path: Path,
    credential: Credential,
) -> None:
    vault = RotationVault()
    state = State(tmp_path, credential)
    previous = state.save(vault, VERIFIED, EXPIRY)

    def retire(version: str) -> None:
        if version == previous.version:
            state.save(vault, REPLACEMENT, EXPIRY)

    vault.on_read = retire
    assert state.read(vault, NOW) == REPLACEMENT
    assert previous.version not in vault.values
    assert state.active() != previous


def test_state_read_does_not_retry_a_vault_failure_without_rotation(
    tmp_path: Path,
    credential: Credential,
) -> None:
    vault = RotationVault()
    state = State(tmp_path, credential)
    previous = state.save(vault, VERIFIED, EXPIRY)
    vault.reads.clear()

    def fail_first_read(version: str) -> None:
        if len(vault.reads) == 1:
            raise CredentialError("vault-unavailable: fixture vault locked")

    vault.on_read = fail_first_read
    with pytest.raises(
        CredentialError, match="vault-unavailable: fixture vault locked"
    ):
        state.read(vault, NOW)
    assert vault.reads == [previous.version]


def test_state_read_stops_after_a_second_concurrent_retirement(
    tmp_path: Path,
    credential: Credential,
) -> None:
    vault = RotationVault()
    state = State(tmp_path, credential)
    first = Enrollment("1" * 32, EXPIRY)
    second = Enrollment("2" * 32, EXPIRY)
    third = Enrollment("3" * 32, EXPIRY)
    vault.values.update(
        {
            first.version: VERIFIED,
            second.version: REPLACEMENT,
            third.version: Secret("fixture-third-token"),
        }
    )
    state.select(first)
    successors = {first.version: second, second.version: third}

    def retire(version: str) -> None:
        state.select(successors[version])
        vault.erase(version)

    vault.on_read = retire
    with pytest.raises(
        CredentialError, match="credential-missing: fixture version retired"
    ):
        state.read(vault, NOW)
    assert vault.reads == [first.version, second.version]
    assert state.active() == third


def test_state_read_rejects_expired_enrollment_before_reading_the_vault(
    tmp_path: Path,
    credential: Credential,
) -> None:
    vault = RotationVault()
    state = State(tmp_path, credential)
    state.select(Enrollment("1" * 32, NOW))
    with pytest.raises(CredentialError, match="credential-expired"):
        state.read(vault, NOW)
    assert vault.reads == []


def test_state_read_validates_expiry_again_after_a_concurrent_retirement(
    tmp_path: Path,
    credential: Credential,
) -> None:
    vault = RotationVault()
    state = State(tmp_path, credential)
    previous = state.save(vault, VERIFIED, EXPIRY)
    expired = Enrollment("2" * 32, NOW)
    vault.values[expired.version] = REPLACEMENT
    vault.reads.clear()

    def retire(version: str) -> None:
        state.select(expired)
        vault.erase(version)

    vault.on_read = retire
    with pytest.raises(CredentialError, match="credential-expired"):
        state.read(vault, NOW)
    assert vault.reads == [previous.version]
