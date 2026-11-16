"""GitHub access gate: one gh shim and Git credential helper for agents and humans.

github_gate.py setup [--check] [--onboard]
github_gate.py onboard app --app SLUG --key PRIVATE-KEY.pem
github_gate.py converge
github_gate.py gh ARGUMENTS...            (run by the installed gh shim)
github_gate.py credential OPERATION      (run by Git)
"""

import argparse
import os
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

import gate_posix
import gate_windows
from gate_agent import agent_host
from gate_app import Apps, OpenSSL, fetch_github
from gate_credential import (
    MAX_REQUEST_CHARACTERS,
    QUIT,
    Answer,
    CredentialRequest,
    answer,
)
from gate_declaration import AppAccess, declared_app
from gate_error import GateError
from gate_gh import run as run_gh
from gate_git import git_home
from gate_install import Machine, check, converge, setup
from gate_platform import Platform, SupportedPlatform

PLATFORMS: Mapping[SupportedPlatform, Platform] = {
    SupportedPlatform.WINDOWS: gate_windows,
    SupportedPlatform.POSIX: gate_posix,
}
PREFIX = "github-gate: "


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="github_gate.py", description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    install = commands.add_parser(
        "setup", help="install or check the gh shim and helper"
    )
    mode = install.add_mutually_exclusive_group()
    mode.add_argument(
        "--check", action="store_true", help="report drift, change nothing"
    )
    mode.add_argument(
        "--onboard",
        action="store_true",
        help="also enrol the App this repository declares, if missing",
    )
    commands.add_parser("converge", help="refresh installed runtime copies")
    onboard = commands.add_parser("onboard", help="enrol a secret on this machine")
    kinds = onboard.add_subparsers(dest="kind", required=True)
    app = kinds.add_parser("app", help="enrol an existing GitHub App's private key")
    app.add_argument("--app", required=True, dest="slug", help="the App's slug")
    app.add_argument("--key", required=True, type=Path, help="its private key (.pem)")
    credential = commands.add_parser("credential", help="Git credential helper")
    credential.add_argument("operation")
    return result


def gate_home(environment: Mapping[str, str], home: Path) -> Path:
    configured = environment.get("AGENT_STATE_DIRECTORY")
    state = Path(configured).expanduser() if configured else home / ".agents-state"
    return state / "github-gate"


def enrol(apps: Apps, slug: str, key: Path, now: float) -> None:
    app = apps(AppAccess(slug).slug)
    descriptor = app.enrol(key.read_bytes(), now)
    print(f"{PREFIX}enrolled GitHub App {slug} owned by {descriptor.owner}")
    covered = app.covered_accounts(now)
    if covered:
        print(f"{PREFIX}covered accounts: {', '.join(covered)}")
    else:
        print(f"{PREFIX}no installations yet; install it at {descriptor.install_url}")


def onboard_declared(
    apps: Apps, cwd: Path, environment: Mapping[str, str], now: float
) -> None:
    slug = declared_app(cwd, environment)
    if slug is None:
        return
    try:
        apps(slug).verify_enrolled()
    except GateError:
        if not sys.stdin.isatty():
            raise
        key = input(f"Path to GitHub App {slug}'s private key (.pem): ").strip()
        enrol(apps, slug, Path(key.strip('"')).expanduser(), now)


def machine_for(
    environment: Mapping[str, str],
    platform: Platform,
    script: Path,
    neutral: Path,
) -> Machine:
    home = Path.home()
    configured = environment.get("TJ_BIN_DIR")
    return Machine(
        source=script.parent,
        home=gate_home(environment, home),
        bin_directory=Path(configured).expanduser()
        if configured
        else platform.default_bin_directory(environment, home),
        python=Path(sys.executable).resolve(),
        platform=platform,
        environment=environment,
        neutral=neutral,
        git_home=git_home(environment, neutral),
    )


def command(
    arguments: argparse.Namespace,
    environment: Mapping[str, str],
    platform: Platform,
    script: Path,
    now: float,
) -> int:
    sign = OpenSSL(environment.get("PATH", ""))
    if arguments.command == "credential":
        apps = Apps(script.parent.parent, platform, fetch_github, sign)
        request = CredentialRequest.parse(sys.stdin.read(MAX_REQUEST_CHARACTERS))
        try:
            reply = answer(
                arguments.operation, request, environment, Path.cwd(), apps, now
            )
        except Exception as error:  # noqa: BLE001 - agents must never reach human helpers
            failed = QUIT if agent_host(environment) else ""
            reply = Answer(failed, f"unexpected {type(error).__name__}")
        sys.stdout.buffer.write(reply.text.encode())
        sys.stdout.flush()
        if reply.message:
            print(PREFIX + reply.message, file=sys.stderr)
        return 0
    apps = Apps(gate_home(environment, Path.home()), platform, fetch_github, sign)
    if arguments.command == "onboard":
        enrol(apps, arguments.slug, arguments.key, now)
        return 0
    with tempfile.TemporaryDirectory(prefix="github-gate-") as neutral:
        machine = machine_for(environment, platform, script, Path(neutral))
        if arguments.command == "converge":
            changes = converge(machine)
        elif arguments.check:
            problems = check(machine, Path.cwd(), apps)
            for problem in problems:
                print(PREFIX + problem, file=sys.stderr)
            if not problems:
                print(PREFIX + "ready")
            return 1 if problems else 0
        else:
            changes = setup(machine)
    for change in changes:
        print(PREFIX + change)
    if arguments.command == "setup":
        if not changes:
            print(PREFIX + "already set up")
        if arguments.onboard:
            onboard_declared(apps, Path.cwd(), environment, now)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    if sys.version_info < (3, 12):
        print("Python 3.12 or newer is required", file=sys.stderr)
        return 2
    arguments = list(sys.argv[1:] if argv is None else argv)
    environment = dict(os.environ)
    try:
        platform = PLATFORMS[SupportedPlatform(os.name)]
    except ValueError:
        print(PREFIX + "only Windows and POSIX are supported", file=sys.stderr)
        return 2
    script = Path(__file__).resolve()
    now = time.time()
    try:
        if arguments[:1] == ["gh"]:
            apps = Apps(
                script.parent.parent,
                platform,
                fetch_github,
                OpenSSL(environment.get("PATH", "")),
            )
            return run_gh(arguments[1:], environment, Path.cwd(), apps, platform, now)
        return command(
            parser().parse_args(arguments), environment, platform, script, now
        )
    except (GateError, OSError) as error:
        print(PREFIX + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
