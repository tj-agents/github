"""Public state for one owner-started enrollment attempt."""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from credentials.model import NAME, CredentialError


@dataclass(frozen=True, slots=True)
class OwnerReceipt:
    path: Path
    operation: str
    name: str
    started_at: str

    @classmethod
    def start(cls, path: Path, operation: str, name: str) -> OwnerReceipt:
        if not NAME.fullmatch(name):
            raise CredentialError("credential-name-invalid: select a declared name")
        receipt = cls(path, operation, name, datetime.now(UTC).isoformat())
        receipt.write("running", None, {})
        return receipt

    def write(self, state: str, exit_code: int | None, fields: dict[str, str]) -> None:
        public = {
            "schema_version": 1,
            "credential": self.name,
            "operation": self.operation,
            "pid": os.getpid(),
            "started_at": self.started_at,
            "state": state,
            "observed_at": datetime.now(UTC).isoformat(),
            "exit_code": exit_code,
            **fields,
        }
        descriptor, filename = tempfile.mkstemp(dir=self.path.parent)
        temporary = Path(filename)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as staged:
                json.dump(public, staged)
            if state == "running":
                # Claim only an unused name, with a complete first snapshot.
                try:
                    os.link(temporary, self.path)
                except FileExistsError as error:
                    raise CredentialError(
                        "owner-attempt-exists: inspect the original receipt and process"
                    ) from error
            else:
                temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def failed(self, message: str) -> None:
        code = message.partition(":")[0]
        if not re.fullmatch(r"[a-z]+(?:-[a-z]+)+", code):
            code = "credential-operation-failed"
        # The outer command still reports the failure if its receipt is unwritable.
        with contextlib.suppress(OSError):
            self.write("failed", 1, {"reason": code})
