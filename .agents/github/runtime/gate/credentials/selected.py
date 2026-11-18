"""Credentials that the plugins a repository selects locally ship when installed.

An employer repository cannot commit TJ Agents configuration, so its uncommitted
`.agents/plugins.json` selection names the employer plugin, and that plugin's installed
payload ships the credential profile as `credentials.json`, in the same format as a
committed `.agents/repository.json` with only a `credentials` section.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from gate_git import git, machine_environment

from credentials.model import Credential, CredentialError, Declaration, declared

SELECTION = ".agents/plugins.json"
PROFILE = "credentials.json"
MARKETPLACE = "tj-agents"
MAX_BYTES = 128 * 1024


def _checkouts(cwd: Path, environment: Mapping[str, str]) -> tuple[Path, Path] | None:
    """This checkout's root and its repository's main checkout root."""
    result = git(
        ["rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir"],
        cwd,
        machine_environment(environment),
    )
    lines = result.stdout.decode("utf-8", "replace").splitlines()
    if result.returncode or len(lines) != 2:
        return None
    return Path(lines[0]), Path(lines[1]).parent


def _json(path: Path) -> object:
    try:
        with path.open("rb") as stream:
            return json.loads(stream.read(MAX_BYTES + 1)[:MAX_BYTES])
    except (OSError, ValueError):
        return None


def _selected(checkouts: tuple[Path, Path]) -> tuple[str, ...]:
    """The tj-agents plugins this checkout, else its main checkout, selects."""
    for root in dict.fromkeys(checkouts):
        if (root / SELECTION).is_file():
            document = _json(root / SELECTION)
            plugins = document.get("plugins") if isinstance(document, dict) else None
            if not isinstance(plugins, list):
                raise CredentialError(f"credentials-invalid: check {root / SELECTION}")
            return tuple(
                plugin
                for plugin in plugins
                if isinstance(plugin, str) and plugin.endswith("@" + MARKETPLACE)
            )
    return ()


def _same(left: str, right: Path) -> bool:
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(
        os.path.normpath(str(right))
    )


def _payload(entries: object, main: Path) -> Path | None:
    """The installed copy Claude loads for the main checkout, else the user's."""
    if not isinstance(entries, list):
        return None
    candidates = [entry for entry in entries if isinstance(entry, dict)]
    for scope in ("local", "project", "user"):
        for entry in candidates:
            path, project = entry.get("installPath"), entry.get("projectPath")
            if entry.get("scope") != scope or not isinstance(path, str):
                continue
            if scope == "user" or (isinstance(project, str) and _same(project, main)):
                return Path(path)
    return None


def selected(cwd: Path, environment: Mapping[str, str]) -> Declaration | None:
    """The credentials the plugins this repository selects ship; None without any."""
    checkouts = _checkouts(cwd, environment)
    if checkouts is None:
        return None
    plugins = _selected(checkouts)
    if not plugins:
        return None
    configured = environment.get("CLAUDE_CONFIG_DIR")
    home = environment.get("USERPROFILE") or environment.get("HOME") or str(Path.home())
    root = Path(configured) if configured else Path(home) / ".claude"
    registry = _json(root / "plugins" / "installed_plugins.json")
    installed = registry.get("plugins") if isinstance(registry, dict) else None
    if not isinstance(installed, dict):
        return None
    credentials: list[Credential] = []
    for plugin in plugins:
        payload = _payload(installed.get(plugin), checkouts[1])
        if payload is None or not (payload / PROFILE).is_file():
            continue
        try:
            text = (payload / PROFILE).read_bytes()
        except OSError as error:
            raise CredentialError(f"credentials-invalid: {plugin} {PROFILE}") from error
        if len(text) > MAX_BYTES:
            raise CredentialError(f"credentials-invalid: {plugin} {PROFILE} is large")
        declaration = declared(text)
        if declaration is not None:
            credentials.extend(declaration.credentials)
    return Declaration(tuple(credentials)) if credentials else None
