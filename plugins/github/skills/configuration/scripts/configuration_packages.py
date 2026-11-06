from dataclasses import dataclass

from configuration_browser import GITHUB, require_ordinary_page
from configuration_declaration import Package, Role
from playwright.sync_api import Locator, Page

PERMISSIONS = {
    Role.READ: "contents",
    Role.WRITE: "administration",
    Role.ADMIN: "maintainer",
}
LABELS = {"Read": Role.READ, "Write": Role.WRITE, "Admin": Role.ADMIN}


class PackageAccessError(Exception):
    pass


@dataclass(frozen=True)
class Change:
    package: Package
    repository: str
    current: Role | None
    desired: Role

    def describe(self) -> str:
        target = f"{self.package.type}/{self.package.name}: {self.repository}"
        if self.current is None:
            return f"{target} add {self.desired}"
        return f"{target} {self.current} -> {self.desired}"


def changes(package: Package, current: dict[str, Role]) -> list[Change]:
    return [
        Change(package, repository, current.get(repository), role)
        for repository, role in sorted(package.actions_access.items())
        if current.get(repository) != role
    ]


def _open(page: Page, owner: str, package: Package) -> None:
    for scope in ("orgs", "users"):
        url = (
            f"{GITHUB}/{scope}/{owner}/packages/{package.type}/{package.name}/settings"
        )
        response = page.goto(url, wait_until="domcontentloaded")
        require_ordinary_page(page)
        if response is not None and response.status != 404:
            if not response.ok:
                raise PackageAccessError(f"{url} returned {response.status}")
            return
    raise PackageAccessError(f"{owner} has no {package.type} package {package.name}")


def _section(page: Page) -> Locator:
    return page.locator(
        "[data-bulk-actions-url$='/repositories_toolbar_actions']"
    ).first


def _rows(page: Page) -> Locator:
    return _section(page).locator(".Box-row")


def _row(page: Page, repository: str) -> Locator:
    return _rows(page).filter(
        has=page.locator(f"input.js-bulk-actions-toggle[aria-label='{repository}']")
    )


def read(page: Page) -> dict[str, Role]:
    access: dict[str, Role] = {}
    for row in _rows(page).all():
        repository = row.locator("input.js-bulk-actions-toggle").get_attribute(
            "aria-label"
        )
        label = row.locator("summary [data-menu-button]").inner_text().strip()
        if not repository or label not in LABELS:
            raise PackageAccessError(f"Unrecognised Actions access row: {repository!r}")
        access[repository] = LABELS[label]
    return access


def _add(page: Page, repository: str) -> None:
    page.locator("summary:has-text('Add Repository')").first.click()
    dialog = page.locator("details-dialog[aria-label='Add Repository']").first
    dialog.locator("input[type=search]").fill(repository)
    dialog.locator(f"input[type=checkbox][aria-label='{repository}']").check()
    with page.expect_navigation(wait_until="domcontentloaded"):
        dialog.locator("button:has-text('Add repositories')").click()
    require_ordinary_page(page)


def _set_role(page: Page, repository: str, role: Role) -> None:
    row = _row(page, repository)
    row.locator("summary:has-text('Role:')").click()
    row.locator(
        f"label[role=menuitemradio]:has(input[value='{PERMISSIONS[role]}'])"
    ).click()
    row.locator(".js-permission-success").wait_for(state="visible")


def plan(page: Page, owner: str, package: Package) -> tuple[list[Change], list[str]]:
    _open(page, owner, package)
    current = read(page)
    unmanaged = sorted(set(current) - set(package.actions_access))
    return changes(package, current), unmanaged


def apply(page: Page, owner: str, package: Package) -> list[Change]:
    pending, _ = plan(page, owner, package)
    for change in pending:
        if change.current is None:
            _add(page, change.repository)
        if read(page).get(change.repository) != change.desired:
            _set_role(page, change.repository, change.desired)
    if pending:
        _open(page, owner, package)
        remaining = changes(package, read(page))
        if remaining:
            raise PackageAccessError(
                "Did not converge: " + "; ".join(c.describe() for c in remaining)
            )
    return pending
