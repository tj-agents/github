import os
import signal
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

GITHUB = "https://github.com"
SIGN_IN_TIMEOUT_SECONDS = 1800
SIGN_IN_PATHS = (f"{GITHUB}/login", f"{GITHUB}/session")


class SignInRequiredError(Exception):
    pass


def profile_path() -> Path:
    root = Path(
        os.environ.get("AGENT_STATE_DIRECTORY", str(Path.home() / ".agents-state"))
    )
    return root / "github-configuration" / "browser-profile"


def _command_lines() -> list[tuple[int, str]]:
    if os.name == "nt":
        listing = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                'ForEach-Object { "$($_.ProcessId)`t$($_.CommandLine)" }',
            ],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        rows = [line.split("\t", 1) for line in listing.splitlines() if "\t" in line]
        return [(int(pid), command) for pid, command in rows]
    processes = []
    for entry in Path("/proc").iterdir():
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ")
        except OSError:
            continue
        if entry.name.isdigit():
            processes.append((int(entry.name), command.decode(errors="ignore")))
    return processes


def _release(profile: Path) -> None:
    marker = str(profile).lower()
    for pid, command in _command_lines():
        if marker in command.lower():
            with suppress(OSError):
                os.kill(pid, signal.SIGTERM)
    time.sleep(1)
    for lock in profile.glob("Singleton*"):
        lock.unlink(missing_ok=True)


def signed_in_login(page: Page) -> str:
    meta = page.locator('meta[name="user-login"]')
    if meta.count() == 0:
        return ""
    return meta.first.get_attribute("content") or ""


def require_ordinary_page(page: Page) -> None:
    url = page.url
    if "/sessions/sudo" in url or page.locator("form[action*='sudo']").count():
        raise SignInRequiredError(
            "GitHub asked to confirm access (sudo). Run `login`, confirm access in "
            "the opened browser, then rerun."
        )
    if "/sessions/two-factor" in url:
        raise SignInRequiredError(
            "GitHub asked for two-factor authentication. Run `login`, then rerun."
        )
    if url.startswith(SIGN_IN_PATHS) or not signed_in_login(page):
        raise SignInRequiredError("Not signed in to GitHub. Run `login`, then rerun.")


@contextmanager
def browser(*, headless: bool) -> Iterator[Page]:
    profile = profile_path()
    profile.mkdir(parents=True, exist_ok=True)
    _release(profile)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile),
            channel="chrome",
            headless=headless,
            args=[] if headless else ["--start-maximized"],
            no_viewport=not headless,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.bring_to_front()
            yield page
        finally:
            context.close()


def login() -> str:
    with browser(headless=False) as page:
        page.goto(f"{GITHUB}/login", wait_until="domcontentloaded")
        deadline = time.monotonic() + SIGN_IN_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                if not page.url.startswith(SIGN_IN_PATHS) and "/sessions/" not in (
                    page.url
                ):
                    account = signed_in_login(page)
                    if account:
                        return account
            except PlaywrightError:
                pass
            page.wait_for_timeout(1000)
    raise SignInRequiredError("Timed out waiting for the GitHub sign-in to finish.")
