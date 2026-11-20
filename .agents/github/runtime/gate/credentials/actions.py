"""Publish a workflow read credential after consent in the broker's browser form."""

from __future__ import annotations

import secrets
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from gate_git import git, machine_environment

from credentials import children, model
from credentials.context import Context
from credentials.model import ActionsSecret, Credential, CredentialError
from credentials.onboarding import Operation, Submission
from credentials.service import Service
from credentials.vault import Secret, private_environment


@dataclass(frozen=True, slots=True)
class Publisher:
    target: ActionsSecret
    environment: Mapping[str, str]
    context: Context
    credential: Credential
    operation: Operation

    def _call(self, *args: str, input_secret: Secret | None = None) -> bytes:
        with tempfile.TemporaryDirectory(prefix="tj-credentials-gh-") as cwd:
            try:
                result = subprocess.run(
                    [str(children.real_tool("gh", self.environment)), *args],
                    input=input_secret.value.encode() if input_secret else b"",
                    capture_output=True,
                    check=False,
                    timeout=45,
                    env=self.environment,
                    cwd=cwd,
                )
            except (OSError, subprocess.SubprocessError):
                raise CredentialError(
                    "actions-client-unavailable: gh did not finish"
                ) from None
        if result.returncode:
            raise CredentialError(
                f"actions-owner-operation-failed: gh {args[0]} exited "
                f"{result.returncode} for "
                f"{self.target.repository} using the owner's sign-in"
            )
        return result.stdout.strip()

    @classmethod
    def prepare(
        cls, context: Context, credential: Credential, operation: Operation
    ) -> Publisher:
        target = credential.actions_secret
        if target is None:
            raise CredentialError("actions-undeclared: select a hosted credential")
        env = context.environment
        if any(
            env.get(key)
            for key in (
                "GH_TOKEN",
                "GITHUB_TOKEN",
                "GH_CONFIG_DIR",
                "GH_ENTERPRISE_TOKEN",
                "GITHUB_ENTERPRISE_TOKEN",
                "GITHUB_ACTIONS",
            )
        ):
            raise CredentialError(
                "actions-credential-override: use the configured GitHub CLI sign-in"
            )
        child = private_environment(env)
        child.update(GH_HOST="github.com", GH_PROMPT_DISABLED="1")
        publisher = cls(target, child, context, credential, operation)
        publisher.validate_destination()
        return publisher

    def validate_destination(self) -> None:
        context, credential, target = self.context, self.credential, self.target
        env = context.environment
        safe = machine_environment(env)
        declaration = model.at_revision(context.cwd, safe, "HEAD")
        if declaration is None or credential not in declaration.credentials:
            raise CredentialError(
                "actions-undeclared: commit the destination in this checkout"
            )
        origin = git(["remote", "get-url", "origin"], context.cwd, safe)
        expected = "https://github.com/" + target.repository.casefold()
        address = origin.stdout.decode().strip().casefold().removesuffix(".git")
        if origin.returncode or address != expected:
            raise CredentialError(
                "actions-origin-mismatch: use the declared destination checkout"
            )

    def verify_owner(self) -> None:
        target = self.target
        actor = self._call("api", "user", "--jq", ".login")
        if actor.decode().casefold() != self.credential.login.casefold():
            raise CredentialError(
                "actions-owner-mismatch: check your GitHub CLI sign-in"
            )
        remote = self._call("api", f"repos/{target.repository}", "--jq", ".full_name")
        if remote.decode().casefold() != target.repository.casefold():
            raise CredentialError(
                "actions-repository-mismatch: check the remote destination"
            )
        if not self._call(
            "api",
            f"repos/{target.repository}/actions/secrets/public-key",
            "--jq",
            ".key_id // empty",
        ):
            raise CredentialError(
                "actions-owner-access-denied: missing repository encryption key"
            )

    def publish(self, service: Service, submission: Submission) -> dict[str, str]:
        if (
            submission.request.credential != self.credential
            or submission.request.operation != self.operation
            or service.credential != self.credential
        ):
            raise CredentialError("actions-consent-mismatch: reopen onboarding")
        self.validate_destination()
        submission.request.check_enrollment(service.state)
        self.verify_owner()
        candidate = submission.candidate
        if candidate is not None:
            service.onboard(candidate.secret, candidate.expires_at, datetime.now(UTC))
        target = self.target
        with service.state.lock():
            self.validate_destination()
            submission.request.check_enrollment(service.state)
            secret = service.authorize(datetime.now(UTC))
            if candidate is not None and not secrets.compare_digest(
                secret.value, candidate.secret.value
            ):
                raise CredentialError("actions-enrollment-changed: reopen onboarding")
            self._call(
                "secret",
                "set",
                target.name,
                "--app",
                "actions",
                "--repo",
                "github.com/" + target.repository,
                input_secret=secret,
            )
            name = self._call(
                "api",
                f"repos/{target.repository}/actions/secrets/{target.name}",
                "--jq",
                ".name",
            )
            if name != target.name.encode():
                raise CredentialError(
                    "actions-secret-unverified: missing matching secret metadata"
                )
        return {
            "status": "published",
            "repository": target.repository,
            "secret": target.name,
            "verification": "rerun the consuming workflow",
        }
