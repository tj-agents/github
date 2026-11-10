import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import configuration_browser
import pytest
from configuration_adopt import AdoptError, native
from configuration_declaration import (
    DeclarationError,
    Package,
    PackageType,
    Role,
    parse,
)
from configuration_packages import Change, changes


@pytest.mark.skipif(sys.platform != "win32", reason="Uses Windows native argv parsing")
@pytest.mark.parametrize(
    ("command", "matches"),
    [
        ("chrome --user-data-dir=C:/Example/Profile", True),
        ('chrome --user-data-dir="C:\\Example\\Profile"', True),
        ('chrome "--user-data-dir=C:/EXAMPLE/Profile/" --headless', True),
        ('chrome --user-data-dir "C:/Example/Profile"', True),
        ("chrome --user-data-dir=C:/Example/Profile-other", False),
        ("chrome --user-data-dir=C:/Example/Profile/child", False),
        ("chrome --other=C:/Example/Profile", False),
        ("chrome --other-user-data-dir=C:/Example/Profile", False),
        ('chrome --other="a --user-data-dir=C:/Example/Profile other"', False),
        ('chrome "--other a --user-data-dir=C:/Example/Profile other"', False),
        ("chrome --user-data-dir=C:/Other --other=C:/Example/Profile", False),
    ],
)
def test_profile_processes_match_only_the_owned_argument(
    monkeypatch: pytest.MonkeyPatch, command: str, matches: bool
) -> None:
    profile = Path("C:/Example/Profile")
    monkeypatch.setattr(configuration_browser, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        configuration_browser, "_command_lines", lambda: [(123, command)]
    )

    assert configuration_browser._profile_processes(profile) == (
        [123] if matches else []
    )


@pytest.mark.parametrize(
    ("arguments", "matches"),
    [
        (["--user-data-dir=/home/user/agent state/browser-profile"], True),
        (["--user-data-dir", "/home/user/agent state/browser-profile"], True),
        (["--user-data-dir=/home/user/agent state/browser-profile-other"], False),
        (["--user-data-dir=/home/user/Agent State/browser-profile"], False),
        (["--other=/home/user/agent state/browser-profile"], False),
    ],
)
def test_profile_processes_preserve_posix_argument_boundaries(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str], matches: bool
) -> None:
    profile = Path("/home/user/agent state/browser-profile")
    command = "\0".join(["/opt/google/chrome", *arguments, "--headless", ""])
    monkeypatch.setattr(configuration_browser, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(
        configuration_browser, "_command_lines", lambda: [(123, command)]
    )

    assert configuration_browser._profile_processes(profile) == (
        [123] if matches else []
    )


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


def test_null_access_removes_only_the_named_repository() -> None:
    parsed = parse(
        declaration(
            packages={"nuget": {"Example.Kernel": {"actions_access": {"legacy": None}}}}
        )
    )
    package = parsed.packages[0]

    pending = changes(package, {"legacy": Role.ADMIN, "consumer": Role.READ})

    assert pending == [Change(package, "legacy", Role.ADMIN, None)]
    assert pending[0].describe() == "nuget/Example.Kernel: legacy remove admin"
    assert changes(package, {"consumer": Role.READ}) == []


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (declaration(schema_version=2), "schema_version must be 1"),
        (declaration(owner=""), "owner must be a nonempty string"),
        (declaration(teams={}), "unknown fields: teams"),
        (
            declaration(repositories={"r": {"rulesets": {"m": {"rules": [{}]}}}}),
            "repositories.r.rulesets.m.rules must be a list of rules with a type",
        ),
        (
            declaration(repositories={"r": {"webhooks": {}}}),
            "repositories.r has unknown fields: webhooks",
        ),
        (
            declaration(
                repositories={"r": {"environments": {"e": {"secrets": {"T": ""}}}}}
            ),
            "repositories.r.environments.e.secrets.T must name a workspace secret",
        ),
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


def ruleset_state(**rules: Any) -> dict[str, Any]:
    return {
        "target": "branch",
        "enforcement": "active",
        "conditions": [{"ref_name": [{"include": ["~DEFAULT_BRANCH"], "exclude": []}]}],
        "bypass_actors": [
            {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
        ],
        "rules": [rules],
    }


def test_adopt_carries_merge_queue_and_flags() -> None:
    queue = {
        "check_response_timeout_minutes": 60,
        "grouping_strategy": "ALLGREEN",
        "max_entries_to_build": 5,
        "max_entries_to_merge": 5,
        "merge_method": "MERGE",
        "min_entries_to_merge": 1,
        "min_entries_to_merge_wait_minutes": 5,
    }

    adopted = native(ruleset_state(deletion=True, merge_queue=[queue]))

    assert adopted["rules"] == [
        {"type": "deletion"},
        {"type": "merge_queue", "parameters": queue},
    ]
    assert adopted["bypass_actors"] == [
        {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
    ]


def test_adopt_refuses_rules_it_cannot_carry() -> None:
    with pytest.raises(AdoptError, match="tag_name_pattern"):
        native(ruleset_state(tag_name_pattern=[{"pattern": "v*"}]))


def test_secret_syncs_need_a_known_scope() -> None:
    secrets = {"project": "p", "connection": "c", "syncs": {"s": {"scope": "team"}}}

    with pytest.raises(DeclarationError, match=r"secrets.syncs.s.scope must be one of"):
        parse(declaration(secrets=secrets))
