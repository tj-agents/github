"""Files only the current user can read: App keys and installation tokens."""

import os
import stat
from pathlib import Path

from gate_platform import Platform, PrivateStateError

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def is_redirected(path: Path) -> bool:
    try:
        attributes = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(attributes.st_mode) or bool(
        getattr(attributes, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
    )


def private_directory(path: Path, platform: Platform) -> Path:
    """Create `path` and its parents, refusing a link, and make it private."""
    if is_redirected(path):
        raise PrivateStateError(f"{path} is a link; refusing private state")
    path.mkdir(parents=True, exist_ok=True)
    if is_redirected(path) or not path.is_dir():
        raise PrivateStateError(f"{path} is not a private directory")
    platform.protect(path, directory=True)
    return path


def write_private(path: Path, content: bytes, platform: Platform) -> None:
    """Atomically replace `path` with private `content`."""
    private_directory(path.parent, platform)
    if is_redirected(path):
        raise PrivateStateError(f"{path} is a link; refusing private state")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            platform.protect(temporary, directory=False)
            output.write(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_private(path: Path, platform: Platform) -> bytes:
    if is_redirected(path) or not path.is_file():
        raise PrivateStateError(f"{path} is missing or redirected")
    platform.verify_private(path, directory=False)
    return path.read_bytes()
