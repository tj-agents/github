# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright==1.64.1"]
# ///
import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import configuration_app
import configuration_browser
import configuration_declaration
import configuration_packages
import configuration_terraform
from playwright.sync_api import Page


def parse(arguments: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="github_configuration")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("login")
    for name in ("plan", "apply"):
        command = commands.add_parser(name)
        command.add_argument("--declaration", type=Path, required=True)
    admin_app = commands.add_parser("admin-app")
    admin_app.add_argument("--owner", required=True)
    admin_app.add_argument("--hcp-organization", required=True)
    admin_app.add_argument("--workspace", required=True)
    admin_app.add_argument("--existing-slug")
    admin_app.add_argument("--rotate-key", action="store_true")
    return parser.parse_args(arguments)


def converge(declaration: configuration_declaration.Declaration, *, write: bool) -> int:
    drift = 0
    with configuration_browser.browser(headless=True) as page:
        for package in declaration.packages:
            try:
                done, unmanaged = _converge_package(
                    page, declaration.owner, package, write
                )
            except configuration_packages.PackageMissingError as missing:
                print(missing, flush=True)
                continue
            drift += len(done)
            for change in done:
                print(("applied " if write else "") + change.describe(), flush=True)
            for repository in unmanaged:
                print(f"{package.type}/{package.name}: {repository} unmanaged")
    print(f"{drift} change(s) {'applied' if write else 'pending'}.")
    return 0


def _converge_package(
    page: Page,
    owner: str,
    package: configuration_declaration.Package,
    write: bool,
) -> tuple[list[configuration_packages.Change], list[str]]:
    if write:
        return configuration_packages.apply(page, owner, package), []
    return configuration_packages.plan(page, owner, package)


def main(arguments: Sequence[str]) -> int:
    options = parse(arguments)
    try:
        if options.command == "login":
            account = configuration_browser.login()
            print(f"Signed in to GitHub as {account}.")
            return 0
        if options.command == "admin-app":
            app = configuration_app.bootstrap(
                options.owner,
                options.hcp_organization,
                options.workspace,
                options.existing_slug,
                options.rotate_key,
            )
            print(
                f"{app.slug} installed as {app.installation_id}; "
                f"credentials are in HCP workspace {options.workspace}."
            )
            return 0
        declaration = configuration_declaration.load(options.declaration)
        if declaration.packages:
            converge(declaration, write=options.command == "apply")
        root = configuration_terraform.owner_root(options.declaration)
        if root is not None:
            if options.command == "plan":
                configuration_terraform.plan(root)
            else:
                print(f"API settings in {root} apply when the declaration merges.")
        return 0
    except (
        configuration_declaration.DeclarationError,
        configuration_terraform.TerraformError,
    ) as error:
        print(error, file=sys.stderr)
        return 1
    except (
        configuration_app.AppSetupError,
        configuration_browser.SignInRequiredError,
        configuration_packages.PackageAccessError,
    ) as stop:
        print(stop, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
