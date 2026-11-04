from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

from access_core import AccessError, State, private_environment
from github_binding import Binding
from github_claude_registration import RegistrationState, registration_state
from github_hosts import (
    MCPCommand,
    NativeHost,
    SettingsError,
    apply_settings,
    mcp_command,
    stage_setting,
)
from github_platform import PlatformProtocol


def _registration(command: MCPCommand) -> dict[str, object]:
    return {
        "type": "stdio",
        "command": command.command,
        "args": list(command.args),
        "env": dict(command.environment),
    }


def _public_registration(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"type", "command", "args", "env"}:
        raise AccessError("host-receipt-invalid: inspect public activation state")
    if (
        value["type"] != "stdio"
        or not isinstance(value["command"], str)
        or not isinstance(value["args"], list)
        or any(not isinstance(argument, str) for argument in value["args"])
        or not isinstance(value["env"], dict)
        or set(value["env"])
        - {
            "PATH",
            "GITHUB_ACCESS_BINDING",
            "AGENT_STATE_DIRECTORY",
            "GCM_CREDENTIAL_STORE",
        }
        or any(not isinstance(item, str) for item in value["env"].values())
    ):
        raise AccessError("host-receipt-invalid: inspect public activation state")
    return value


def _receipt_path(binding: Binding) -> Path:
    key = hashlib.sha256(str(binding.path).encode()).hexdigest()
    return binding.state_root / "github-access" / "activation" / key / "claude.json"


def _previous(binding: Binding) -> list[dict[str, object]]:
    path = _receipt_path(binding)
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 262144:
        raise AccessError("host-receipt-invalid: inspect public activation state")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as error:
        raise AccessError(
            "host-receipt-invalid: inspect public activation state"
        ) from error
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "root", "binding", "desired", "previous"}
        or value["schema_version"] != 1
        or value["root"] != str(binding.root)
        or value["binding"] != str(binding.path)
    ):
        raise AccessError("host-receipt-invalid: inspect public activation state")
    entries = [_public_registration(value["desired"])]
    if value["previous"] is not None:
        entries.append(_public_registration(value["previous"]))
    return entries


def _save_receipt(
    binding: Binding,
    desired: Mapping[str, object],
    previous: Mapping[str, object] | None,
) -> None:
    path = _receipt_path(binding)
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise AccessError("host-receipt-invalid: activation state cannot be linked")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="activation-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(
                {
                    "schema_version": 1,
                    "root": str(binding.root),
                    "binding": str(binding.path),
                    "desired": desired,
                    "previous": previous,
                },
                stream,
                indent=2,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _claude(
    executable: Path | None, root: Path, arguments: list[str], base: Mapping[str, str]
) -> None:
    if executable is None or not executable.is_absolute():
        raise AccessError(
            "host-executable-required: select the native Claude executable"
        )
    try:
        native = executable.resolve(strict=True)
        if not native.is_file():
            raise OSError
        result = subprocess.run(
            [str(native), "mcp", *arguments],
            cwd=root,
            env=private_environment(base),
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AccessError(
            "host-registration-failed: native Claude command failed"
        ) from error
    if result.returncode:
        raise AccessError("host-registration-failed: native Claude command failed")


def _claude_state(
    binding: Binding, profile: Path, desired: Mapping[str, object]
) -> tuple[RegistrationState, dict[str, object] | None]:
    state = registration_state(profile, binding.root, desired)
    if state != RegistrationState.CONFLICT:
        return state, None
    for previous in _previous(binding):
        state = registration_state(profile, binding.root, desired, previous)
        if state == RegistrationState.PREVIOUS_OWNED:
            return state, previous
        if state != RegistrationState.CONFLICT:
            return state, None
    return RegistrationState.CONFLICT, None


def _register_claude(
    binding: Binding,
    command: MCPCommand,
    profile: Path,
    executable: Path | None,
    base: Mapping[str, str],
) -> None:
    desired = _registration(command)
    state, previous = _claude_state(binding, profile, desired)
    if state == RegistrationState.CONFLICT:
        raise AccessError(
            "host-registration-conflict: employer_github is already owned"
        )
    _save_receipt(binding, desired, previous)
    if registration_state(profile, binding.root, desired, previous) != state:
        raise AccessError(
            "host-registration-changed: inspect local Claude registration"
        )
    if state == RegistrationState.PREVIOUS_OWNED:
        _claude(
            executable,
            binding.root,
            ["remove", "--scope", "local", "employer_github"],
            base,
        )
        if (
            registration_state(profile, binding.root, desired)
            != RegistrationState.ABSENT
        ):
            raise AccessError(
                "host-registration-changed: inspect local Claude registration"
            )
    if state != RegistrationState.CURRENT:
        arguments = ["add"]
        for key, value in command.environment.items():
            arguments.extend(("--env", f"{key}={value}"))
        arguments.extend(
            (
                "--transport",
                "stdio",
                "--scope",
                "local",
                "employer_github",
                "--",
                command.command,
                *command.args,
            )
        )
        _claude(executable, binding.root, arguments, base)
    if registration_state(profile, binding.root, desired) != RegistrationState.CURRENT:
        raise AccessError(
            "host-registration-failed: native Claude registration differs"
        )


def activate(
    binding: Binding,
    platform: PlatformProtocol,
    host: NativeHost,
    base: Mapping[str, str],
    *,
    executable: Path | None = None,
    check: bool = False,
) -> dict[str, object]:
    binding.validate(Path.cwd(), platform)
    command = mcp_command(binding, base)
    settings = binding.root / (
        ".claude/settings.local.json"
        if host == NativeHost.CLAUDE
        else ".codex/config.toml"
    )
    if host == NativeHost.CLAUDE and base.get("CLAUDE_CONFIG_DIR"):
        raise AccessError(
            "host-profile-unsupported: use the native default Claude profile"
        )
    profile = Path.home() / ".claude.json"
    try:
        with State(binding.state_root, binding.profile).lock():
            change = stage_setting(host, settings, command)
            if check:
                if change is not None:
                    raise AccessError(
                        "host-settings-stale: run activation for this host"
                    )
                if (
                    host == NativeHost.CLAUDE
                    and registration_state(
                        profile, binding.root, _registration(command)
                    )
                    != RegistrationState.CURRENT
                ):
                    raise AccessError(
                        "host-registration-stale: run activation for this host"
                    )
            else:
                if host == NativeHost.CLAUDE:
                    _register_claude(binding, command, profile, executable, base)
                binding.validate(Path.cwd(), platform)
                apply_settings([change] if change is not None else [])
                if host == NativeHost.CLAUDE:
                    _save_receipt(binding, _registration(command), None)
    except SettingsError as error:
        raise AccessError(
            "host-settings-invalid: inspect selected project settings"
        ) from error
    return {
        "status": "native-host-configured",
        "host": host.value,
        "binding": str(binding.path),
        "settings": str(settings),
        "required_host_arguments": ["--settings", str(settings)]
        if host == NativeHost.CLAUDE
        else [],
    }
