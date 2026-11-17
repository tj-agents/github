from __future__ import annotations

import json
import queue
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import IO

from credentials.model import Client, CredentialError
from credentials.service import Service

LIMIT = 1024 * 1024
HANDSHAKE_ID = "tj-credentials-handshake"
type Resolve = Callable[[Client, str], Service | None]


@dataclass(frozen=True, slots=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: object) -> Version:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}", value) is None
        ):
            raise ValueError("invalid protocol version")
        major, minor, patch = (int(part) for part in value.split("."))
        return cls(major, minor, patch)


@dataclass(slots=True)
class Pending:
    request: Mapping[str, object]
    result: queue.Queue[dict[str, object]] = field(default_factory=queue.Queue)


def authentication(
    payload: Mapping[str, object], resolve: Resolve, now: datetime
) -> dict[str, object]:
    uri = payload.get("Uri")
    if not isinstance(uri, str):
        raise CredentialError(
            "credential-request-invalid: NuGet must name its source URI"
        )
    service = resolve(Client.NUGET, uri)
    if service is None:
        return {
            "ResponseCode": "NotFound",
            "Username": None,
            "Password": None,
            "Message": "No declared credential for this source",
            "AuthenticationTypes": [],
        }
    service.check(now)
    secret = service.secret(now)
    return {
        "ResponseCode": "Success",
        "Username": service.credential.login,
        "Password": secret.value,
        "Message": None,
        "AuthenticationTypes": ["basic"],
    }


def _complete(
    pending: Pending, payload: Mapping[str, object], resolve: Resolve, now: datetime
) -> None:
    try:
        result = authentication(payload, resolve, now)
    except CredentialError as error:
        result = {
            "ResponseCode": "Error",
            "Username": None,
            "Password": None,
            "Message": str(error),
            "AuthenticationTypes": [],
        }
    except Exception:  # noqa: BLE001 - no private backend diagnostics enter the protocol
        result = {
            "ResponseCode": "Error",
            "Username": None,
            "Password": None,
            "Message": "Credential backend failed; run tj-credentials check",
            "AuthenticationTypes": [],
        }
    pending.result.put(result)


def _frames(stream: IO[str], frames: queue.Queue[str | None]) -> None:
    try:
        while line := stream.readline(LIMIT + 1):
            frames.put(line)
    except (OSError, UnicodeError):
        pass
    finally:
        frames.put(None)


def _write(
    stream: IO[str],
    frame: Mapping[str, object],
    payload: object,
    kind: str = "Response",
) -> None:
    response = {
        "RequestId": frame["RequestId"],
        "Type": kind,
        "Method": frame["Method"],
        "Payload": payload,
    }
    stream.write(json.dumps(response, separators=(",", ":")) + "\n")
    stream.flush()


def serve(
    input_stream: IO[str],
    output_stream: IO[str],
    resolve: Resolve,
    now: datetime,
    *,
    declared: bool,
) -> int:
    frames: queue.Queue[str | None] = queue.Queue(maxsize=32)
    threading.Thread(target=_frames, args=(input_stream, frames), daemon=True).start()
    pending: dict[str, Pending] = {}
    handshaken = False
    peer_accepted = False
    initialized = False
    _write(
        output_stream,
        {"RequestId": HANDSHAKE_ID, "Method": "Handshake"},
        {"ProtocolVersion": "2.0.0", "MinimumProtocolVersion": "2.0.0"},
        "Request",
    )
    while True:
        for identifier, operation in tuple(pending.items()):
            try:
                result = operation.result.get_nowait()
            except queue.Empty:
                _write(output_stream, operation.request, None, "Progress")
            else:
                _write(output_stream, operation.request, result)
                del pending[identifier]
        try:
            line = frames.get(timeout=0.5)
        except queue.Empty:
            continue
        if line is None:
            return 0
        if len(line) > LIMIT or not line.endswith("\n"):
            return 1
        try:
            frame = json.loads(line)
        except ValueError:
            return 1
        if (
            not isinstance(frame, dict)
            or not isinstance(frame.get("RequestId"), str)
            or not isinstance(frame.get("Method"), str)
        ):
            return 1
        identifier, method = frame["RequestId"], frame["Method"]
        if (
            frame.get("Type") == "Response"
            and identifier == HANDSHAKE_ID
            and method == "Handshake"
        ):
            response = frame.get("Payload")
            if (
                not isinstance(response, dict)
                or response.get("ResponseCode") != "Success"
                or response.get("ProtocolVersion") != "2.0.0"
            ):
                return 1
            peer_accepted = True
            continue
        if frame.get("Type") == "Cancel":
            pending.pop(identifier, None)
            continue
        if frame.get("Type") != "Request":
            return 1
        payload = frame.get("Payload") or {}
        if not isinstance(payload, dict):
            return 1
        if method == "Close":
            return 0
        if method == "Handshake" and not handshaken:
            try:
                minimum = Version.parse(payload.get("MinimumProtocolVersion"))
                maximum = Version.parse(payload.get("ProtocolVersion"))
                compatible = minimum <= Version(2, 0, 0) <= maximum
            except ValueError:
                compatible = False
            if not compatible:
                _write(
                    output_stream,
                    frame,
                    {"ResponseCode": "Error", "ProtocolVersion": None},
                )
                return 1
            handshaken = True
            _write(
                output_stream,
                frame,
                {"ResponseCode": "Success", "ProtocolVersion": "2.0.0"},
            )
        elif (
            method == "Initialize" and handshaken and peer_accepted and not initialized
        ):
            initialized = True
            _write(output_stream, frame, {"ResponseCode": "Success"})
        elif not initialized:
            _write(output_stream, frame, {"ResponseCode": "Error"})
        elif method == "GetOperationClaims":
            source = payload.get("PackageSourceRepository")
            supported = (
                declared
                if source is None
                else isinstance(source, str)
                and resolve(Client.NUGET, source) is not None
            )
            _write(
                output_stream,
                frame,
                {"Claims": ["Authentication"] if supported else []},
            )
        elif (
            method == "GetAuthenticationCredentials"
            and identifier not in pending
            and len(pending) < 8
        ):
            operation = Pending(frame)
            pending[identifier] = operation
            threading.Thread(
                target=_complete, args=(operation, payload, resolve, now), daemon=True
            ).start()
        elif method in {"SetLogLevel", "MonitorNuGetProcessExit", "SetCredentials"}:
            _write(output_stream, frame, {"ResponseCode": "Success"})
        else:
            _write(output_stream, frame, {"ResponseCode": "Error"})
