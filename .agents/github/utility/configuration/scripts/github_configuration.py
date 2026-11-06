# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright==1.64.1"]
# ///
import argparse
import sys
from collections.abc import Sequence

import configuration_browser


def parse(arguments: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="github_configuration")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("login")
    return parser.parse_args(arguments)


def main(arguments: Sequence[str]) -> int:
    options = parse(arguments)
    try:
        if options.command == "login":
            account = configuration_browser.login()
            print(f"Signed in to GitHub as {account}.")
            return 0
    except configuration_browser.SignInRequiredError as stop:
        print(stop, file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
