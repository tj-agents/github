"""The `gh` shim: humans and agents without App access reach the real gh untouched."""

import os
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import assert_never

from gate_access import Declarations
from gate_agent import agent_host
from gate_app import GitHubApp
from gate_declaration import AppAccess, HumanAccess
from gate_error import GateError
from gate_owner import gh_target, origin_owner, owners_in
from gate_platform import MARKER, Platform

PROBE_BYTES = 4096
NON_API = frozenset({"--version", "version", "help", "--help", "-h"})
SHOW_TOKEN = ("-t", "--show-token")


class ShimError(GateError):
    pass


def is_gate_launcher(path: Path) -> bool:
    """Whether `path` is a launcher this gate installed, judged by its marker."""
    try:
        with path.open("rb") as stream:
            head = stream.read(PROBE_BYTES)
            size = stream.seek(0, os.SEEK_END)
            stream.seek(max(0, size - PROBE_BYTES))
            tail = stream.read()
    except OSError:
        return False
    marker = MARKER.encode()
    return marker in head or marker in tail


def executable_names(environment: Mapping[str, str]) -> list[str]:
    if os.name != "nt":
        return ["gh"]
    extensions = environment.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD"
    return [
        f"gh{extension.lower()}" for extension in extensions.split(";") if extension
    ]


def gh_on_path(environment: Mapping[str, str]) -> Iterator[Path]:
    """Every gh on PATH, in the order a shell would try them."""
    names = executable_names(environment)
    for entry in environment.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        for name in names:
            candidate = Path(entry) / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                yield candidate


def real_gh(environment: Mapping[str, str]) -> Path:
    """The first gh on PATH that is not one of this gate's launchers."""
    for candidate in gh_on_path(environment):
        if not is_gate_launcher(candidate):
            return candidate
    raise ShimError("the real gh is not on PATH after the gate's own gh")


def app_environment(
    arguments: Sequence[str],
    environment: Mapping[str, str],
    cwd: Path,
    app: GitHubApp,
    now: float,
) -> dict[str, str]:
    """The environment for an agent's gh command acting as `app`, or refuse it."""
    command = list(arguments)
    if command[:1] == ["auth"] and (
        command[1:2] != ["status"]
        or any(argument.partition("=")[0] in SHOW_TOKEN for argument in command)
    ):
        raise ShimError(
            f"agents here act as GitHub App {app.slug}; of gh auth they may run only "
            "`gh auth status`"
        )
    target = gh_target(command)
    hostname = target.hostname or environment.get("GH_HOST") or "github.com"
    if hostname.lower() != "github.com":
        raise ShimError(
            f"GitHub App {app.slug} acts only on github.com, not {hostname}"
        )
    owners = list(target.owners)
    if not owners and environment.get("GH_REPO"):
        owners = owners_in(environment["GH_REPO"])
    if not owners:
        origin = origin_owner(cwd, environment)
        owners = [origin] if origin else []
    if not owners:
        raise ShimError(
            "cannot tell which GitHub account this command targets; pass --repo "
            "OWNER/REPO"
        )
    if len({owner.lower() for owner in owners}) > 1:
        raise ShimError("one gh command must target a single GitHub account")
    return {**environment, "GH_TOKEN": app.installation_token(owners[0], now)}


def run(
    arguments: Sequence[str],
    environment: Mapping[str, str],
    cwd: Path,
    declarations: Declarations,
    platform: Platform,
    now: float,
) -> int:
    real = real_gh(environment)
    if agent_host(environment) is None or not arguments or arguments[0] in NON_API:
        return platform.execute(real, arguments, None)
    access = declarations.resolve(cwd, environment, now)
    match access:
        case HumanAccess():
            return platform.execute(real, arguments, None)
        case AppAccess(slug=slug):
            app = declarations.apps(slug)
            scoped = app_environment(arguments, environment, cwd, app, now)
            return platform.execute(real, arguments, scoped)
        case _:
            assert_never(access)
