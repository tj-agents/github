from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from credentials import children
from credentials.context import Context
from credentials.model import Client
from credentials.vault import private_environment


def run(
    context: Context, executable: Path, arguments: Sequence[str], now: datetime
) -> int:
    if (
        not arguments
        or arguments[0]
        in {"--version", "--info", "--list-sdks", "--list-runtimes", "--help", "-h"}
        or context.declaration is None
    ):
        return children.execute(executable, arguments, context.cwd, context.environment)
    environment = private_environment(context.environment)
    for credential in context.declaration.credentials:
        placeholders = [
            endpoint.variable
            for endpoint in credential.endpoints
            if endpoint.client == Client.NUGET
        ]
        if placeholders:
            secret = context.service(credential).authorize(now)
            environment.update(
                (placeholder, secret.value)
                for placeholder in placeholders
                if placeholder
            )
    return children.execute(executable, arguments, context.cwd, environment)
