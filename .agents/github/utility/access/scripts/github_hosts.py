from __future__ import annotations

import copy
import json
import os
import stat
import sys
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

PUBLIC_ENVIRONMENT_KEYS = frozenset(
    {
        "PATH",
        "GITHUB_ACCESS_BINDING",
        "AGENT_STATE_DIRECTORY",
        "GCM_CREDENTIAL_STORE",
    }
)
MCP_ENV_VARS = (
    "DBUS_SESSION_BUS_ADDRESS",
    "GNUPGHOME",
    "GPG_TTY",
    "PASSWORD_STORE_DIR",
    "XDG_RUNTIME_DIR",
)


class SettingsError(ValueError):
    pass


class NativeHost(StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"


class BindingProtocol(Protocol):
    @property
    def path(self) -> Path: ...

    @property
    def root(self) -> Path: ...

    @property
    def runtime(self) -> Path: ...

    @property
    def launchers(self) -> Path: ...

    def activation_settings(self, base: Mapping[str, str]) -> dict[str, str]: ...


@dataclass(frozen=True, slots=True)
class MCPCommand:
    command: str
    args: tuple[str, ...]
    cwd: str
    environment: Mapping[str, str]
    env_vars: tuple[str, ...] = MCP_ENV_VARS


@dataclass(frozen=True, slots=True)
class SettingsChange:
    path: Path
    original: bytes | None
    updated: bytes


@dataclass(frozen=True, slots=True)
class _Statement:
    text: str
    table: tuple[str, ...] | None
    document: dict[str, object] | None


def _normalized_path(value: str) -> str:
    return os.path.normcase(os.path.normpath(value))


def public_environment(
    binding: BindingProtocol, base: Mapping[str, str]
) -> dict[str, str]:
    environment = binding.activation_settings(base)
    if not isinstance(environment, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in environment.items()
    ):
        raise SettingsError("Binding activation settings must be string values")
    path = environment.get("PATH")
    if path is None:
        raise SettingsError("Binding activation settings must include PATH")
    launcher = str(binding.launchers)
    launcher_key = _normalized_path(launcher)
    entries = [
        entry
        for entry in path.split(os.pathsep)
        if entry and _normalized_path(entry) != launcher_key
    ]
    environment["PATH"] = os.pathsep.join((launcher, *entries))
    return environment


def mcp_command(binding: BindingProtocol, base: Mapping[str, str]) -> MCPCommand:
    executable = Path(sys.executable).resolve(strict=True)
    return MCPCommand(
        command=str(executable),
        args=(
            "-B",
            str(binding.runtime / "github_access.py"),
            "mcp",
            "--binding",
            str(binding.path),
        ),
        cwd=str(binding.root),
        environment=public_environment(binding, base),
    )


def _require_object(value: object, description: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SettingsError(f"{description} must be an object")
    return value


def _require_string_map(value: object, description: str) -> dict[str, str]:
    mapping = _require_object(value, description)
    if any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in mapping.items()
    ):
        raise SettingsError(f"{description} must contain string values")
    return {key: item for key, item in mapping.items() if isinstance(item, str)}


def _public_values(command: MCPCommand) -> dict[str, str]:
    environment = _require_string_map(command.environment, "MCP environment")
    unknown = set(environment) - PUBLIC_ENVIRONMENT_KEYS
    if unknown:
        raise SettingsError("MCP environment contains non-public variables")
    required = PUBLIC_ENVIRONMENT_KEYS - {"GCM_CREDENTIAL_STORE"}
    if required - set(environment):
        raise SettingsError("MCP environment omits required public variables")
    return environment


def render_claude(text: str, command: MCPCommand) -> str:
    try:
        before = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as error:
        raise SettingsError("Claude settings must be valid JSON") from error
    settings = _require_object(before, "Claude settings")
    expected = copy.deepcopy(settings)
    existing = expected.get("env", {})
    environment = _require_string_map(existing, "Claude env")
    for key in PUBLIC_ENVIRONMENT_KEYS:
        environment.pop(key, None)
    environment.update(_public_values(command))
    expected["env"] = environment
    if settings == expected:
        return text
    return json.dumps(expected, indent=2, ensure_ascii=False) + "\n"


def _header_path(text: str) -> tuple[str, ...]:
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise SettingsError("Cannot parse a Codex TOML table header") from error
    path: list[str] = []
    current: object = document
    while True:
        if isinstance(current, dict) and len(current) == 1:
            key, current = next(iter(current.items()))
            path.append(key)
        elif isinstance(current, list) and len(current) == 1:
            current = current[0]
        else:
            break
    if not path or current != {}:
        raise SettingsError("Cannot identify a Codex TOML table header")
    return tuple(path)


def _statements(text: str) -> list[_Statement]:
    result: list[_Statement] = []
    table: tuple[str, ...] | None = None
    header = ""
    pending = ""
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if not pending and (not stripped.strip() or stripped.startswith("#")):
            result.append(_Statement(line, None, None))
            continue
        if not pending and stripped.startswith("["):
            table = _header_path(line)
            header = line
            result.append(_Statement(line, table, None))
            continue
        pending += line
        try:
            document = tomllib.loads(header + pending)
        except tomllib.TOMLDecodeError:
            continue
        result.append(_Statement(pending, table, document))
        pending = ""
    if pending:
        raise SettingsError("Cannot identify complete Codex settings statements")
    return result


def _table_value(
    document: dict[str, object], table: tuple[str, ...]
) -> dict[str, object]:
    current: object = document
    for key in table:
        if not isinstance(current, dict) or key not in current:
            return {}
        current = current[key]
    return current if isinstance(current, dict) else {}


def _validate_policy(
    expected: dict[str, object], environment: Mapping[str, str]
) -> None:
    policy_value = expected.get("shell_environment_policy")
    if policy_value is None:
        policy: dict[str, object] = {}
        expected["shell_environment_policy"] = policy
    else:
        policy = _require_object(policy_value, "Codex shell_environment_policy")
    set_value = policy.get("set")
    if set_value is None:
        set_values: dict[str, object] = {}
        policy["set"] = set_values
    else:
        set_values = _require_object(set_value, "Codex shell_environment_policy.set")
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in set_values.items()
        ):
            raise SettingsError(
                "Codex shell_environment_policy.set must contain string values"
            )
    include_only = policy.get("include_only")
    filters = policy.get("filters")
    if include_only is not None and filters is not None:
        raise SettingsError("Codex shell environment has conflicting include filters")
    patterns: list[str] | None = None
    if include_only is not None:
        if not isinstance(include_only, list) or any(
            not isinstance(value, str) for value in include_only
        ):
            raise SettingsError(
                "Codex shell_environment_policy.include_only must be a string list"
            )
        patterns = include_only
    if filters is not None:
        filters_map = _require_object(filters, "Codex shell_environment_policy.filters")
        if any(
            not isinstance(pattern, str) or action not in {"include", "exclude"}
            for pattern, action in filters_map.items()
        ):
            raise SettingsError(
                "Codex shell_environment_policy.filters has an unknown shape"
            )
        patterns = [
            pattern for pattern, action in filters_map.items() if action == "include"
        ]
    if patterns is not None:
        import fnmatch

        missing = [
            key
            for key in environment
            if not any(
                fnmatch.fnmatchcase(key.casefold(), pattern.casefold())
                for pattern in patterns
            )
        ]
        if missing:
            raise SettingsError(
                "Codex shell environment filters required public variables"
            )
    for key in PUBLIC_ENVIRONMENT_KEYS:
        set_values.pop(key, None)
    set_values.update(environment)


def _desired_mcp(command: MCPCommand) -> dict[str, object]:
    return {
        "command": command.command,
        "args": list(command.args),
        "cwd": command.cwd,
        "required": True,
        "env_vars": list(command.env_vars),
        "env": _public_values(command),
    }


def _contains_path(document: dict[str, object], path: tuple[str, ...]) -> bool:
    current: object = document
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return False
        current = current[key]
    return True


def _inline_ancestor_conflict(
    statement: _Statement, environment: Mapping[str, str]
) -> bool:
    if statement.document is None:
        return False
    document = statement.document
    mcp = document.get("mcp_servers")
    if isinstance(mcp, dict) and "employer_github" in mcp:
        if set(mcp) != {"employer_github"}:
            raise SettingsError("Cannot preserve mixed inline mcp_servers settings")
        return True
    policy = document.get("shell_environment_policy")
    if isinstance(policy, dict) and "set" in policy:
        values = policy["set"]
        if isinstance(values, dict) and set(values) & set(environment):
            if set(policy) != {"set"} or set(values) - PUBLIC_ENVIRONMENT_KEYS:
                raise SettingsError(
                    "Cannot preserve mixed inline shell environment settings"
                )
            return True
    return False


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_list(values: Sequence[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _render_shell_values(environment: Mapping[str, str], newline: str) -> str:
    return "".join(
        f"{key} = {_toml_string(value)}{newline}" for key, value in environment.items()
    )


def _render_mcp_block(
    command: MCPCommand, environment: Mapping[str, str], newline: str
) -> str:
    mcp_lines = ["[mcp_servers.employer_github]"]
    mcp_lines.extend(
        (
            f"command = {_toml_string(command.command)}",
            f"args = {_toml_list(command.args)}",
            f"cwd = {_toml_string(command.cwd)}",
            "required = true",
            f"env_vars = {_toml_list(command.env_vars)}",
            "",
            "[mcp_servers.employer_github.env]",
        )
    )
    mcp_lines.extend(
        f"{key} = {_toml_string(value)}" for key, value in environment.items()
    )
    return newline.join(mcp_lines) + newline


def render_codex(text: str, command: MCPCommand) -> str:
    try:
        before = tomllib.loads(text) if text.strip() else {}
    except tomllib.TOMLDecodeError as error:
        raise SettingsError("Codex settings must be valid TOML") from error
    expected = copy.deepcopy(_require_object(before, "Codex settings"))
    environment = _public_values(command)
    _validate_policy(expected, environment)
    servers_value = expected.get("mcp_servers")
    if servers_value is None:
        servers: dict[str, object] = {}
        expected["mcp_servers"] = servers
    else:
        servers = _require_object(servers_value, "Codex mcp_servers")
    servers["employer_github"] = _desired_mcp(command)
    if before == expected:
        return text
    kept: list[str] = []
    shell_insert: int | None = None
    for statement in _statements(text):
        if _inline_ancestor_conflict(statement, environment):
            continue
        if statement.table is not None:
            if statement.table[:2] == ("mcp_servers", "employer_github"):
                continue
            if statement.table == ("shell_environment_policy", "set"):
                if statement.document is None:
                    shell_insert = len(kept) + 1
                    kept.append(statement.text)
                elif not set(_table_value(statement.document, statement.table)) & set(
                    environment
                ):
                    kept.append(statement.text)
                continue
            kept.append(statement.text)
            continue
        if statement.document is None:
            kept.append(statement.text)
            continue
        if _contains_path(statement.document, ("mcp_servers", "employer_github")):
            continue
        kept.append(statement.text)
    newline = "\r\n" if "\r\n" in text else "\n"
    if shell_insert is None:
        if kept and not kept[-1].endswith(("\r", "\n")):
            kept[-1] += newline
        kept.append("[shell_environment_policy.set]" + newline)
        shell_insert = len(kept)
    elif shell_insert and not kept[shell_insert - 1].endswith(("\r", "\n")):
        kept[shell_insert - 1] += newline
    kept.insert(shell_insert, _render_shell_values(environment, newline))
    prefix = "".join(kept).rstrip("\r\n")
    result = (prefix + newline + newline if prefix else "") + _render_mcp_block(
        command, environment, newline
    )
    try:
        actual = tomllib.loads(result)
    except tomllib.TOMLDecodeError as error:
        raise SettingsError(
            "Cannot update Codex settings without changing unrelated configuration"
        ) from error
    if actual != expected:
        raise SettingsError(
            "Cannot update Codex settings without changing unrelated configuration"
        )
    return result


def _check_path(path: Path) -> None:
    absolute = path.absolute()
    for ancestor in (absolute, *absolute.parents):
        if ancestor.is_symlink():
            raise SettingsError(f"Settings path contains a symlink: {path}")
    if path.exists():
        try:
            mode = path.stat().st_mode
        except OSError as error:
            raise SettingsError(f"Cannot inspect settings path: {path}") from error
        if not stat.S_ISREG(mode):
            raise SettingsError(f"Settings path is not a regular file: {path}")


def _read(path: Path) -> bytes | None:
    _check_path(path)
    return path.read_bytes() if path.exists() else None


def _decode(value: bytes | None) -> tuple[str, bool]:
    if value is None:
        return "", False
    bom = value.startswith(b"\xef\xbb\xbf")
    try:
        return value.decode("utf-8-sig"), bom
    except UnicodeDecodeError as error:
        raise SettingsError("Settings file must use UTF-8") from error


def stage_setting(
    host: NativeHost, path: Path, command: MCPCommand
) -> SettingsChange | None:
    original = _read(path)
    text, bom = _decode(original)
    updated_text = (
        render_claude(text, command)
        if host is NativeHost.CLAUDE
        else render_codex(text, command)
    )
    updated = (b"\xef\xbb\xbf" if bom else b"") + updated_text.encode("utf-8")
    if updated == original:
        return None
    return SettingsChange(path, original, updated)


def _write_atomic(path: Path, value: bytes) -> None:
    _check_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _check_path(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def apply_settings(changes: Sequence[SettingsChange]) -> None:
    if len({change.path.absolute() for change in changes}) != len(changes):
        raise SettingsError("Settings changes contain duplicate paths")
    for change in changes:
        if _read(change.path) != change.original:
            raise SettingsError("Settings changed while staging")
    applied: list[SettingsChange] = []
    try:
        for change in changes:
            _write_atomic(change.path, change.updated)
            applied.append(change)
    except (OSError, SettingsError) as error:
        rollback_failed = False
        for change in reversed(applied):
            try:
                if _read(change.path) != change.updated:
                    rollback_failed = True
                    continue
                if change.original is None:
                    change.path.unlink()
                else:
                    _write_atomic(change.path, change.original)
            except (OSError, SettingsError):
                rollback_failed = True
        if rollback_failed:
            raise SettingsError("Settings update and rollback failed") from error
        raise SettingsError("Settings update failed") from error
