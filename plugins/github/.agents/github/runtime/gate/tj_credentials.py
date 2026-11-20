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
    actions,
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
from credentials.owner_receipt import OwnerReceipt


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
    Tool.PNPM: npm,
    Tool.DOCKER: docker,
    Tool.AZURE_CLI: azure_cli,
}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    check.add_argument("--name")
    for name in ("onboard", "rotate", "publish"):
        onboard = commands.add_parser(name)
        onboard.add_argument("--name", required=True)
        onboard.add_argument("--result-file", type=Path)
    plugin = commands.add_parser("nuget")
    plugin.add_argument("-Plugin", action="store_true", required=True, dest="plugin")
    commands.add_parser("mcp")
    execute = commands.add_parser("exec")
    execute.add_argument("--tool", type=Tool, choices=tuple(Tool), required=True)
    execute.add_argument("arguments", nargs=argparse.REMAINDER)
    return result


def check(context: Context, name: str | None, now: datetime) -> int:
    if name and (
        context.declaration is None
        or not any(item.name == name for item in context.declaration.credentials)
    ):
        print(
            json.dumps(
                {
                    "name": name,
                    "status": "not-declared",
                    "action": "commit the named credential in .agents/repository.json; "
                    "for a workflow token declare actions_secret, then rerun check",
                }
            )
        )
        return 1
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
            failure: dict[str, object] = {
                "name": credential.name,
                "status": "unavailable",
                "reason": str(error),
                "action": credential.action,
            }
            if credential.actions_secret is not None:
                failure.update(
                    repository=credential.actions_secret.repository,
                    secret=credential.actions_secret.name,
                    workflow_status="unverified",
                    owner_action=credential.action,
                )
            results.append(failure)
        else:
            record = asdict(health)
            if credential.actions_secret is not None:
                record.update(
                    repository=credential.actions_secret.repository,
                    secret=credential.actions_secret.name,
                    workflow_status="unverified",
                    owner_action=health.action
                    or f"tj-credentials publish --name {credential.name}",
                )
            results.append(record)
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
    receipt: OwnerReceipt | None = None
    try:
        if getattr(arguments, "result_file", None) is not None:
            receipt = OwnerReceipt.start(
                arguments.result_file, arguments.command, arguments.name
            )
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
        credential = context.named(arguments.name)
        if receipt and credential.actions_secret is None:
            raise CredentialError(
                "actions-undeclared: owner receipts require a hosted credential"
            )
        publisher = (
            actions.Publisher.prepare(
                context, credential, onboarding.Operation(arguments.command)
            )
            if credential.actions_secret is not None or arguments.command == "publish"
            else None
        )
        service = context.service(credential)
        submission = onboarding.receive(
            service, now, operation=onboarding.Operation(arguments.command)
        )
        if publisher is None:
            candidate = submission.candidate
            if candidate is None:
                raise CredentialError("actions-undeclared: select a hosted credential")
            health = service.onboard(
                candidate.secret, candidate.expires_at, datetime.now(UTC)
            )
            print(json.dumps(asdict(health)))
            return 0
        result = publisher.publish(service, submission)
        if receipt:
            receipt.write("complete", 0, result)
        print(json.dumps(result))
    except CredentialError as error:
        if receipt:
            receipt.failed(str(error))
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError):
        if receipt:
            receipt.failed("credential-operation-failed")
        print(
            "credential-operation-failed: check the declared tools and public state",
            file=sys.stderr,
        )
        return 1
    else:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
