from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from credentials import http
from credentials.model import Credential, CredentialError
from credentials.providers import CredentialProvider
from credentials.vault import Enrollment, Secret, State, Vault

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Health:
    name: str
    status: str
    expires_at: str
    action: str | None


@dataclass(frozen=True, slots=True)
class Service:
    credential: Credential
    state: State
    vault: Vault
    provider: CredentialProvider
    transport: http.Transport

    def _inspect(self, now: datetime) -> tuple[Health, Secret]:
        enrollment, secret = self.state.snapshot(self.vault, now)
        actual_expiry = self.provider.verify(self.credential, secret, self.transport)
        expiry = (
            min(actual_expiry, enrollment.expires_at)
            if actual_expiry is not None
            else enrollment.expires_at
        )
        current = Enrollment(enrollment.version, expiry)
        status = current.status(self.credential, now)
        if status == "expiring":
            LOGGER.warning(
                "Credential %s expires at %s; %s",
                self.credential.name,
                expiry.isoformat(),
                self.credential.action,
            )
        return (
            Health(
                self.credential.name,
                status,
                expiry.isoformat(),
                self.credential.action if status == "expiring" else None,
            ),
            secret,
        )

    def check(self, now: datetime) -> Health:
        health, _ = self._inspect(now)
        return health

    def authorize(self, now: datetime) -> Secret:
        _, secret = self._inspect(now)
        return secret

    def onboard(self, secret: Secret, expires_at: datetime, now: datetime) -> Health:
        if expires_at.tzinfo is None or not now < expires_at <= now + timedelta(
            days=self.credential.expires_in
        ):
            raise CredentialError(
                "credential-expiry-invalid: choose an expiry "
                "within the declared lifetime"
            )
        actual_expiry = self.provider.verify(self.credential, secret, self.transport)
        expiry = (
            min(actual_expiry, expires_at) if actual_expiry is not None else expires_at
        )
        if expiry <= now:
            raise CredentialError(f"credential-expired: {self.credential.action}")
        enrollment = self.state.save(self.vault, secret, expiry)
        status = enrollment.status(self.credential, now)
        return Health(
            self.credential.name,
            status,
            expiry.isoformat(),
            self.credential.action if status == "expiring" else None,
        )
