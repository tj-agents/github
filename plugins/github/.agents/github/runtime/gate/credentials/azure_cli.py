from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from credentials import children
from credentials.context import Context
from credentials.model import Client, CredentialError
from credentials.vault import private_environment


def run(
    context: Context, executable: Path, arguments: Sequence[str], now: datetime
) -> int:
    if (
        not arguments
        or arguments[0] not in {"boards", "repos", "pipelines", "artifacts", "devops"}
        or context.declaration is None
    ):
        return children.execute(executable, arguments, context.cwd, context.environment)
    selected = [
        credential
        for credential in context.declaration.credentials
        if credential.supports(Client.AZURE_DEVOPS)
    ]
    if not selected:
        return children.execute(executable, arguments, context.cwd, context.environment)
    if len(selected) != 1:
        raise CredentialError(
            "credential-client-conflict: select one Azure DevOps "
            "organization per repository"
        )
    credential = selected[0]
    organization = f"https://dev.azure.com/{credential.owner}"
    supplied = []
    command = list(arguments)
    for index, argument in enumerate(command):
        if argument in {"--organization", "--org"}:
            if index + 1 == len(command):
                raise CredentialError(
                    "credential-request-invalid: Azure organization is missing"
                )
            supplied.append(command[index + 1])
        elif argument.startswith(("--organization=", "--org=")):
            supplied.append(argument.partition("=")[2])
    if any(
        value.rstrip("/").casefold() != organization.casefold() for value in supplied
    ):
        raise CredentialError(
            "credential-target-denied: Azure command names another organization"
        )
    if not supplied:
        command.extend(("--organization", organization))
    current = context.service(credential)
    current.check(now)
    environment = private_environment(context.environment)
    environment["AZURE_DEVOPS_EXT_PAT"] = current.secret(now).value
    return children.execute(executable, command, context.cwd, environment)
