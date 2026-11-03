from __future__ import annotations

import contextlib
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import IO

import access_core
import github_binding
import github_mcp_install
import github_platform

FRAME_LIMIT = 1024 * 1024
DEADLINE_SECONDS = 30.0
PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = frozenset({"2025-03-26", "2025-06-18", "2025-11-25"})
_TOOLS_PATH = Path(__file__).with_name("github_mcp_schemas.json")


class MCPError(access_core.AccessError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _load_tools() -> tuple[dict[str, object], ...]:
    try:
        value = json.loads(_TOOLS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError("GitHub MCP schema asset is unavailable") from error
    tools = value.get("tools") if isinstance(value, dict) else None
    if not isinstance(tools, list) or {
        item.get("name") for item in tools if isinstance(item, dict)
    } != {
        "get_file_contents",
        "list_branches",
        "pull_request_read",
    }:
        raise RuntimeError("GitHub MCP schema asset is invalid")
    return tuple(item for item in tools if isinstance(item, dict))


TOOLS = _load_tools()
TOOLS_BY_NAME = {str(tool["name"]): tool for tool in TOOLS}


def _error(identifier: int | str | None, code: int, message: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": identifier,
        "error": {"code": code, "message": message},
    }


def _result(identifier: int | str, result: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def _write(stream: IO[str], value: Mapping[str, object]) -> None:
    stream.write(json.dumps(value, separators=(",", ":"), ensure_ascii=False) + "\n")
    stream.flush()


def _read_frame(stream: IO[str]) -> dict[str, object] | None:
    try:
        frame = stream.readline(FRAME_LIMIT + 1)
    except (OSError, UnicodeError) as error:
        raise MCPError(-32700, "invalid JSON-RPC JSON") from error
    if not frame:
        return None
    if len(frame) > FRAME_LIMIT or not frame.endswith("\n"):
        raise MCPError(-32700, "invalid JSON-RPC frame")
    try:
        value = json.loads(
            frame, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())
        )
    except (RecursionError, TypeError, ValueError) as error:
        raise MCPError(-32700, "invalid JSON-RPC JSON") from error
    if not isinstance(value, dict):
        raise MCPError(-32600, "invalid JSON-RPC request")
    return value


def _identifier(value: object) -> int | str:
    if type(value) is int or isinstance(value, str):
        return value
    raise MCPError(-32600, "invalid JSON-RPC id")


def _request(value: dict[str, object]) -> tuple[int | str, str, dict[str, object]]:
    if (
        set(value)
        not in ({"jsonrpc", "id", "method"}, {"jsonrpc", "id", "method", "params"})
        or value.get("jsonrpc") != "2.0"
    ):
        raise MCPError(-32600, "invalid JSON-RPC request")
    identifier = _identifier(value.get("id"))
    method = value.get("method")
    params = value.get("params", {})
    if not isinstance(method, str) or not isinstance(params, dict):
        raise MCPError(-32600, "invalid JSON-RPC request")
    return identifier, method, params


def _initialized(value: dict[str, object]) -> bool:
    return (
        set(value) in ({"jsonrpc", "method"}, {"jsonrpc", "method", "params"})
        and value.get("jsonrpc") == "2.0"
        and value.get("method") == "notifications/initialized"
        and value.get("params", {}) == {}
    )


def _number(
    value: object, minimum: float | None = None, maximum: float | None = None
) -> float:
    if type(value) is not int and type(value) is not float:
        raise MCPError(-32602, "tool arguments are invalid")
    try:
        number = float(value)
    except OverflowError as error:
        raise MCPError(-32602, "tool arguments are invalid") from error
    if not math.isfinite(number):
        raise MCPError(-32602, "tool arguments are invalid")
    if (minimum is not None and number < minimum) or (
        maximum is not None and number > maximum
    ):
        raise MCPError(-32602, "tool arguments are invalid")
    return number


def _metadata(params: dict[str, object], message: str) -> None:
    metadata = params.get("_meta")
    if metadata is None:
        return
    if not isinstance(metadata, dict):
        raise MCPError(-32602, f"{message} request is invalid")
    if "progressToken" not in metadata:
        return
    progress_token = metadata["progressToken"]
    if isinstance(progress_token, str):
        return
    try:
        _number(progress_token)
    except MCPError as error:
        raise MCPError(-32602, f"{message} request is invalid") from error


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise MCPError(-32602, "tool arguments are invalid")
    return value


def _allowed_repository(
    profile: access_core.Profile, arguments: dict[str, object]
) -> None:
    owner = _string(arguments.get("owner"))
    repository = _string(arguments.get("repo"))
    if (
        owner.casefold() != profile.organization.casefold()
        or repository.casefold()
        not in {item.casefold() for item in profile.repositories}
    ):
        raise MCPError(-32602, "tool repository is not allowlisted")


def _validate_arguments(
    profile: access_core.Profile, name: str, arguments: object
) -> dict[str, object]:
    if not isinstance(arguments, dict):
        raise MCPError(-32602, "tool arguments are invalid")
    fields = {
        "get_file_contents": {"owner", "repo", "path", "ref", "sha", "fields"},
        "list_branches": {"owner", "repo", "page", "perPage"},
        "pull_request_read": {
            "method",
            "owner",
            "repo",
            "pullNumber",
            "page",
            "perPage",
            "after",
        },
    }[name]
    required = {
        "get_file_contents": {"owner", "repo"},
        "list_branches": {"owner", "repo"},
        "pull_request_read": {"method", "owner", "repo", "pullNumber"},
    }[name]
    if set(arguments) - fields or not required <= set(arguments):
        raise MCPError(-32602, "tool arguments are invalid")
    _allowed_repository(profile, arguments)
    for field in {"path", "ref", "sha", "after"} & set(arguments):
        _string(arguments[field])
    for field in {"page", "perPage"} & set(arguments):
        _number(arguments[field], 1, 100 if field == "perPage" else None)
    if "fields" in arguments:
        fields_value = arguments["fields"]
        allowed = {
            "type",
            "name",
            "path",
            "size",
            "sha",
            "url",
            "git_url",
            "html_url",
            "download_url",
        }
        if not isinstance(fields_value, list) or any(
            not isinstance(item, str) or item not in allowed for item in fields_value
        ):
            raise MCPError(-32602, "tool arguments are invalid")
    if name == "pull_request_read":
        _number(arguments["pullNumber"])
        method = arguments["method"]
        if not isinstance(method, str) or method not in {
            "get",
            "get_diff",
            "get_status",
            "get_files",
            "get_commits",
            "get_review_comments",
            "get_reviews",
            "get_comments",
            "get_check_runs",
        }:
            raise MCPError(-32602, "tool arguments are invalid")
    return arguments


def _same_identifier(left: object, right: int | str) -> bool:
    return type(left) is type(right) and left == right


def _upstream_response(value: dict[str, object], identifier: int | str) -> None:
    fields = set(value)
    if (
        value.get("jsonrpc") != "2.0"
        or not _same_identifier(value.get("id"), identifier)
        or fields not in ({"jsonrpc", "id", "result"}, {"jsonrpc", "id", "error"})
    ):
        raise MCPError(-32000, "upstream GitHub MCP returned an invalid response")
    if "error" in value:
        error = value["error"]
        if (
            not isinstance(error, dict)
            or set(error) - {"code", "message", "data"}
            or type(error.get("code")) is not int
            or not isinstance(error.get("message"), str)
        ):
            raise MCPError(-32000, "upstream GitHub MCP returned an invalid response")


def _tool_call(
    profile: access_core.Profile, params: dict[str, object]
) -> tuple[str, dict[str, object]]:
    if (
        set(params) - {"name", "arguments", "_meta"}
        or not {"name", "arguments"} <= set(params)
        or not isinstance(params.get("name"), str)
    ):
        raise MCPError(-32602, "tool call is invalid")
    _metadata(params, "tool call")
    name = params["name"]
    if name not in TOOLS_BY_NAME:
        raise MCPError(-32602, "tool is not available")
    return name, _validate_arguments(profile, name, params["arguments"])


def _child_environment(token: str) -> dict[str, str]:
    retained = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP"}
    environment = {
        key: value for key, value in os.environ.items() if key.upper() in retained
    }
    environment["GITHUB_PERSONAL_ACCESS_TOKEN"] = token
    return environment


def _child_frame(process: subprocess.Popen[str], deadline: float) -> dict[str, object]:
    assert process.stdout is not None
    output = process.stdout
    frames: queue.Queue[str | None] = queue.Queue(maxsize=1)

    def read() -> None:
        try:
            frames.put(output.readline(FRAME_LIMIT + 1))
        except (OSError, ValueError):
            frames.put(None)

    threading.Thread(target=read, daemon=True).start()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise MCPError(-32000, "upstream GitHub MCP timed out")
    try:
        frame = frames.get(timeout=remaining)
    except queue.Empty as error:
        raise MCPError(-32000, "upstream GitHub MCP timed out") from error
    if not frame or len(frame) > FRAME_LIMIT or not frame.endswith("\n"):
        raise MCPError(-32000, "upstream GitHub MCP returned an invalid response")
    try:
        value = json.loads(
            frame, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())
        )
    except ValueError as error:
        raise MCPError(
            -32000, "upstream GitHub MCP returned an invalid response"
        ) from error
    if not isinstance(value, dict):
        raise MCPError(-32000, "upstream GitHub MCP returned an invalid response")
    return value


def _send_child(process: subprocess.Popen[str], value: Mapping[str, object]) -> None:
    assert process.stdin is not None
    try:
        process.stdin.write(json.dumps(value, separators=(",", ":")) + "\n")
        process.stdin.flush()
    except (OSError, ValueError) as error:
        raise MCPError(-32000, "upstream GitHub MCP is unavailable") from error


def _close_child(process: subprocess.Popen[str]) -> None:
    if process.stdin is not None:
        with contextlib.suppress(OSError, ValueError):
            process.stdin.close()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass
    except OSError:
        pass


def _redact(value: object, token: str) -> object:
    if isinstance(value, str):
        return value.replace(token, "[redacted]")
    if isinstance(value, list):
        return [_redact(item, token) for item in value]
    if isinstance(value, dict):
        return {
            _redact(key, token) if isinstance(key, str) else key: _redact(item, token)
            for key, item in value.items()
        }
    return value


def _upstream_call(
    identifier: int | str,
    name: str,
    arguments: dict[str, object],
    binding: github_binding.Binding,
    platform: github_platform.PlatformProtocol,
) -> dict[str, object]:
    profile = binding.validate(Path.cwd(), platform)
    state = access_core.State(binding.state_root, profile)
    version = state.active()
    if version is None:
        raise MCPError(-32000, "employer credential is unavailable")
    token = access_core.GCMVault(profile, os.environ).read(version)
    binary = github_mcp_install.check(binding.state_root, platform.mcp_release())
    try:
        process = subprocess.Popen(
            [
                str(binary),
                "stdio",
                "--read-only",
                "--tools=list_branches,get_file_contents,pull_request_read",
                "--gh-host=https://github.com",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            cwd=binding.root,
            env=_child_environment(token),
        )
    except OSError as error:
        raise MCPError(-32000, "upstream GitHub MCP is unavailable") from error
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        _send_child(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "github-access", "version": "1"},
                },
            },
        )
        initial = _child_frame(process, deadline)
        try:
            _upstream_response(initial, 1)
        except MCPError as error:
            raise MCPError(
                -32000, "upstream GitHub MCP initialization failed"
            ) from error
        if "result" not in initial:
            raise MCPError(-32000, "upstream GitHub MCP initialization failed")
        _send_child(
            process,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )
        _send_child(
            process,
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        response = _child_frame(process, deadline)
        _upstream_response(response, identifier)
        redacted = _redact(response, token)
        if not isinstance(redacted, dict):
            raise MCPError(-32000, "upstream GitHub MCP returned an invalid response")
        return redacted
    finally:
        _close_child(process)


def serve(
    binding: github_binding.Binding,
    platform: github_platform.PlatformProtocol,
    input_stream: IO[str] = sys.stdin,
    output_stream: IO[str] = sys.stdout,
) -> int:
    initialized = False
    notified = False
    while True:
        try:
            frame = _read_frame(input_stream)
        except MCPError as error:
            _write(output_stream, _error(None, error.code, str(error)))
            continue
        if frame is None:
            return 0
        if _initialized(frame):
            if not initialized or notified:
                _write(output_stream, _error(None, -32600, "invalid JSON-RPC request"))
            else:
                notified = True
            continue
        try:
            identifier, method, params = _request(frame)
            if method == "initialize":
                if (
                    initialized
                    or set(params)
                    - {"protocolVersion", "capabilities", "clientInfo", "_meta"}
                    or not isinstance(params.get("protocolVersion"), str)
                    or params["protocolVersion"] not in SUPPORTED_PROTOCOL_VERSIONS
                    or (
                        "capabilities" in params
                        and not isinstance(params["capabilities"], dict)
                    )
                    or (
                        "clientInfo" in params
                        and not isinstance(params["clientInfo"], dict)
                    )
                ):
                    raise MCPError(-32602, "initialize request is invalid")
                _metadata(params, "initialize")
                protocol_version = params["protocolVersion"]
                initialized = True
                _write(
                    output_stream,
                    _result(
                        identifier,
                        {
                            "protocolVersion": protocol_version,
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "github-access", "version": "1"},
                        },
                    ),
                )
                continue
            if not initialized or not notified:
                raise MCPError(-32600, "initialize the MCP session first")
            if method == "ping":
                if set(params) - {"_meta"}:
                    raise MCPError(-32602, "ping request is invalid")
                _metadata(params, "ping")
                _write(output_stream, _result(identifier, {}))
            elif method == "tools/list":
                if set(params) - {"_meta"}:
                    raise MCPError(-32602, "tools list request is invalid")
                _metadata(params, "tools list")
                _write(output_stream, _result(identifier, {"tools": TOOLS}))
            elif method == "tools/call":
                name, arguments = _tool_call(binding.profile, params)
                _write(
                    output_stream,
                    _upstream_call(identifier, name, arguments, binding, platform),
                )
            else:
                raise MCPError(-32601, "MCP method is not available")
        except MCPError as error:
            request_id = frame.get("id")
            error_id = (
                request_id
                if type(request_id) is int or isinstance(request_id, str)
                else None
            )
            _write(
                output_stream,
                _error(error_id, error.code, str(error)),
            )
        except access_core.AccessError as error:
            request_id = frame.get("id")
            error_id = (
                request_id
                if type(request_id) is int or isinstance(request_id, str)
                else None
            )
            _write(
                output_stream,
                _error(error_id, -32000, str(error)),
            )
