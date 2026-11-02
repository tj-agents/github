from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

from .records import ItemError


class PosixLock(Protocol):
    LOCK_EX: int
    LOCK_NB: int
    LOCK_UN: int

    def flock(self, fd: int, operation: int) -> None: ...


def _posix_lock() -> PosixLock:
    return cast(PosixLock, import_module("fcntl"))


class RequestJournal:
    def __init__(self, root: Path, namespace: str = "work-items") -> None:
        if not isinstance(namespace, str) or not re.fullmatch(
            r"[a-z][a-z0-9-]{0,63}", namespace
        ):
            raise ItemError(
                "request receipt namespace must be a safe single path segment"
            )
        root = Path(root).resolve()
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--git-common-dir"],
                capture_output=True,
                check=False,
                encoding="utf-8",
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ItemError("cannot locate work-item request receipts") from error
        if result.returncode or not result.stdout.strip():
            raise ItemError("work-item receipts require a Git source checkout")
        common = Path(result.stdout.strip())
        self.root = (
            (common if common.is_absolute() else root / common).resolve()
            / namespace
            / "requests"
        )

    def path(self, key: str) -> Path:
        return self.root / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    def read(self, key: str, fingerprint: str) -> dict[str, int | str | None] | None:
        path = self.path(key)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ItemError(
                "request receipt is unreadable; reconcile before submission"
            ) from error
        if (
            not isinstance(value, dict)
            or set(value) != {"fingerprint", "resource_id"}
            or value.get("fingerprint") != fingerprint
            or (
                value.get("resource_id") is not None
                and (
                    not isinstance(value["resource_id"], int)
                    or isinstance(value["resource_id"], bool)
                    or value["resource_id"] <= 0
                )
            )
        ):
            raise ItemError(
                "request receipt differs or is invalid; reconcile before submission"
            )
        return value

    def reserve(self, key: str, fingerprint: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path(key)
        try:
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    json.dumps(
                        {"fingerprint": fingerprint, "resource_id": None},
                        separators=(",", ":"),
                    )
                )
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as error:
            raise ItemError(
                "request is already reserved; reconcile without another POST"
            ) from error

    def complete(self, key: str, fingerprint: str, resource_id: int) -> None:
        if (
            not isinstance(resource_id, int)
            or isinstance(resource_id, bool)
            or resource_id <= 0
        ):
            raise ItemError(
                "request response has no durable identity; reconcile without reposting"
            )
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path(key)
        temporary = path.with_suffix(".tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    json.dumps(
                        {"fingerprint": fingerprint, "resource_id": resource_id},
                        separators=(",", ":"),
                    )
                )
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise ItemError(
                "cannot persist request receipt; reconcile before another submission"
            ) from error

    @contextmanager
    def writer(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / ".writer.lock"
        with path.open("a+b") as handle:
            if handle.seek(0, 2) == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl = _posix_lock()
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise ItemError(
                    "another work-item writer is running; retry after it finishes"
                ) from error
            try:
                yield
            finally:
                handle.seek(0)
                if sys.platform == "win32":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl = _posix_lock()
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
