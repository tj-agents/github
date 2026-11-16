from collections.abc import Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Protocol

from gate_error import GateError

MARKER = "tj-agents github access gate"


class PrivateStateError(GateError):
    pass


class SupportedPlatform(Enum):
    WINDOWS = "nt"
    POSIX = "posix"


class Platform(Protocol):
    def default_bin_directory(
        self, environment: Mapping[str, str], home: Path
    ) -> Path: ...

    def launcher_name(self) -> str: ...

    def launcher_bytes(self, python: Path, script: Path) -> bytes: ...

    def write_launcher(self, path: Path, content: bytes) -> None: ...

    def protect(self, path: Path, *, directory: bool) -> None: ...

    def verify_private(self, path: Path, *, directory: bool) -> None: ...

    def execute(
        self,
        executable: Path,
        arguments: Sequence[str],
        environment: Mapping[str, str] | None,
    ) -> int: ...
