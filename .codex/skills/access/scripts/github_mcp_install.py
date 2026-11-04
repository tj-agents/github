from __future__ import annotations

import hashlib
import http.client
import shutil
import ssl
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import IO

from access_core import AccessError
from github_mcp_release import ReleaseArtifact

VERSION = "v2.1.0"
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXTRACTED_BYTES = 150 * 1024 * 1024
RELEASE_ROOT = (
    "https://github.com/github/github-mcp-server/releases/download/" + VERSION + "/"
)
_REDIRECT_HOSTS = frozenset(
    {
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _root(state_root: Path) -> Path:
    return state_root / "github-access" / "tools" / "github-mcp-server-v2.1.0"


def _paths(state_root: Path, release: ReleaseArtifact) -> tuple[Path, Path]:
    root = _root(state_root)
    return root / release.archive_name, root / release.binary_name


def _safe_member(name: str) -> bool:
    candidate = Path(name)
    return bool(name) and not candidate.is_absolute() and ".." not in candidate.parts


def _extract_zip(archive: Path, output: Path, release: ReleaseArtifact) -> int:
    with zipfile.ZipFile(archive) as payload:
        members = payload.infolist()
        if any(not _safe_member(member.filename) for member in members):
            raise AccessError("mcp-install-invalid: unsafe release archive")
        matches = [
            member
            for member in members
            if Path(member.filename).name == release.binary_name
        ]
        if len(matches) != 1 or matches[0].is_dir():
            raise AccessError("mcp-install-invalid: release binary is missing")
        total = sum(member.file_size for member in members)
        if total > MAX_EXTRACTED_BYTES:
            raise AccessError("mcp-install-invalid: release archive is oversized")
        with payload.open(matches[0]) as source, output.open("xb") as destination:
            shutil.copyfileobj(source, destination, 1024 * 1024)
    return total


def _extract_tar(archive: Path, output: Path, release: ReleaseArtifact) -> int:
    with tarfile.open(archive, "r:gz") as payload:
        members = payload.getmembers()
        if any(
            not _safe_member(member.name) or not member.isfile() for member in members
        ):
            raise AccessError("mcp-install-invalid: unsafe release archive")
        matches = [
            member
            for member in members
            if Path(member.name).name == release.binary_name
        ]
        if len(matches) != 1:
            raise AccessError("mcp-install-invalid: release binary is missing")
        total = sum(member.size for member in members)
        if total > MAX_EXTRACTED_BYTES:
            raise AccessError("mcp-install-invalid: release archive is oversized")
        source = payload.extractfile(matches[0])
        if source is None:
            raise AccessError("mcp-install-invalid: release binary is missing")
        with source, output.open("xb") as destination:
            shutil.copyfileobj(source, destination, 1024 * 1024)
    return total


def _extract(archive: Path, target: Path, release: ReleaseArtifact) -> None:
    target.mkdir(parents=True, exist_ok=True)
    output = target / release.binary_name
    try:
        total = (
            _extract_zip(archive, output, release)
            if archive.suffix == ".zip"
            else _extract_tar(archive, output, release)
        )
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as error:
        raise AccessError(
            "mcp-install-invalid: read the pinned GitHub MCP release"
        ) from error
    if total < 1 or _digest(output) != release.binary_sha256:
        output.unlink(missing_ok=True)
        raise AccessError("mcp-install-invalid: pinned GitHub MCP binary hash mismatch")
    if release.binary_name != "github-mcp-server.exe":
        output.chmod(0o700)


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file: IO[bytes],
        code: int,
        message: str,
        headers: http.client.HTTPMessage,
        target: str,
    ) -> urllib.request.Request | None:
        parsed = urllib.parse.urlparse(target)
        if parsed.scheme != "https" or parsed.hostname not in _REDIRECT_HOSTS:
            raise AccessError("mcp-download-invalid: untrusted release redirect")
        return super().redirect_request(request, file, code, message, headers, target)


def _download(destination: Path, release: ReleaseArtifact) -> None:
    parsed = urllib.parse.urlparse(RELEASE_ROOT)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        raise AccessError("mcp-download-invalid: pinned release URL is invalid")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _Redirects(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    request = urllib.request.Request(
        RELEASE_ROOT + release.archive_name,
        headers={"User-Agent": "tj-agents-github-access"},
    )
    try:
        with (
            opener.open(request, timeout=30) as response,
            destination.open("xb") as stream,
        ):
            while block := response.read(1024 * 1024):
                stream.write(block)
                if stream.tell() > MAX_ARCHIVE_BYTES:
                    raise AccessError(
                        "mcp-download-invalid: release archive is oversized"
                    )
    except (OSError, urllib.error.URLError) as error:
        destination.unlink(missing_ok=True)
        raise AccessError(
            "mcp-download-failed: download the pinned GitHub MCP release"
        ) from error


def install(state_root: Path, release: ReleaseArtifact) -> Path:
    archive, binary = _paths(state_root, release)
    if (
        archive.is_file()
        and binary.is_file()
        and _digest(archive) == release.archive_sha256
        and _digest(binary) == release.binary_sha256
    ):
        return binary
    root = _root(state_root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive.unlink(missing_ok=True)
    binary.unlink(missing_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="mcp-", dir=root))
    try:
        candidate = temporary / release.archive_name
        _download(candidate, release)
        if _digest(candidate) != release.archive_sha256:
            raise AccessError(
                "mcp-install-invalid: pinned GitHub MCP archive hash mismatch"
            )
        _extract(candidate, temporary, release)
        shutil.move(str(candidate), archive)
        shutil.move(str(temporary / release.binary_name), binary)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return check(state_root, release)


def check(state_root: Path, release: ReleaseArtifact) -> Path:
    archive, binary = _paths(state_root, release)
    if not archive.is_file() or _digest(archive) != release.archive_sha256:
        raise AccessError(
            "mcp-install-missing: run employer setup to install GitHub MCP"
        )
    if not binary.is_file() or _digest(binary) != release.binary_sha256:
        raise AccessError(
            "mcp-install-missing: run employer setup to install GitHub MCP"
        )
    return binary
