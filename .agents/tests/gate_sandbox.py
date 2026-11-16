"""Isolated machines and a fake GitHub for the access gate's tests."""

import base64
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from gate_app import GitHubError
from gate_platform import Platform, SupportedPlatform
from gate_private import write_private
from github_gate import PLATFORMS

GATE_SOURCE = Path(__file__).parents[1] / "github/runtime/gate"
AGENT_MARKERS = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")
INHERITED = (
    *AGENT_MARKERS,
    "TJ_AGENT",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GH_CONFIG_DIR",
    "GH_HOST",
    "GH_REPO",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_SYSTEM",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_ASKPASS",
    "SSH_ASKPASS",
    "XDG_CONFIG_HOME",
)
CURRENT_PLATFORM: Platform = PLATFORMS[SupportedPlatform(os.name)]
SLUG = "sample-bot"
COVERED = "sample-org"
INSTALLATION_ID = 7
FAR_FUTURE = 4_102_444_800
NOW = 1_800_000_000.0
CACHED_TOKEN = "ghs_" + "c" * 36
OWNER_DEFAULT = "/.github/contents/.agents/github-access.json"


def tool_directories() -> list[str]:
    git = shutil.which("git")
    assert git is not None, "the tests need Git"
    directories = [Path(git).parent]
    for root in Path(git).resolve().parents[1:3]:
        if (root / "usr/bin/sh.exe").is_file():
            directories.append(root / "usr/bin")
    directories.append(Path(sys.executable).parent)
    return list(dict.fromkeys(str(directory) for directory in directories))


@dataclass(frozen=True, slots=True)
class Sandbox:
    """A machine whose home, state, bin and Git configuration live under `root`."""

    root: Path

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def gate_home(self) -> Path:
        return self.state / "github-gate"

    @property
    def bin(self) -> Path:
        return self.root / "bin"

    @property
    def global_config(self) -> Path:
        return self.home / ".gitconfig"

    @property
    def neutral(self) -> Path:
        return self.root / "neutral"

    def environment(self, **extra: str) -> dict[str, str]:
        return {
            "PATH": os.pathsep.join([str(self.bin), *tool_directories()]),
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "LOCALAPPDATA": str(self.root / "local"),
            "TJ_BIN_DIR": str(self.bin),
            "AGENT_STATE_DIRECTORY": str(self.state),
            "GIT_CONFIG_GLOBAL": str(self.global_config),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "Sample Human",
            "GIT_AUTHOR_EMAIL": "human@example.test",
            "GIT_COMMITTER_NAME": "Sample Human",
            "GIT_COMMITTER_EMAIL": "human@example.test",
            **extra,
        }

    def git(self, cwd: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            env=self.environment(),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def repository(
        self,
        name: str,
        declaration: Mapping[str, object] | None = None,
        origin: str | None = f"https://github.com/{COVERED}/core.git",
    ) -> Path:
        path = self.root / name
        self.git(self.root, "init", "-q", "-b", "main", str(path))
        if declaration is not None:
            target = path / ".agents/github-access.json"
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(declaration), encoding="utf-8")
            self.git(path, "add", ".agents/github-access.json")
        self.git(path, "commit", "-q", "--allow-empty", "-m", "initial")
        if origin is not None:
            self.git(path, "remote", "add", "origin", origin)
        return path

    def enrol_cached(self, slug: str = SLUG) -> Path:
        """Enrol an App whose covered account already holds a far-future token."""
        directory = self.gate_home / "apps" / slug
        descriptor = {
            "slug": slug,
            "id": 101,
            "client_id": "Iv1.sample",
            "owner": {"login": COVERED},
        }
        write_private(directory / "key.pem", b"not a key", CURRENT_PLATFORM)
        write_private(
            directory / "app.json", json.dumps(descriptor).encode(), CURRENT_PLATFORM
        )
        write_private(
            directory / "installations.json",
            json.dumps({COVERED: INSTALLATION_ID}).encode(),
            CURRENT_PLATFORM,
        )
        write_private(
            directory / "tokens" / f"{COVERED}.json",
            json.dumps({"token": CACHED_TOKEN, "expires": FAR_FUTURE}).encode(),
            CURRENT_PLATFORM,
        )
        return directory


@dataclass
class FakeGitHub:
    """GitHub's App endpoints for one App installed on `installed` accounts.

    `owner_defaults` holds each owner's `.github/.agents/github-access.json`; an owner
    without one answers 404, and `owner_failure` makes every such read fail with it.
    """

    slug: str = SLUG
    installed: Mapping[str, int] = field(
        default_factory=lambda: {COVERED: INSTALLATION_ID}
    )
    app_id: int = 101
    signed_as: int = 101
    owner_defaults: dict[str, str] = field(default_factory=dict)
    owner_failure: GitHubError | None = None
    calls: list[tuple[str, str, str | None]] = field(default_factory=list)
    issued: int = 0

    def owner_reads(self) -> list[tuple[str, str | None]]:
        return [
            (path.split("/")[2], bearer)
            for _, path, bearer in self.calls
            if path.endswith(OWNER_DEFAULT)
        ]

    def __call__(self, path: str, bearer: str | None, method: str) -> object:
        self.calls.append((method, path, bearer))
        if path == f"/apps/{self.slug}":
            return {
                "slug": self.slug,
                "id": self.app_id,
                "client_id": "Iv1.sample",
                "owner": {"login": COVERED, "type": "Organization"},
            }
        if path == "/app":
            return {"slug": self.slug, "id": self.signed_as}
        if path.startswith("/app/installations?"):
            return [
                {"id": number, "account": {"login": login}}
                for login, number in self.installed.items()
            ]
        if path.endswith("/access_tokens") and method == "POST":
            self.issued += 1
            return {
                "token": f"ghs_issued{self.issued:032d}",
                "expires_at": "2027-01-15T09:00:00Z",
            }
        if path.endswith(OWNER_DEFAULT) and method == "GET":
            owner = path.split("/")[2].lower()
            if self.owner_failure is not None:
                raise self.owner_failure
            if owner not in self.owner_defaults:
                raise GitHubError(f"GitHub GET {path} failed (HTTP 404)", 404)
            content = base64.encodebytes(self.owner_defaults[owner].encode())
            return {"type": "file", "encoding": "base64", "content": content.decode()}
        raise AssertionError(f"unexpected GitHub call {method} {path}")


def fake_sign(key: Path, message: bytes) -> bytes:
    return b"signature:" + key.name.encode()


DUMPER = """\
import json, os, sys
from pathlib import Path

Path(os.environ["GATE_TEST_DUMP"]).write_text(
    json.dumps({"argv": sys.argv[1:], "env": dict(os.environ)}), encoding="utf-8"
)
raise SystemExit(int(os.environ.get("GATE_TEST_STATUS", "0")))
"""


def fake_gh(directory: Path, script: str = DUMPER) -> Path:
    """An executable named gh that runs `script`; it carries no gate marker."""
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "fake_gh.py"
    source.write_text(script, encoding="utf-8")
    python = Path(sys.executable).resolve()
    if os.name == "nt":
        import io
        import zipfile

        from gate_windows import launcher_stub

        main = f"import runpy\nrunpy.run_path({str(source)!r}, run_name='__main__')\n"
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as payload:
            payload.writestr("__main__.py", main)
        target = directory / "gh.exe"
        target.write_bytes(
            launcher_stub() + f'#!"{python}" -B\n'.encode() + archive.getvalue()
        )
        return target
    target = directory / "gh"
    target.write_text(
        f'#!/bin/sh\nexec "{python}" -B "{source}" "$@"\n', encoding="utf-8"
    )
    target.chmod(0o755)
    return target


@dataclass
class RecordingPlatform:
    """The current platform, except that it records programs instead of running them."""

    calls: list[tuple[Path, tuple[str, ...], dict[str, str] | None]] = field(
        default_factory=list
    )

    @staticmethod
    def default_bin_directory(environment: Mapping[str, str], home: Path) -> Path:
        return CURRENT_PLATFORM.default_bin_directory(environment, home)

    @staticmethod
    def launcher_name() -> str:
        return CURRENT_PLATFORM.launcher_name()

    @staticmethod
    def launcher_bytes(python: Path, script: Path) -> bytes:
        return CURRENT_PLATFORM.launcher_bytes(python, script)

    @staticmethod
    def write_launcher(path: Path, content: bytes) -> None:
        CURRENT_PLATFORM.write_launcher(path, content)

    @staticmethod
    def approve_launcher_name() -> str:
        return CURRENT_PLATFORM.approve_launcher_name()

    @staticmethod
    def approve_launcher_bytes(python: Path, script: Path) -> bytes:
        return CURRENT_PLATFORM.approve_launcher_bytes(python, script)

    @staticmethod
    def is_terminal(descriptor: int) -> bool:
        return CURRENT_PLATFORM.is_terminal(descriptor)

    @staticmethod
    def protect(path: Path, *, directory: bool) -> None:
        CURRENT_PLATFORM.protect(path, directory=directory)

    @staticmethod
    def verify_private(path: Path, *, directory: bool) -> None:
        CURRENT_PLATFORM.verify_private(path, directory=directory)

    def execute(
        self,
        executable: Path,
        arguments: Sequence[str],
        environment: Mapping[str, str] | None,
    ) -> int:
        recorded = None if environment is None else dict(environment)
        self.calls.append((executable, tuple(arguments), recorded))
        return 0
