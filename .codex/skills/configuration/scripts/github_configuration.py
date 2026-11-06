# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright==1.64.1"]
# ///
import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import configuration_browser
import configuration_declaration
import configuration_packages


def parse(arguments: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="github_configuration")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("login")
    for name in ("plan", "apply"):
        command = commands.add_parser(name)
        command.add_argument("--declaration", type=Path, required=True)
    return parser.parse_args(arguments)


def converge(declaration: configuration_declaration.Declaration, *, write: bool) -> int:
    drift = 0
    with configuration_browser.browser(headless=True) as page:
        for package in declaration.packages:
            if write:
                done = configuration_packages.apply(page, declaration.owner, package)
                unmanaged: list[str] = []
            else:
                done, unmanaged = configuration_packages.plan(
                    page, declaration.owner, package
                )
            drift += len(done)
            for change in done:
                print(("applied " if write else "") + change.describe(), flush=True)
            for repository in unmanaged:
                print(f"{package.type}/{package.name}: {repository} unmanaged")
    print(f"{drift} change(s) {'applied' if write else 'pending'}.")
    return 0


def main(arguments: Sequence[str]) -> int:
    options = parse(arguments)
    try:
        if options.command == "login":
            account = configuration_browser.login()
            print(f"Signed in to GitHub as {account}.")
            return 0
        declaration = configuration_declaration.load(options.declaration)
        return converge(declaration, write=options.command == "apply")
    except configuration_declaration.DeclarationError as error:
        print(error, file=sys.stderr)
        return 1
    except (
        configuration_browser.SignInRequiredError,
        configuration_packages.PackageAccessError,
    ) as stop:
        print(stop, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
