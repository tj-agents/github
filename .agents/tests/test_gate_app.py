import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from gate_app import AppError, Apps, OpenSSL
from gate_platform import PrivateStateError
from gate_sandbox import CURRENT_PLATFORM, NOW, FakeGitHub, Sandbox

KEY = b"-----BEGIN RSA PRIVATE KEY-----\nsample\n-----END RSA PRIVATE KEY-----\n"


def claims(jwt: str) -> dict[str, object]:
    payload = jwt.split(".")[1]
    decoded: dict[str, object] = json.loads(
        base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    )
    return decoded


def test_enrol_verifies_the_key_and_records_github_public_descriptor(
    apps: Apps, github: FakeGitHub
) -> None:
    app = apps("sample-bot")
    descriptor = app.enrol(KEY, NOW)
    assert (descriptor.app_id, descriptor.client_id) == (101, "Iv1.sample")
    assert app.key.read_bytes() == KEY
    CURRENT_PLATFORM.verify_private(app.key, directory=False)
    verify = next(bearer for method, path, bearer in github.calls if path == "/app")
    assert verify is not None
    assert claims(verify)["iss"] == "Iv1.sample"
    assert claims(verify)["exp"] == int(NOW) + 540
    assert [path for _, path, _ in github.calls] == ["/apps/sample-bot", "/app"]


def test_enrol_keeps_no_key_that_signs_as_another_app(
    apps: Apps, github: FakeGitHub
) -> None:
    github.signed_as = 999
    app = apps("sample-bot")
    with pytest.raises(AppError, match="does not authenticate GitHub App sample-bot"):
        app.enrol(KEY, NOW)
    assert not app.key.exists()


def test_enrol_rejects_anything_but_a_private_key(apps: Apps) -> None:
    with pytest.raises(AppError, match="not a PEM private key"):
        apps("sample-bot").enrol(b"ghp_not_a_key", NOW)


def test_a_covered_owner_gets_one_cached_installation_token(
    apps: Apps, github: FakeGitHub
) -> None:
    app = apps("sample-bot")
    app.enrol(KEY, NOW)
    first = app.installation_token("Sample-Org", NOW)
    assert first == app.installation_token("sample-org", NOW + 60)
    assert sum(method == "POST" for method, _, _ in github.calls) == 1
    CURRENT_PLATFORM.verify_private(app.tokens / "sample-org.json", directory=False)


def test_an_expiring_token_is_reissued(apps: Apps, github: FakeGitHub) -> None:
    app = apps("sample-bot")
    app.enrol(KEY, NOW)
    first = app.installation_token("sample-org", NOW)
    expiry = 1_799_830_800.0 + 86_400 * 2
    later = app.installation_token("sample-org", expiry)
    assert first != later
    assert github.issued == 2


def test_an_uncovered_owner_is_refused_after_rereading_installations(
    apps: Apps, github: FakeGitHub
) -> None:
    app = apps("sample-bot")
    app.enrol(KEY, NOW)
    app.installation_token("sample-org", NOW)
    with pytest.raises(AppError, match="other-org is not covered by GitHub App"):
        app.installation_token("other-org", NOW)
    listed = [path for _, path, _ in github.calls if path.startswith("/app/inst")]
    assert len([path for path in listed if "?" in path]) == 2
    assert github.issued == 1


def test_an_owner_installed_later_is_found_without_reenrolling(
    apps: Apps, github: FakeGitHub
) -> None:
    app = apps("sample-bot")
    app.enrol(KEY, NOW)
    app.installation_token("sample-org", NOW)
    github.installed = {"sample-org": 7, "later-org": 8}
    assert app.installation_token("later-org", NOW).startswith("ghs_")
    assert app.covered_accounts(NOW) == ["later-org", "sample-org"]


def test_an_unenrolled_app_fails_closed_with_the_onboarding_command(
    apps: Apps, github: FakeGitHub
) -> None:
    with pytest.raises(AppError, match="onboard app --app sample-bot"):
        apps("sample-bot").installation_token("sample-org", NOW)
    assert github.calls == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_a_key_others_can_read_is_refused(apps: Apps) -> None:
    app = apps("sample-bot")
    app.enrol(KEY, NOW)
    app.key.chmod(0o644)
    with pytest.raises(PrivateStateError, match="readable by other users"):
        app.installation_token("sample-org", NOW)


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl")
def test_openssl_signs_an_rs256_jwt_github_can_verify(
    sandbox: Sandbox, tmp_path: Path
) -> None:
    key = tmp_path / "key.pem"
    public = tmp_path / "public.pem"
    message = tmp_path / "message"
    signature = tmp_path / "signature"
    openssl = shutil.which("openssl")
    assert openssl is not None
    subprocess.run(
        [openssl, "genrsa", "-out", key, "2048"], capture_output=True, check=True
    )
    subprocess.run(
        [openssl, "rsa", "-in", key, "-pubout", "-out", public],
        capture_output=True,
        check=True,
    )
    signed = OpenSSL(str(Path(openssl).parent))(key, b"header.claims")
    message.write_bytes(b"header.claims")
    signature.write_bytes(signed)
    verified = subprocess.run(
        [
            openssl,
            "dgst",
            "-sha256",
            "-verify",
            public,
            "-signature",
            signature,
            message,
        ],
        capture_output=True,
        check=False,
    )
    assert verified.returncode == 0
