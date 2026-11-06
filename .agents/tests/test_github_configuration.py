from typing import Any

import pytest
from configuration_declaration import (
    DeclarationError,
    Package,
    PackageType,
    Role,
    parse,
)
from configuration_packages import Change, changes


def declaration(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": 1,
        "owner": "example",
        "packages": {
            "nuget": {"Example.Kernel": {"actions_access": {"platform": "write"}}}
        },
    }
    document.update(overrides)
    return document


def test_parses_package_actions_access() -> None:
    parsed = parse(declaration())

    assert parsed.owner == "example"
    assert parsed.packages == (
        Package(PackageType.NUGET, "Example.Kernel", {"platform": Role.WRITE}),
    )


def test_changes_cover_only_declared_drift() -> None:
    package = Package(
        PackageType.NUGET, "Example.Kernel", {"platform": Role.WRITE, "api": Role.READ}
    )

    pending = changes(package, {"api": Role.READ, "legacy": Role.ADMIN})

    assert pending == [Change(package, "platform", None, Role.WRITE)]
    assert pending[0].describe() == "nuget/Example.Kernel: platform add write"
    assert changes(package, {"platform": Role.READ, "api": Role.READ})[
        0
    ].describe() == ("nuget/Example.Kernel: platform read -> write")


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (declaration(schema_version=2), "schema_version must be 1"),
        (declaration(owner=""), "owner must be a nonempty string"),
        (declaration(repositories={}), "unknown fields: repositories"),
        (declaration(packages={"pypi": {}}), "packages.pypi must be one of"),
        (
            declaration(
                packages={"nuget": {"A": {"actions_access": {"r": "maintain"}}}}
            ),
            "packages.nuget.A.actions_access.r must be one of: read, write, admin",
        ),
        (
            declaration(
                packages={"nuget": {"A": {"actions_access": {"other/r": "read"}}}}
            ),
            "must be a repository name in the owner",
        ),
        (
            declaration(packages={"nuget": {"A": {"visibility": "public"}}}),
            "packages.nuget.A has unknown fields: visibility",
        ),
    ],
)
def test_rejects_invalid_declarations(document: dict[str, Any], message: str) -> None:
    with pytest.raises(DeclarationError, match=message):
        parse(document)
