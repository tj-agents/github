"""Publish a verified workflow read credential from the owner's terminal."""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from gate_agent import agent_host
from gate_git import git, machine_environment

from credentials import children, model
from credentials.context import Context
from credentials.model import ActionsSecret, Credential, CredentialError
from credentials.service import Service
from credentials.vault import Secret, private_environment


@dataclass(frozen=True, slots=True)
class Publisher:
    target: ActionsSecret
    gh: Path
    environment: Mapping[str, str]

    def _call(self, *args: str, input_secret: Secret | None = None) -> bytes:
        with tempfile.TemporaryDirectory(prefix="tj-credentials-gh-") as cwd:
            try:
                result = subprocess.run(
                    [str(self.gh), *args],
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
    def prepare(cls, context: Context, credential: Credential) -> Publisher:
        target = credential.actions_secret
        if target is None:
            raise CredentialError("actions-undeclared: select a hosted credential")
        env = context.environment
        if (
            agent_host(env) is not None
            or env.get("TJ_AGENT") == "1"
            or any(
                env.get(key)
                for key in (
                    "CODEX_SESSION_ID",
                    "GH_TOKEN",
                    "GITHUB_TOKEN",
                    "GH_CONFIG_DIR",
                    "GH_ENTERPRISE_TOKEN",
                    "GITHUB_ENTERPRISE_TOKEN",
                    "GITHUB_ACTIONS",
                )
            )
        ):
            raise CredentialError(
                "actions-owner-required: run this command in your own terminal"
            )
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
        child = private_environment(env)
        child.update(GH_HOST="github.com", GH_PROMPT_DISABLED="1")
        publisher = cls(target, children.real_tool("gh", env), child)
        actor = publisher._call("api", "user", "--jq", ".login")
        if actor.decode().casefold() != credential.login.casefold():
            raise CredentialError(
                "actions-owner-mismatch: check your GitHub CLI sign-in"
            )
        remote = publisher._call(
            "api", f"repos/{target.repository}", "--jq", ".full_name"
        )
        if remote.decode().casefold() != target.repository.casefold():
            raise CredentialError(
                "actions-repository-mismatch: check the remote destination"
            )
        if not publisher._call(
            "api",
            f"repos/{target.repository}/actions/secrets/public-key",
            "--jq",
            ".key_id // empty",
        ):
            raise CredentialError(
                "actions-owner-access-denied: missing repository encryption key"
            )
        return publisher

    def publish(self, service: Service) -> dict[str, str]:
        target = self.target
        with service.state.lock():
            secret = service.authorize(datetime.now(UTC))
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
