import getpass
import html
import json
import os
import re
import secrets
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from configuration_browser import GITHUB, browser
from playwright.sync_api import Page

CALLBACK = "http://127.0.0.1:47391/github-configuration"
HUMAN_TIMEOUT_MS = 30 * 60 * 1000
HCP = "https://app.terraform.io/api/v2"
PERMISSIONS = {
    "administration": "write",
    "environments": "write",
    "secrets": "write",
    "actions_variables": "write",
    "metadata": "read",
    "organization_administration": "write",
    "organization_secrets": "write",
    "organization_actions_variables": "write",
}


class AppSetupError(Exception):
    pass


@dataclass(frozen=True)
class AdminApp:
    slug: str
    installation_id: int | None


def _json(request: urllib.request.Request) -> Any:
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def _is_organization(owner: str) -> bool:
    user = _json(urllib.request.Request(f"https://api.github.com/users/{owner}"))
    return bool(user["type"] == "Organization")


def _create(page: Page, owner: str, name: str) -> dict[str, Any]:
    state = secrets.token_urlsafe(16)
    manifest = {
        "name": name,
        "url": f"{GITHUB}/{owner}",
        "redirect_url": CALLBACK,
        "setup_url": CALLBACK,
        "public": False,
        "default_permissions": PERMISSIONS,
        "default_events": [],
    }
    scope = f"organizations/{owner}/" if _is_organization(owner) else ""
    action = f"{GITHUB}/{scope}settings/apps/new?state={state}"
    value = html.escape(json.dumps(manifest))
    page.set_content(
        f'<form method="post" action="{action}">'
        f'<input type="hidden" name="manifest" value="{value}">'
        "</form><script>document.forms[0].submit()</script>"
    )
    page.route(
        f"{CALLBACK}*",
        lambda route: route.fulfill(body="Created.", content_type="text/plain"),
    )
    print(
        "In the opened window: confirm access if GitHub asks; the App is then "
        "created automatically.",
        flush=True,
    )
    page.get_by_role("button", name=re.compile(r"^\s*Create GitHub App")).first.click(
        timeout=HUMAN_TIMEOUT_MS
    )
    page.wait_for_url(f"{CALLBACK}*", timeout=120_000)
    query = parse_qs(urlparse(page.url).query)
    if "code" not in query:
        raise AppSetupError("GitHub returned without a manifest code")
    if query.get("state") != [state]:
        raise AppSetupError("GitHub returned a different manifest state")
    conversion = urllib.request.Request(
        f"https://api.github.com/app-manifests/{query['code'][0]}/conversions",
        method="POST",
        headers={"Accept": "application/vnd.github+json"},
    )
    created: dict[str, Any] = _json(conversion)
    return created


def _install(page: Page, slug: str) -> int:
    page.route(
        f"{CALLBACK}*",
        lambda route: route.fulfill(body="Installed.", content_type="text/plain"),
    )
    page.goto(f"{GITHUB}/apps/{slug}/installations/new", wait_until="domcontentloaded")
    page.locator("#install_target_all").check(timeout=HUMAN_TIMEOUT_MS)
    install = page.get_by_role("button", name=re.compile(r"^\s*Install\b"))
    install.first.click(timeout=60_000)
    page.wait_for_url(
        re.compile(rf"{re.escape(CALLBACK)}|/settings/installations/\d+"),
        timeout=120_000,
    )
    found = re.search(r"installation_id=(\d+)|/settings/installations/(\d+)", page.url)
    if found is None:
        raise AppSetupError(f"No installation id after installing: {page.url}")
    return int(found.group(1) or found.group(2))


def _hcp_token() -> str:
    path = (
        Path(os.environ["APPDATA"], "terraform.d")
        if os.name == "nt"
        else Path.home() / ".terraform.d"
    ) / "credentials.tfrc.json"
    token: str = json.loads(path.read_text(encoding="utf-8"))["credentials"][
        "app.terraform.io"
    ]["token"]
    return token


def _hcp(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    request = urllib.request.Request(
        HCP + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {_hcp_token()}",
            "Content-Type": "application/vnd.api+json",
        },
    )
    return _json(request)


def ensure_workspace(organization: str, workspace: str, working_directory: str) -> None:
    names = {
        item["attributes"]["name"]
        for item in _hcp("GET", f"/organizations/{organization}/workspaces")["data"]
    }
    if workspace in names:
        return
    _hcp(
        "POST",
        f"/organizations/{organization}/workspaces",
        {
            "data": {
                "type": "workspaces",
                "attributes": {
                    "name": workspace,
                    "execution-mode": "remote",
                    "working-directory": working_directory,
                    "auto-apply": False,
                },
            }
        },
    )


def _variables(organization: str, workspace: str) -> tuple[str, dict[str, str]]:
    workspace_id = _hcp("GET", f"/organizations/{organization}/workspaces/{workspace}")[
        "data"
    ]["id"]
    existing = {
        item["attributes"]["key"]: item["id"]
        for item in _hcp("GET", f"/workspaces/{workspace_id}/vars")["data"]
    }
    return workspace_id, existing


def _set_variables(
    organization: str, workspace: str, values: dict[str, tuple[str, bool]]
) -> None:
    workspace_id, existing = _variables(organization, workspace)
    for key, (value, sensitive) in values.items():
        attributes = {
            "key": key,
            "value": value,
            "category": "env",
            "sensitive": sensitive,
        }
        if key in existing:
            _hcp(
                "PATCH",
                f"/workspaces/{workspace_id}/vars/{existing[key]}",
                {
                    "data": {
                        "type": "vars",
                        "id": existing[key],
                        "attributes": attributes,
                    }
                },
            )
        else:
            _hcp(
                "POST",
                f"/workspaces/{workspace_id}/vars",
                {"data": {"type": "vars", "attributes": attributes}},
            )


def _pem_variable(pem: str) -> tuple[str, bool]:
    return pem.strip().replace("\n", "\\n"), True


def _settings(owner: str, slug: str) -> str:
    scope = f"organizations/{owner}/" if _is_organization(owner) else ""
    return f"{GITHUB}/{scope}settings/apps/{slug}"


def _key_forms(page: Page) -> list[str]:
    forms = page.locator("form").filter(has=page.locator("button.btn-danger.btn-block"))
    return [form.get_attribute("action") or "" for form in forms.all()]


def _new_key(page: Page, owner: str, slug: str) -> tuple[str, list[str]]:
    page.goto(f"{_settings(owner, slug)}/key_pairs", wait_until="domcontentloaded")
    print(
        "In the opened window: confirm access if GitHub asks; the rest is automatic.",
        flush=True,
    )
    generate = page.locator("button.js-generate-integration-key")
    generate.wait_for(state="visible", timeout=HUMAN_TIMEOUT_MS)
    previous = _key_forms(page)
    with page.expect_download(timeout=120_000) as pending:
        generate.click()
    download = pending.value
    pem = Path(download.path()).read_text(encoding="utf-8")
    download.delete()
    return pem, previous


def _delete_keys(page: Page, owner: str, slug: str, actions: list[str]) -> None:
    for action in actions:
        page.goto(f"{_settings(owner, slug)}/key_pairs", wait_until="domcontentloaded")
        form = page.locator(f'form[action="{action}"]')
        if form.count() == 0:
            continue
        form.locator("xpath=ancestor::details[1]/summary").click()
        form.locator("button.btn-danger.btn-block").click()
        page.wait_for_load_state("domcontentloaded")
        form.wait_for(state="detached", timeout=60_000)
    page.goto(f"{_settings(owner, slug)}/key_pairs", wait_until="domcontentloaded")
    remaining = set(_key_forms(page)) & set(actions)
    if remaining:
        raise AppSetupError(f"Old private keys remain on {slug}")


def _ensure_name(page: Page, owner: str, slug: str) -> None:
    page.goto(_settings(owner, slug), wait_until="domcontentloaded")
    name = page.get_by_label("GitHub App name")
    name.wait_for(timeout=HUMAN_TIMEOUT_MS)
    if name.input_value() == slug:
        return
    name.fill(slug)
    with page.expect_navigation(wait_until="domcontentloaded"):
        page.get_by_role("button", name="Save changes").first.click()


def bootstrap(
    owner: str,
    organization: str,
    workspace: str,
    existing_slug: str | None,
    rotate_key: bool,
    working_directory: str,
) -> AdminApp:
    ensure_workspace(organization, workspace, working_directory)
    stored = _variables(organization, workspace)[1]
    installation: int | None = None
    with browser(headless=False) as page:
        if existing_slug is None:
            created = _create(page, owner, f"{owner.lower()}-configuration")
            slug = str(created["slug"])
            _set_variables(
                organization,
                workspace,
                {
                    "GITHUB_APP_ID": (str(created["id"]), False),
                    "GITHUB_APP_PEM_FILE": _pem_variable(str(created["pem"])),
                },
            )
        else:
            slug = existing_slug
            if rotate_key or "GITHUB_APP_PEM_FILE" not in stored:
                pem, previous = _new_key(page, owner, slug)
                _set_variables(
                    organization, workspace, {"GITHUB_APP_PEM_FILE": _pem_variable(pem)}
                )
                _delete_keys(page, owner, slug, previous)
        _ensure_name(page, owner, slug)
        if existing_slug is None or "GITHUB_APP_INSTALLATION_ID" not in stored:
            installation = _install(page, slug)
    if installation is not None:
        _set_variables(
            organization,
            workspace,
            {"GITHUB_APP_INSTALLATION_ID": (str(installation), False)},
        )
    return AdminApp(slug, installation)


def plan_json(run_id: str) -> Any:
    run = _hcp("GET", f"/runs/{run_id}")
    plan_id = run["data"]["relationships"]["plan"]["data"]["id"]
    return _hcp("GET", f"/plans/{plan_id}/json-output")


def store_prompted(organization: str, workspaces: list[str], keys: list[str]) -> None:
    values = {key: getpass.getpass(f"{key}: ") for key in keys}
    if not all(values.values()):
        raise AppSetupError("Every value is required; nothing was stored.")
    for workspace in workspaces:
        _set_variables(
            organization,
            workspace,
            {key: (value, True) for key, value in values.items()},
        )
