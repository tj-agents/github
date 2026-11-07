import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


class DeclarationError(ValueError):
    pass


class PackageType(StrEnum):
    CONTAINER = "container"
    MAVEN = "maven"
    NPM = "npm"
    NUGET = "nuget"
    RUBYGEMS = "rubygems"


class Role(StrEnum):
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


@dataclass(frozen=True)
class Package:
    type: PackageType
    name: str
    actions_access: dict[str, Role]


@dataclass(frozen=True)
class Declaration:
    owner: str
    packages: tuple[Package, ...]


def _object(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DeclarationError(f"{where} must be an object")
    return value


def _keys(value: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise DeclarationError(f"{where} has unknown fields: {', '.join(unknown)}")


def _enum[E: StrEnum](kind: type[E], value: Any, where: str) -> E:
    try:
        return kind(value)
    except ValueError:
        choices = ", ".join(member.value for member in kind)
        raise DeclarationError(f"{where} must be one of: {choices}") from None


def _package(package_type: PackageType, name: str, value: Any) -> Package:
    where = f"packages.{package_type}.{name}"
    body = _object(value, where)
    _keys(body, {"actions_access"}, where)
    access = _object(body.get("actions_access", {}), f"{where}.actions_access")
    roles = {
        repository: _enum(Role, role, f"{where}.actions_access.{repository}")
        for repository, role in access.items()
    }
    for repository in roles:
        if not repository or "/" in repository:
            raise DeclarationError(
                f"{where}.actions_access key {repository!r} must be a repository "
                "name in the owner"
            )
    return Package(package_type, name, roles)


def _ruleset(where: str, value: Any) -> None:
    body = _object(value, where)
    _keys(
        body, {"target", "enforcement", "conditions", "bypass_actors", "rules"}, where
    )
    rules = body.get("rules")
    if not isinstance(rules, list) or not all(
        isinstance(rule, dict) and isinstance(rule.get("type"), str) for rule in rules
    ):
        raise DeclarationError(f"{where}.rules must be a list of rules with a type")


def _environment(where: str, value: Any) -> None:
    body = _object(value, where)
    _keys(body, {"deployment_branches", "secrets"}, where)
    branches = body.get("deployment_branches", [])
    if not isinstance(branches, list) or not all(
        isinstance(branch, str) and branch for branch in branches
    ):
        raise DeclarationError(f"{where}.deployment_branches must list branch patterns")
    for name, source in _object(body.get("secrets", {}), f"{where}.secrets").items():
        if not isinstance(source, str) or not source:
            raise DeclarationError(
                f"{where}.secrets.{name} must name a workspace secret source, "
                "never a value"
            )


def _repositories(value: Any) -> None:
    for repository, settings in _object(value, "repositories").items():
        where = f"repositories.{repository}"
        body = _object(settings, where)
        _keys(body, {"rulesets", "environments"}, where)
        for name, environment in _object(
            body.get("environments", {}), f"{where}.environments"
        ).items():
            _environment(f"{where}.environments.{name}", environment)
        for name, ruleset in _object(
            body.get("rulesets", {}), f"{where}.rulesets"
        ).items():
            _ruleset(f"{where}.rulesets.{name}", ruleset)


def parse(document: Any) -> Declaration:
    root = _object(document, "declaration")
    _keys(root, {"schema_version", "owner", "packages", "repositories"}, "declaration")
    _repositories(root.get("repositories", {}))
    if root.get("schema_version") != SCHEMA_VERSION:
        raise DeclarationError(f"declaration.schema_version must be {SCHEMA_VERSION}")
    owner = root.get("owner")
    if not isinstance(owner, str) or not owner:
        raise DeclarationError("declaration.owner must be a nonempty string")
    by_type = {
        _enum(PackageType, package_type, f"packages.{package_type}"): _object(
            named, f"packages.{package_type}"
        )
        for package_type, named in _object(root.get("packages", {}), "packages").items()
    }
    packages = [
        _package(package_type, name, body)
        for package_type, named in by_type.items()
        for name, body in named.items()
    ]
    return Declaration(owner, tuple(packages))


def load(path: Path) -> Declaration:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise DeclarationError(f"{path}: {error}") from None
    return parse(document)
