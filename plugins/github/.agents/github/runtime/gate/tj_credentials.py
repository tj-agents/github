"""Declared credential enrollment, preflight and native client adapters."""

import argparse
import json
import os
import platform
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from credentials import (
    azure_cli,
    children,
    docker,
    dotnet,
    mcp,
    mcp_install,
    mcp_release,
    npm,
    nuget,
    onboarding,
)
from credentials.context import Context
from credentials.model import Client, CredentialError, Tool


class Runner(Protocol):
    def run(
        self,
        context: Context,
        executable: Path,
        arguments: Sequence[str],
        now: datetime,
    ) -> int: ...


RUNNERS: Mapping[Tool, Runner] = {
    Tool.DOTNET: dotnet,
    Tool.NPM: npm,
    Tool.DOCKER: docker,
    Tool.AZURE_CLI: azure_cli,
}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    check.add_argument("--name")
    for name in ("onboard", "rotate"):
        onboard = commands.add_parser(name)
        onboard.add_argument("--name", required=True)
    plugin = commands.add_parser("nuget")
    plugin.add_argument("-Plugin", action="store_true", required=True, dest="plugin")
    commands.add_parser("mcp")
    execute = commands.add_parser("exec")
    execute.add_argument("--tool", type=Tool, choices=tuple(Tool), required=True)
    execute.add_argument("arguments", nargs=argparse.REMAINDER)
    return result


def check(context: Context, name: str | None, now: datetime) -> int:
    if context.declaration is None:
        print(json.dumps({"status": "not-declared"}))
        return 0
    credentials = (
        (context.declaration.named(name),) if name else context.declaration.credentials
    )
    results: list[dict[str, object]] = []
    failed = False
    for credential in credentials:
        try:
            health = context.service(credential).check(now)
        except CredentialError as error:
            failed = True
            results.append(
                {
                    "name": credential.name,
                    "status": "unavailable",
                    "reason": str(error),
                    "action": credential.action,
                }
            )
        else:
            results.append(asdict(health))
    print(json.dumps({"credentials": results}))
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    if sys.version_info < (3, 12):  # noqa: UP036 - shipped outside the dev environment
        print("Python 3.12 or newer is required", file=sys.stderr)
        return 2
    arguments = parser().parse_args(argv)
    if arguments.command in {"nuget", "mcp"}:
        for stream in (sys.stdin, sys.stdout):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(encoding="utf-8", errors="strict")
    environment = dict(os.environ)
    now = datetime.now(UTC)
    state_root = Path(
        environment.get("AGENT_STATE_DIRECTORY", str(Path.home() / ".agents-state"))
    )
    try:
        context = Context.load(
            Path.cwd(), environment, state_root, windows=os.name == "nt"
        )
        if arguments.command == "check":
            return check(context, arguments.name, now)
        if arguments.command == "exec":
            client_arguments = arguments.arguments
            if client_arguments[:1] == ["--"]:
                client_arguments = client_arguments[1:]
            real = children.real_tool(arguments.tool.value, environment)
            return RUNNERS[arguments.tool].run(context, real, client_arguments, now)
        if arguments.command == "mcp":
            release = mcp_release.select(platform.system(), platform.machine())
            if context.declaration is not None and context.declaration.supports(
                Client.MCP
            ):
                mcp_install.install(state_root, release)
            return mcp.serve(
                context, release, sys.stdin, sys.stdout, lambda: datetime.now(UTC)
            )
        if arguments.command == "nuget":
            configured = (
                context.declaration is not None
                and context.declaration.supports(Client.NUGET)
            )
            return nuget.serve(
                sys.stdin, sys.stdout, context.resolve, now, declared=configured
            )
        service = context.named(arguments.name)
        candidate = onboarding.receive(service, now)
        health = service.onboard(
            candidate.secret, candidate.expires_at, datetime.now(UTC)
        )
        print(json.dumps(asdict(health)))
    except CredentialError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print(
            "credential-operation-failed: check the declared tools and public state",
            file=sys.stderr,
        )
        return 1
    else:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
