import os
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Protocol

import github_posix
import github_windows
from access_core import AccessError


class SupportedPlatform(Enum):
    WINDOWS = "nt"
    POSIX = "posix"


class Launcher(Enum):
    GIT = ("git", "native-git")
    GH = ("gh", "gh")

    def __init__(self, filename: str, operation: str) -> None:
        self.filename = filename
        self.operation = operation


class PlatformProtocol(Protocol):
    def real_git(self) -> Path: ...

    def real_gh(self) -> Path: ...

    def launcher_path(self, launchers: Path, launcher: Launcher) -> Path: ...

    def launcher_contents(
        self, script: Path, binding: Path, launcher: Launcher
    ) -> bytes: ...

    def write_launcher(self, path: Path, contents: bytes) -> None: ...


PLATFORMS: Mapping[SupportedPlatform, PlatformProtocol] = {
    SupportedPlatform.WINDOWS: github_windows,
    SupportedPlatform.POSIX: github_posix,
}


def current_platform() -> PlatformProtocol:
    try:
        supported = SupportedPlatform(os.name)
    except ValueError as error:
        raise AccessError(
            "platform-unsupported: GitHub access requires Windows or POSIX"
        ) from error
    return PLATFORMS[supported]
