from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from gate_git import git, machine_environment
from gate_owner import gh_target
from gate_platform import Platform

from credentials import commands
from credentials.children import real_tool
from credentials.context import Context
from credentials.model import Client, CredentialError
from credentials.repository_scope import RepositoryScope
from credentials.vault import Secret, private_environment


def target_url(arguments: Sequence[str], context: Context) -> str:
    origin = git(
        ["remote", "get-url", "origin"],
        context.cwd,
        machine_environment(context.environment),
    )
    if origin.returncode:
        raise CredentialError(
            "credential-target-unknown: configure the owning HTTPS origin"
        )
    url = origin.stdout.decode("utf-8", "strict").strip()
    for index, argument in enumerate(arguments):
        if argument in {"-R", "--repo"} and index + 1 < len(arguments):
            value = arguments[index + 1]
            url = (
                value if value.startswith("https://") else "https://github.com/" + value
            )
        elif argument.startswith("--repo="):
            value = argument.partition("=")[2]
            url = (
                value if value.startswith("https://") else "https://github.com/" + value
            )
        elif argument.startswith("-R") and len(argument) > 2:
            url = "https://github.com/" + argument[2:]
    if list(arguments[:1]) == ["api"]:
        if len(arguments) < 2 or not arguments[1].lstrip("/").startswith("repos/"):
            raise CredentialError(
                "credential-command-denied: name a repository REST path"
            )
        parts = arguments[1].strip("/").split("/")
        if len(parts) < 3:
            raise CredentialError(
                "credential-request-invalid: incomplete repository path"
            )
        url = f"https://github.com/{parts[1]}/{parts[2]}"
    return url


def child_environment(
    context: Context, secret: Secret, directory: Path, platform: Platform
) -> dict[str, str]:
    environment = private_environment(context.environment)
    for key in tuple(environment):
        if key.upper().startswith(("GH_", "GITHUB_")) or key.upper() in {
            "BROWSER",
            "EDITOR",
            "PAGER",
            "VISUAL",
        }:
            environment.pop(key)
    environment.update(
        GH_TOKEN=secret.value,
        GH_HOST="github.com",
        GH_CONFIG_DIR=str(directory),
        GH_PROMPT_DISABLED="1",
        GIT_TERMINAL_PROMPT="0",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_CONFIG_COUNT="0",
        TEMP=str(directory),
        TMP=str(directory),
    )
    real_git = real_tool("git", context.environment)
    launchers = directory / "git-child"
    launchers.mkdir()
    path = launchers / platform.command_launcher_name("git")
    script = Path(__file__).parents[1] / "credential_git_child.py"
    platform.write_launcher(
        path,
        platform.command_launcher_bytes(Path(sys.executable), script, (str(real_git),)),
    )
    environment["PATH"] = str(launchers) + os.pathsep + environment.get("PATH", "")
    return environment


def run(
    arguments: Sequence[str],
    context: Context,
    now: datetime,
    executable: Path,
    platform: Platform,
    input_stream: IO[str] | None,
    output_stream: IO[str],
    error_stream: IO[str],
) -> int | None:
    if context.declaration is None or not context.declaration.supports(Client.GH):
        return None
    if list(arguments[:1]) == ["auth"] and list(arguments[1:]) != ["status"]:
        raise CredentialError(
            "credential-command-denied: use tj-credentials onboarding"
        )
    target = gh_target(arguments)
    if target.hostname is not None and target.hostname.casefold() != "github.com":
        raise CredentialError(
            "credential-target-denied: this credential is for github.com"
        )
    url = target_url(arguments, context)
    service = context.resolve(Client.GH, url)
    if service is None:
        raise CredentialError(
            "credential-target-denied: gh target has no declared credential"
        )
    scope = RepositoryScope.from_credential(service.credential)
    if any(
        owner.casefold() != scope.organization.casefold() for owner in target.owners
    ):
        raise CredentialError("credential-target-denied: command names another account")
    if list(arguments) == ["auth", "status"]:
        command = tuple(arguments)
    else:
        invocation = commands.validate_gh_command(
            scope, arguments, default_repository=url.removeprefix("https://github.com/")
        )
        if not any(
            endpoint.matches(
                Client.GH, "https://github.com/" + invocation.repository.slug
            )
            for endpoint in service.credential.endpoints
        ):
            raise CredentialError(
                "credential-target-denied: repository is not declared"
            )
        command = invocation.arguments
    input_text = None
    if command[-4:] == ("-X", "POST", "--input", "-"):
        if input_stream is None:
            raise CredentialError(
                "credential-request-invalid: review JSON must arrive on stdin"
            )
        input_text = commands.validate_pending_review_input(input_stream.read(131073))
    secret = service.authorize(now)
    with tempfile.TemporaryDirectory(prefix="tj-credentials-gh-") as directory:
        try:
            result = subprocess.run(
                [str(executable), *command],
                text=True,
                encoding="utf-8",
                input=input_text,
                capture_output=True,
                check=False,
                cwd=context.cwd,
                env=child_environment(context, secret, Path(directory), platform),
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            raise CredentialError(
                "credential-client-unavailable: gh did not complete safely"
            ) from None
    output_stream.write(result.stdout.replace(secret.value, "[redacted]"))
    error_stream.write(result.stderr.replace(secret.value, "[redacted]"))
    return result.returncode


def configured_run(
    arguments: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
    now: float,
    executable: Path,
    platform: Platform,
    input_stream: IO[str] | None,
    output_stream: IO[str],
    error_stream: IO[str],
) -> int | None:
    home = Path(
        environment.get("USERPROFILE") or environment.get("HOME") or "~"
    ).expanduser()
    state = Path(environment.get("AGENT_STATE_DIRECTORY", str(home / ".agents-state")))
    context = Context.load(
        cwd,
        environment,
        state,
        windows="SYSTEMROOT" in {key.upper() for key in environment},
    )
    return run(
        arguments,
        context,
        datetime.fromtimestamp(now, UTC),
        executable,
        platform,
        input_stream,
        output_stream,
        error_stream,
    )
