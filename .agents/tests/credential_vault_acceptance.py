"""Exercise the installed OS vault with a unique, nonsecret fixture entry."""

import json
import os
import secrets

from credentials.model import Client, Credential, CredentialError, Endpoint, Provider
from credentials.vault import GCMVault, Secret


def main() -> int:
    credential = Credential(
        "native-vault-fixture",
        Provider.GITHUB_CLASSIC,
        "fixture-" + secrets.token_hex(8),
        "fixture-user",
        ("read:packages",),
        (Endpoint(Client.NUGET, "https://nuget.pkg.github.com/Example/index.json"),),
    )
    vault = GCMVault(credential, dict(os.environ), windows=os.name == "nt")
    version = secrets.token_hex(16)
    try:
        vault.write(version, Secret("nonsecret-native-vault-fixture"))
        if vault.read(version).value != "nonsecret-native-vault-fixture":
            raise AssertionError("native secure-store readback failed")
    finally:
        vault.erase(version)
    try:
        vault.read(version)
    except CredentialError as error:
        if not str(error).startswith("credential-missing:"):
            raise
    else:
        raise AssertionError("native secure-store fixture was not removed")
    print(json.dumps({"native_vault": "passed", "fixture_removed": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
