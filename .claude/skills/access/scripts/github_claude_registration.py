from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO, cast

from access_core import AccessError

MAX_PROFILE_BYTES = 16 * 1024 * 1024
MAX_DEPTH = 128
_BUFFER_SIZE = 64 * 1024
_ERROR = "claude-registration-invalid: inspect local Claude registration"


class RegistrationState(Enum):
    ABSENT = "absent"
    CURRENT = "current"
    PREVIOUS_OWNED = "previous_owned"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class Registration:
    command_path: str
    args: tuple[str, ...]
    environment: Mapping[str, str]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.command_path, str)
            or not isinstance(self.args, tuple)
            or not isinstance(self.environment, Mapping)
            or any(not isinstance(argument, str) for argument in self.args)
            or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in self.environment.items()
            )
        ):
            raise ValueError
        object.__setattr__(
            self, "environment", MappingProxyType(dict(self.environment))
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> Registration:
        if set(value) != {"type", "command", "args", "env"}:
            raise ValueError
        command = value["command"]
        arguments = value["args"]
        environment = value["env"]
        if (
            value["type"] != "stdio"
            or not isinstance(command, str)
            or not isinstance(arguments, list)
            or not isinstance(environment, Mapping)
            or any(not isinstance(argument, str) for argument in arguments)
            or any(
                not isinstance(key, str) or not isinstance(item, str)
                for key, item in environment.items()
            )
        ):
            raise ValueError
        return cls(command, tuple(arguments), environment)


class _Reader:
    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._buffer = b""
        self._position = 0
        self._read = 0

    def byte(self) -> int | None:
        if self._position == len(self._buffer):
            self._buffer = self._stream.read(_BUFFER_SIZE)
            self._position = 0
            self._read += len(self._buffer)
            if self._read > MAX_PROFILE_BYTES:
                raise ValueError
            if not self._buffer:
                return None
        value = self._buffer[self._position]
        self._position += 1
        return value

    def required(self) -> int:
        value = self.byte()
        if value is None:
            raise ValueError
        return value

    def whitespace(self) -> int | None:
        value = self.byte()
        while value in (9, 10, 13, 32):
            value = self.byte()
        return value

    def string(self, capture: bool) -> str | None:
        if self.required() != 34:
            raise ValueError
        content = bytearray() if capture else None
        continuations = 0
        lower = 128
        upper = 191
        while True:
            value = self.required()
            if value == 34:
                if continuations:
                    raise ValueError
                break
            if value < 32:
                raise ValueError
            if value == 92:
                if continuations:
                    raise ValueError
                escape = self.required()
                if escape in (34, 47, 92, 98, 102, 110, 114, 116):
                    if content is not None:
                        content.extend((value, escape))
                    continue
                if escape != 117:
                    raise ValueError
                digits = bytes(self.required() for _ in range(4))
                if any(digit not in b"0123456789abcdefABCDEF" for digit in digits):
                    raise ValueError
                if content is not None:
                    content.extend((value, escape))
                    content.extend(digits)
                continue
            if continuations:
                if not lower <= value <= upper:
                    raise ValueError
                continuations -= 1
                lower, upper = 128, 191
            elif value < 128:
                pass
            elif 194 <= value <= 223:
                continuations = 1
            elif value == 224:
                continuations, lower = 2, 160
            elif 225 <= value <= 236 or 238 <= value <= 239:
                continuations = 2
            elif value == 237:
                continuations, upper = 2, 159
            elif value == 240:
                continuations, lower = 3, 144
            elif 241 <= value <= 243:
                continuations = 3
            elif value == 244:
                continuations, upper = 3, 143
            else:
                raise ValueError
            if content is not None:
                content.append(value)
        if content is None:
            return None
        return cast(str, json.loads(b'"' + bytes(content) + b'"'))

    def literal(self, initial: int) -> None:
        expected = {116: b"rue", 102: b"alse", 110: b"ull"}.get(initial)
        if expected is None or bytes(self.required() for _ in expected) != expected:
            raise ValueError

    def number(self, initial: int) -> None:
        value: int | None = initial
        if value == 45:
            value = self.required()
        if value == 48:
            value = self.byte()
        elif value is not None and 49 <= value <= 57:
            value = self.byte()
            while value is not None and 48 <= value <= 57:
                value = self.byte()
        else:
            raise ValueError
        if value == 46:
            value = self.required()
            if not 48 <= value <= 57:
                raise ValueError
            value = self.byte()
            while value is not None and 48 <= value <= 57:
                value = self.byte()
        if value in (69, 101):
            value = self.required()
            if value in (43, 45):
                value = self.required()
            if not 48 <= value <= 57:
                raise ValueError
            value = self.byte()
            while value is not None and 48 <= value <= 57:
                value = self.byte()
        self._unread(value)

    def _unread(self, value: int | None) -> None:
        if value is not None:
            self._position -= 1

    def unread_last(self) -> None:
        if self._position == 0:
            raise ValueError
        self._position -= 1

    def value(self, depth: int = 0) -> None:
        if depth > MAX_DEPTH:
            raise ValueError
        initial = self.whitespace()
        if initial is None:
            raise ValueError
        if initial == 34:
            self._unread(initial)
            self.string(False)
        elif initial == 123:
            self.object(depth + 1)
        elif initial == 91:
            self.array(depth + 1)
        elif initial in (116, 102, 110):
            self.literal(initial)
        elif initial == 45 or 48 <= initial <= 57:
            self.number(initial)
        else:
            raise ValueError

    def object(self, depth: int) -> None:
        initial = self.whitespace()
        if initial == 125:
            return
        self._unread(initial)
        while True:
            self.string(False)
            if self.whitespace() != 58:
                raise ValueError
            self.value(depth)
            delimiter = self.whitespace()
            if delimiter == 125:
                return
            if delimiter != 44:
                raise ValueError

    def array(self, depth: int) -> None:
        initial = self.whitespace()
        if initial == 93:
            return
        self._unread(initial)
        while True:
            self.value(depth)
            delimiter = self.whitespace()
            if delimiter == 93:
                return
            if delimiter != 44:
                raise ValueError

    def object_start(self) -> bool:
        return self.whitespace() == 123

    def array_start(self) -> bool:
        return self.whitespace() == 91

    def key(self) -> str:
        initial = self.whitespace()
        self._unread(initial)
        value = self.string(True)
        if value is None or self.whitespace() != 58:
            raise ValueError
        return value

    def end_or_more(self, closing: int) -> bool:
        delimiter = self.whitespace()
        if delimiter == closing:
            return False
        if delimiter != 44:
            raise ValueError
        return True

    def expected_string(self) -> str | None:
        initial = self.whitespace()
        if initial != 34:
            self._unread(initial)
            self.value()
            return None
        self._unread(initial)
        return self.string(True)


def _normal_path(value: str) -> str:
    return os.path.normcase(str(Path(value).resolve(strict=False)))


def _read_registration(
    reader: _Reader, desired: Registration, previous: Registration | None
) -> RegistrationState:
    if not reader.object_start():
        reader.unread_last()
        reader.value()
        return RegistrationState.CONFLICT
    expected_keys = {"type", "command", "args", "env"}
    values: dict[str, object] = {}
    seen: set[str] = set()
    conflict = False
    more = reader.whitespace() != 125
    if more:
        reader.unread_last()
    while more:
        key = reader.key()
        if key in expected_keys and key in seen:
            raise ValueError
        if key not in expected_keys:
            conflict = True
            reader.value()
        else:
            seen.add(key)
            if key in ("type", "command"):
                value = reader.expected_string()
                if value is None:
                    conflict = True
                else:
                    values[key] = value
            elif key == "args":
                values[key] = _read_args(reader)
            else:
                parsed_environment, invalid = _read_environment(
                    reader, desired, previous
                )
                values[key] = parsed_environment
                conflict = conflict or invalid
        more = reader.end_or_more(125)
    if set(values) != expected_keys or conflict:
        return RegistrationState.CONFLICT
    value_type = values["type"]
    command = values["command"]
    arguments = values["args"]
    environment_value = values["env"]
    if (
        value_type != "stdio"
        or not isinstance(command, str)
        or not isinstance(arguments, tuple)
        or not isinstance(environment_value, Mapping)
    ):
        return RegistrationState.CONFLICT
    actual = Registration(
        command,
        arguments,
        MappingProxyType(dict(cast(Mapping[str, str], environment_value))),
    )
    if actual == desired:
        return RegistrationState.CURRENT
    if previous is not None and actual == previous:
        return RegistrationState.PREVIOUS_OWNED
    return RegistrationState.CONFLICT


def _read_args(reader: _Reader) -> tuple[str, ...] | None:
    if not reader.array_start():
        reader.unread_last()
        reader.value()
        return None
    values: list[str] = []
    invalid = False
    more = reader.whitespace() != 93
    if more:
        reader.unread_last()
    while more:
        value = reader.expected_string()
        if value is None:
            invalid = True
        else:
            values.append(value)
        more = reader.end_or_more(93)
    return None if invalid else tuple(values)


def _read_environment(
    reader: _Reader, desired: Registration, previous: Registration | None
) -> tuple[dict[str, str] | None, bool]:
    if not reader.object_start():
        reader.unread_last()
        reader.value()
        return None, True
    allowed = set(desired.environment)
    if previous is not None:
        allowed.update(previous.environment)
    values: dict[str, str] = {}
    seen: set[str] = set()
    invalid = False
    more = reader.whitespace() != 125
    if more:
        reader.unread_last()
    while more:
        key = reader.key()
        if key in allowed and key in seen:
            raise ValueError
        if key not in allowed:
            invalid = True
            reader.value()
        else:
            seen.add(key)
            value = reader.expected_string()
            if value is None:
                invalid = True
            else:
                values[key] = value
        more = reader.end_or_more(125)
    return values, invalid


def _read_servers(
    reader: _Reader, desired: Registration, previous: Registration | None
) -> RegistrationState:
    if not reader.object_start():
        raise ValueError
    result = RegistrationState.ABSENT
    more = reader.whitespace() != 125
    if more:
        reader.unread_last()
    while more:
        key = reader.key()
        if key == "employer_github":
            if result is not RegistrationState.ABSENT:
                raise ValueError
            result = _read_registration(reader, desired, previous)
        else:
            reader.value()
        more = reader.end_or_more(125)
    return result


def _read_project(
    reader: _Reader, desired: Registration, previous: Registration | None
) -> RegistrationState:
    if not reader.object_start():
        raise ValueError
    result = RegistrationState.ABSENT
    found = False
    more = reader.whitespace() != 125
    if more:
        reader.unread_last()
    while more:
        key = reader.key()
        if key == "mcpServers":
            if found:
                raise ValueError
            found = True
            result = _read_servers(reader, desired, previous)
        else:
            reader.value()
        more = reader.end_or_more(125)
    return result


def _read_projects(
    reader: _Reader, root: Path, desired: Registration, previous: Registration | None
) -> RegistrationState:
    if not reader.object_start():
        raise ValueError
    target = _normal_path(str(root))
    result = RegistrationState.ABSENT
    matched = False
    more = reader.whitespace() != 125
    if more:
        reader.unread_last()
    while more:
        key = reader.key()
        if _normal_path(key) == target:
            if matched:
                raise ValueError
            matched = True
            result = _read_project(reader, desired, previous)
        else:
            reader.value()
        more = reader.end_or_more(125)
    return result


def _inspect(
    reader: _Reader, root: Path, desired: Registration, previous: Registration | None
) -> RegistrationState:
    if reader.whitespace() != 123:
        raise ValueError
    result = RegistrationState.ABSENT
    found = False
    more = reader.whitespace() != 125
    if more:
        reader.unread_last()
    while more:
        key = reader.key()
        if key == "projects":
            if found:
                raise ValueError
            found = True
            result = _read_projects(reader, root, desired, previous)
        else:
            reader.value()
        more = reader.end_or_more(125)
    if reader.whitespace() is not None:
        raise ValueError
    return result


def _check_profile(path: Path) -> None:
    absolute = path.expanduser().absolute()
    for candidate in (absolute, *absolute.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise ValueError
    info = absolute.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError


def registration_state(
    profile: Path,
    root: Path,
    desired_public_registration: Registration | Mapping[str, object],
    previous_public_registration: Registration | Mapping[str, object] | None = None,
) -> RegistrationState:
    try:
        desired = (
            desired_public_registration
            if isinstance(desired_public_registration, Registration)
            else Registration.from_mapping(desired_public_registration)
        )
        previous = (
            previous_public_registration
            if isinstance(previous_public_registration, Registration)
            or previous_public_registration is None
            else Registration.from_mapping(previous_public_registration)
        )
        _check_profile(profile)
        with profile.open("rb", buffering=_BUFFER_SIZE) as stream:
            return _inspect(
                _Reader(stream),
                root.expanduser().resolve(strict=False),
                desired,
                previous,
            )
    except FileNotFoundError:
        return RegistrationState.ABSENT
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise AccessError(_ERROR) from error
