import contextlib
import hashlib
import http.client
import json
import os
import subprocess
import sys
import threading
import urllib.parse
from collections.abc import Iterator, Sequence
from pathlib import Path

import github_access
import github_packages as packages
import pytest
from access_core import AccessError, TokenHandler, TokenServer

TOKEN = "ghp_" + "a" * 36
PREVIOUS = "ghp_" + "b" * 36
MANIFEST = b'{"schemaVersion":2,"manifests":[]}'
IMAGE = "ghcr.io/example/image@sha256:" + hashlib.sha256(MANIFEST).hexdigest()
TARGET = packages.PackageTarget("reader", IMAGE)


class Registry:
    def __init__(self, login: str = "reader", scopes: str = "read:packages") -> None:
        self.login = login
        self.scopes = scopes
        self.urls: list[str] = []
        self.denied = False
        self.manifest = MANIFEST

    def read(self, url: str, authorization: str, accept: str) -> packages.Response:
        self.urls.append(url)
        if url == "https://api.github.com/user":
            assert authorization == "Bearer " + TOKEN
            return packages.Response(
                json.dumps({"login": self.login}).encode(),
                {"x-oauth-scopes": self.scopes},
            )
        if url.startswith("https://ghcr.io/token?"):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            assert query == {
                "service": ["ghcr.io"],
                "scope": ["repository:example/image:pull"],
            }
            assert authorization.startswith("Basic ")
            return packages.Response(b'{"token":"registry-test-token"}', {})
        assert url == f"https://ghcr.io/v2/example/image/manifests/{TARGET.digest}"
        assert authorization == "Bearer registry-test-token"
        assert "application/vnd.oci.image.index.v1+json" in accept
        if self.denied:
            raise AccessError("package-access-denied: fixture registry denial")
        return packages.Response(self.manifest, {})


class Store:
    def __init__(self) -> None:
        self.token: str | None = PREVIOUS
        self.writes: list[str] = []
        self.available = True
        self.discard_candidate = False
        self.fail_after_candidate_write = False
        self.fail_restore = False

    def check(self, login: str) -> None:
        assert login == "reader"
        if not self.available:
            raise AccessError("secure-store-unavailable: fixture locked store")

    def read(self, login: str) -> str | None:
        assert login == "reader"
        return self.token

    def write(self, login: str, token: str) -> None:
        assert login == "reader"
        self.writes.append(token)
        if token == TOKEN and self.discard_candidate:
            return
        self.token = token
        if token == TOKEN and self.fail_after_candidate_write:
            raise AccessError("secure-store-unavailable: fixture failed write")
        if token == PREVIOUS and self.fail_restore:
            raise AccessError("secure-store-unavailable: fixture failed restore")

    def erase(self) -> None:
        self.token = None


@pytest.mark.parametrize("previous", [PREVIOUS, None])
@pytest.mark.parametrize("failed_write", [True, False])
def test_onboard_restores_previous_slot_after_failed_write_or_readback(
    previous: str | None, failed_write: bool
) -> None:
    store = Store()
    store.token = previous
    store.fail_after_candidate_write = failed_write
    store.discard_candidate = not failed_write
    with pytest.raises(AccessError, match="credential-write-failed"):
        packages.onboard(TARGET, TOKEN, store, Registry())
    assert store.token == previous


def test_onboard_reports_uncertain_state_if_restore_cannot_be_proven() -> None:
    store = Store()
    store.fail_after_candidate_write = True
    store.fail_restore = True
    with pytest.raises(AccessError, match="credential-rollback-failed"):
        packages.onboard(TARGET, TOKEN, store, Registry())


@pytest.mark.parametrize(
    ("login", "scopes", "failure"),
    [
        ("another-account", "read:packages", "identity-mismatch"),
        ("reader", "repo, read:packages", "scope-mismatch"),
        ("reader", "read:packages, workflow", "scope-mismatch"),
        ("reader", "write:packages, read:packages", "scope-mismatch"),
        ("reader", "", "scope-mismatch"),
    ],
)
def test_onboard_rejects_wrong_identity_or_scope_before_store(
    login: str, scopes: str, failure: str
) -> None:
    store, registry = Store(), Registry(login, scopes)
    with pytest.raises(AccessError, match=failure):
        packages.onboard(TARGET, TOKEN, store, registry)
    assert store.token == PREVIOUS
    assert store.writes == []
    assert registry.urls == ["https://api.github.com/user"]


@pytest.mark.parametrize("wrong_digest", [False, True])
def test_onboard_failed_manifest_validation_preserves_saved_credential(
    wrong_digest: bool,
) -> None:
    store, registry = Store(), Registry()
    if wrong_digest:
        registry.manifest = b'{"schemaVersion":2}'
    else:
        registry.denied = True
    with pytest.raises(AccessError, match=r"package-(digest-mismatch|access-denied)"):
        packages.onboard(TARGET, TOKEN, store, registry)
    assert store.token == PREVIOUS
    assert store.writes == []


def test_onboard_saves_only_after_identity_scope_and_digest_verification() -> None:
    store, registry = Store(), Registry(login="READER")
    packages.onboard(TARGET, TOKEN, store, registry)
    assert store.token == TOKEN
    assert store.writes == [TOKEN]
    assert len(registry.urls) == 3


def test_onboarding_unavailable_store_does_not_open_token_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store()
    store.available = False
    monkeypatch.setattr(
        packages, "receive_token_page", lambda _: pytest.fail("opened page")
    )
    with pytest.raises(AccessError, match="secure-store-unavailable"):
        packages.run(TARGET, store, onboarding=True)
    assert store.token == PREVIOUS


def test_check_reuses_secure_credential_without_browser_or_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, registry = Store(), Registry()
    store.token = TOKEN
    monkeypatch.setattr(packages, "HTTPS", lambda: registry)
    monkeypatch.setattr(
        packages, "receive_token_page", lambda _: pytest.fail("opened page")
    )
    result = packages.run(TARGET, store, onboarding=False)
    assert result["status"] == "package-access-verified"
    assert result["image"] == IMAGE
    assert TOKEN not in json.dumps(result)
    assert store.writes == []


@pytest.mark.parametrize(
    "image",
    [
        "ghcr.io/example/image:latest",
        "docker.io/example/image@sha256:" + "a" * 64,
        "ghcr.io/example/../image@sha256:" + "a" * 64,
        IMAGE + "?redirect=evil",
    ],
)
def test_target_rejects_mutable_or_other_registry_images(image: str) -> None:
    with pytest.raises(AccessError, match="image-invalid"):
        packages.PackageTarget("reader", image)


@pytest.mark.parametrize(
    "repository", ["my__image", "my--image", "my---image", "path__part/my--image"]
)
def test_target_accepts_oci_repository_separators(repository: str) -> None:
    image = f"ghcr.io/example/{repository}@sha256:" + "a" * 64
    target = packages.PackageTarget("reader", image)
    assert target.repository == f"example/{repository}"
    assert target.digest == "sha256:" + "a" * 64


@pytest.mark.parametrize(
    "repository", ["my___image", "my..image", "my_-image", "my--", "--image"]
)
def test_target_rejects_invalid_oci_repository_separators(repository: str) -> None:
    with pytest.raises(AccessError, match="image-invalid"):
        packages.PackageTarget(
            "reader", f"ghcr.io/example/{repository}@sha256:" + "a" * 64
        )


@pytest.mark.parametrize(
    "configuration",
    [{}, {"auths": {"ghcr.io": {"auth": "not-used"}}}, {"credsStore": "plaintext"}],
)
def test_docker_store_fails_closed_without_secure_helper(
    tmp_path: Path, configuration: dict[str, object]
) -> None:
    (tmp_path / "config.json").write_text(json.dumps(configuration), encoding="utf-8")
    with pytest.raises(AccessError, match="secure-store-unavailable"):
        packages.DockerStore(tmp_path, {}, "win32")


@pytest.mark.parametrize(
    ("platform", "helper"),
    [
        ("win32", "desktop"),
        ("linux", "desktop.exe"),
        ("linux", "pass"),
        ("linux", "secretservice"),
    ],
)
def test_docker_store_uses_selected_helper_stdin_and_preserves_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str, helper: str
) -> None:
    config = tmp_path / "config.json"
    original = json.dumps(
        {
            "credsStore": "unrelated",
            "credHelpers": {"ghcr.io": helper},
            "auths": {"unrelated.example": {}},
        }
    )
    config.write_text(original, encoding="utf-8")
    calls: list[tuple[list[str], str]] = []

    executable = str(tmp_path / ("docker-credential-" + helper))

    def which(name: str) -> str:
        assert name == executable
        return executable

    def run(
        command: Sequence[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        submitted = kwargs.pop("input")
        assert isinstance(submitted, str)
        assert TOKEN not in str(command)
        assert TOKEN not in str(kwargs)
        calls.append((list(command), submitted))
        operation = command[-1]
        output = {
            "list": '{"ghcr.io":"reader","other.registry":"other-user"}',
            "get": json.dumps({"Username": "reader", "Secret": TOKEN}),
            "store": "",
        }
        return subprocess.CompletedProcess(command, 0, output[operation], "")

    monkeypatch.setattr("github_packages.shutil.which", which)
    monkeypatch.setattr("github_packages.subprocess.run", run)
    store = packages.DockerStore(
        tmp_path, {"PATH": str(tmp_path), "GH_TOKEN": "unrelated-secret"}, platform
    )
    store.check("reader")
    assert store.read("reader") == TOKEN
    store.write("reader", TOKEN)
    assert calls[0] == ([executable, "list"], "")
    assert calls[2] == ([executable, "get"], "ghcr.io")
    assert json.loads(calls[3][1]) == {
        "ServerURL": "ghcr.io",
        "Username": "reader",
        "Secret": TOKEN,
    }
    assert config.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    ("output", "returncode", "failure"),
    [
        ('{"ghcr.io":"another-account"}', 0, "credential-conflict"),
        (TOKEN, 1, "secure-store-unavailable"),
    ],
)
def test_docker_store_denies_conflict_or_locked_helper_without_relaying_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output: str,
    returncode: int,
    failure: str,
) -> None:
    (tmp_path / "config.json").write_text('{"credsStore":"desktop"}', encoding="utf-8")
    monkeypatch.setattr("github_packages.shutil.which", lambda name: name)
    monkeypatch.setattr(
        "github_packages.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            ["helper"], returncode, output, TOKEN
        ),
    )
    store = packages.DockerStore(tmp_path, {"PATH": str(tmp_path)}, "win32")
    with pytest.raises(AccessError, match=failure) as caught:
        store.check("reader")
    assert TOKEN not in str(caught.value)


def test_docker_store_missing_credential_requests_onboarding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "config.json").write_text('{"credsStore":"desktop"}', encoding="utf-8")
    monkeypatch.setattr("github_packages.shutil.which", lambda name: name)
    monkeypatch.setattr(
        "github_packages.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(["helper"], 0, "{}", ""),
    )
    store = packages.DockerStore(tmp_path, {"PATH": str(tmp_path)}, "win32")
    assert store.read("reader") is None


@pytest.mark.parametrize("installed", [True, False])
def test_docker_store_never_resolves_helper_from_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, installed: bool
) -> None:
    checkout, trusted = tmp_path / "checkout", tmp_path / "installed"
    checkout.mkdir()
    trusted.mkdir()
    name = "docker-credential-desktop" + (".exe" if sys.platform == "win32" else "")
    shadow = checkout / name
    shadow.write_bytes(b"inert file; never executed")
    shadow.chmod(0o755)
    if installed:
        real = trusted / name
        real.write_bytes(b"inert file; never executed")
        real.chmod(0o755)
    (tmp_path / "config.json").write_text('{"credsStore":"desktop"}', encoding="utf-8")
    monkeypatch.chdir(checkout)
    environment = {"PATH": os.pathsep.join(["", ".", "relative", str(trusted)])}
    if installed:
        store = packages.DockerStore(tmp_path, environment, sys.platform)
        assert Path(store.executable).samefile(trusted / name)
    else:
        with pytest.raises(AccessError, match="secure-store-unavailable"):
            packages.DockerStore(tmp_path, environment, sys.platform)


@pytest.fixture
def package_server() -> Iterator[TokenServer]:
    with TokenServer(("127.0.0.1", 0), TokenHandler) as server:
        server.page, server.nonce = TARGET.token_page(), "test-nonce"
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}
        )
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=3)


@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.parametrize("attack", ["host", "origin", "nonce", "none"])
def test_package_page_accepts_only_guarded_local_submission(
    package_server: TokenServer, attack: str, capsys: pytest.CaptureFixture[str]
) -> None:
    address = f"127.0.0.1:{package_server.server_port}"
    headers = {
        "Host": "evil.example" if attack == "host" else address,
        "Origin": "https://evil.example" if attack == "origin" else "http://" + address,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    body = urllib.parse.urlencode({"token": TOKEN, "owner_confirmed": "yes"})
    with contextlib.closing(
        http.client.HTTPConnection("127.0.0.1", package_server.server_port)
    ) as connection:
        connection.request(
            "POST", "/wrong" if attack == "nonce" else "/test-nonce", body, headers
        )
        response = connection.getresponse()
        assert response.status == (200 if attack == "none" else 403)
        assert TOKEN not in response.read().decode()
    assert package_server.token == (TOKEN if attack == "none" else None)
    assert TOKEN not in str(capsys.readouterr())


def test_package_form_requests_only_read_packages_and_cli_requires_target() -> None:
    page = TARGET.token_page()
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(page.token_url).query)
    assert query["scopes"] == ["read:packages"]
    assert "repo" not in page.instructions
    args = github_access.parser().parse_args(
        ["packages", "check", "--login", "reader", "--image", IMAGE]
    )
    assert args.package_operation == "check"
