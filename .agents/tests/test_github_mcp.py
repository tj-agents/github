import io
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import access_core
import github_access
import github_binding
import github_mcp_install
import github_mcp_release
import github_mcp_stdio
import github_platform
import pytest


def profile() -> access_core.Profile:
    return access_core.Profile.from_data(
        {
            "schema_version": 1,
            "host": "github.com",
            "organization": "employer",
            "login": "worker",
            "repositories": ["private"],
            "verify_repository": "private",
            "permissions": {"contents": "read"},
        }
    )


def binding() -> github_binding.Binding:
    root = Path.cwd()
    return github_binding.Binding(
        root / ".agents/github-access.json",
        root,
        profile(),
        root / "state",
        root / "runtime",
        root / "launchers",
    )


class Platform:
    def real_gh(self) -> Path:
        return Path()

    def real_git(self, environment: Mapping[str, str]) -> Path:
        return Path()

    def launcher_path(
        self, launchers: Path, launcher: github_platform.Launcher
    ) -> Path:
        return launchers

    def command_launcher_contents(
        self, script: Path, arguments: Sequence[str]
    ) -> bytes:
        return b""

    def launcher_contents(
        self,
        script: Path,
        binding_path: Path,
        launcher: github_platform.Launcher,
    ) -> bytes:
        return b""

    def write_launcher(self, path: Path, contents: bytes) -> None:
        return None

    def mcp_release(self) -> github_mcp_release.ReleaseArtifact:
        return github_mcp_release.WINDOWS_X64


def messages(output: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in output.getvalue().splitlines()]


def request(
    identifier: int, method: str, params: dict[str, object] | None = None
) -> str:
    value: dict[str, object] = {"jsonrpc": "2.0", "id": identifier, "method": method}
    if params is not None:
        value["params"] = params
    return json.dumps(value)


def initialized(params: bool = True) -> str:
    value: dict[str, object] = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    if params:
        value["params"] = {}
    return json.dumps(value)


def session(*frames: str) -> io.StringIO:
    return io.StringIO("\n".join(frames) + "\n")


def test_mcp_initialize_list_and_ping_do_not_read_credential() -> None:
    output = io.StringIO()
    github_mcp_stdio.serve(
        binding(),
        Platform(),
        session(
            request(
                1,
                "initialize",
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"roots": {"listChanged": True}},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            ),
            initialized(),
            request(2, "tools/list", {}),
            request(3, "ping", {}),
        ),
        output,
    )
    result = messages(output)
    first = result[0]["result"]
    assert first == {
        "protocolVersion": "2025-11-25",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "github-access", "version": "1"},
    }
    listed = result[1]["result"]
    assert isinstance(listed, dict)
    tools = listed["tools"]
    assert isinstance(tools, list)
    assert [tool["name"] for tool in tools if isinstance(tool, dict)] == [
        "list_branches",
        "get_file_contents",
        "pull_request_read",
    ]
    assert result[2]["result"] == {}


def test_mcp_accepts_omitted_params_and_client_capabilities() -> None:
    output = io.StringIO()
    github_mcp_stdio.serve(
        binding(),
        Platform(),
        session(
            request(
                1,
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"roots": {"listChanged": True}},
                    "clientInfo": {"name": "native", "version": "1", "extra": "ok"},
                },
            ),
            initialized(params=False),
            request(2, "tools/list"),
            request(3, "ping"),
        ),
        output,
    )
    result = messages(output)
    initialized_result = result[0]["result"]
    assert isinstance(initialized_result, dict)
    assert initialized_result["protocolVersion"] == "2025-06-18"
    assert result[1]["id"] == 2
    assert result[2]["id"] == 3


def test_mcp_bound_gateway_entry_lists_without_credential(tmp_path: Path) -> None:
    root = tmp_path / "employer"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "remote",
            "add",
            "origin",
            "https://github.com/employer/private.git",
        ],
        check=True,
    )
    declaration = tmp_path / "profile.json"
    declaration.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "host": "github.com",
                "organization": "employer",
                "login": "worker",
                "repositories": ["private"],
                "verify_repository": "private",
                "permissions": {"contents": "read"},
            }
        ),
        encoding="utf-8",
    )
    selected = github_binding.bind(
        declaration,
        root,
        tmp_path / "state",
        Path(github_access.__file__).parent,
        github_platform.current_platform(),
    )
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(Path(github_access.__file__)),
            "mcp",
            "--binding",
            str(selected.path),
        ],
        cwd=root,
        input=session(
            request(
                1,
                "initialize",
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"roots": {"listChanged": True}},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            ),
            initialized(params=False),
            request(2, "tools/list"),
        ).getvalue(),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    response = [json.loads(line) for line in result.stdout.splitlines()]
    assert response[0]["result"]["capabilities"] == {"tools": {}}
    assert [tool["name"] for tool in response[1]["result"]["tools"]] == [
        "list_branches",
        "get_file_contents",
        "pull_request_read",
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        {"owner": "other", "repo": "private"},
        {"owner": "employer", "repo": "private", "unknown": "value"},
        {"owner": "employer", "repo": "private", "page": True},
        {"owner": "employer", "repo": "private", "fields": [{}]},
    ],
)
def test_mcp_denies_invalid_tool_calls_before_upstream(
    arguments: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*values: object) -> dict[str, object]:
        pytest.fail("invalid tool call reached upstream")

    monkeypatch.setattr(github_mcp_stdio, "_upstream_call", forbidden)
    output = io.StringIO()
    github_mcp_stdio.serve(
        binding(),
        Platform(),
        session(
            request(
                1,
                "initialize",
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            ),
            initialized(),
            request(2, "tools/call", {"name": "list_branches", "arguments": arguments}),
        ),
        output,
    )
    error = messages(output)[1]["error"]
    assert isinstance(error, dict)
    assert error["code"] == -32602


def test_mcp_rejects_unknown_method() -> None:
    output = io.StringIO()
    github_mcp_stdio.serve(
        binding(),
        Platform(),
        session(
            request(
                1,
                "initialize",
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            ),
            initialized(),
            request(2, "resources/list", {}),
        ),
        output,
    )
    error = messages(output)[1]["error"]
    assert isinstance(error, dict)
    assert error["code"] == -32601


def test_mcp_rejects_nested_pull_request_method_before_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*values: object) -> dict[str, object]:
        pytest.fail("malformed tool call reached upstream")

    monkeypatch.setattr(github_mcp_stdio, "_upstream_call", forbidden)
    output = io.StringIO()
    github_mcp_stdio.serve(
        binding(),
        Platform(),
        session(
            request(
                1,
                "initialize",
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            ),
            initialized(),
            request(
                2,
                "tools/call",
                {
                    "name": "pull_request_read",
                    "arguments": {
                        "owner": "employer",
                        "repo": "private",
                        "method": ["get"],
                        "pullNumber": 1,
                    },
                },
            ),
        ),
        output,
    )
    error = messages(output)[1]["error"]
    assert isinstance(error, dict)
    assert error["code"] == -32602


def test_mcp_cache_binary_mismatch_fails_closed(tmp_path: Path) -> None:
    release = github_mcp_release.WINDOWS_X64
    archive, binary = github_mcp_install._paths(tmp_path, release)
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"archive")
    binary.write_bytes(b"binary")
    with pytest.raises(access_core.AccessError, match="mcp-install-missing"):
        github_mcp_install.check(tmp_path, release)


def test_mcp_call_uses_current_credential_and_pinned_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = binding()
    selected = github_binding.Binding(
        selected.path,
        selected.root,
        selected.profile,
        tmp_path,
        selected.runtime,
        selected.launchers,
    )
    state = access_core.State(tmp_path, selected.profile)
    version = "a" * 32
    state.select(version)
    token = "github_pat_" + "x" * 40
    captured: dict[str, object] = {}

    class Vault:
        def read(self, requested: str) -> str:
            assert requested == version
            return token

    class Child:
        def __init__(self) -> None:
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(
                json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})
                + "\n"
                + json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 7,
                        "result": {"content": [{"type": "text", "text": token}]},
                    }
                )
                + "\n"
            )

        def wait(self, timeout: float) -> int:
            return 0

        def kill(self) -> None:
            return None

    def validate(
        item: github_binding.Binding, cwd: Path, platform: Platform
    ) -> access_core.Profile:
        assert item is selected
        return item.profile

    def start(arguments: list[str], **keywords: object) -> Child:
        captured["arguments"] = arguments
        captured["environment"] = keywords["env"]
        return Child()

    monkeypatch.setattr(github_binding.Binding, "validate", validate)
    monkeypatch.setattr(access_core, "GCMVault", lambda *values: Vault())
    monkeypatch.setattr(github_mcp_install, "check", lambda *values: Path("server"))
    monkeypatch.setattr(subprocess, "Popen", start)
    response = github_mcp_stdio._upstream_call(
        7,
        "list_branches",
        {"owner": "employer", "repo": "private"},
        selected,
        Platform(),
    )
    assert captured["arguments"] == [
        "server",
        "stdio",
        "--read-only",
        "--tools=list_branches,get_file_contents,pull_request_read",
        "--gh-host=https://github.com",
    ]
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment["GITHUB_PERSONAL_ACCESS_TOKEN"] == token
    assert "GH_TOKEN" not in environment
    assert json.dumps(response).count(token) == 0


@pytest.mark.parametrize(
    "initial_response",
    [
        "",
        json.dumps({"jsonrpc": "2.0", "id": True, "result": {}}) + "\n",
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}, "extra": {}}) + "\n",
    ],
)
def test_mcp_child_failures_are_sanitized_and_reaped(
    tmp_path: Path,
    initial_response: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = binding()
    selected = github_binding.Binding(
        selected.path,
        selected.root,
        selected.profile,
        tmp_path,
        selected.runtime,
        selected.launchers,
    )
    access_core.State(tmp_path, selected.profile).select("b" * 32)
    closed = False

    class Vault:
        def read(self, version: str) -> str:
            assert version == "b" * 32
            return "github_pat_" + "z" * 40

    class Child:
        def __init__(self) -> None:
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(initial_response)

        def wait(self, timeout: float) -> int:
            nonlocal closed
            closed = True
            return 0

        def kill(self) -> None:
            return None

    monkeypatch.setattr(
        github_binding.Binding,
        "validate",
        lambda item, cwd, platform: item.profile,
    )
    monkeypatch.setattr(access_core, "GCMVault", lambda *values: Vault())
    monkeypatch.setattr(github_mcp_install, "check", lambda *values: Path("server"))
    monkeypatch.setattr(subprocess, "Popen", lambda *values, **keywords: Child())
    with pytest.raises(github_mcp_stdio.MCPError, match="upstream GitHub MCP"):
        github_mcp_stdio._upstream_call(
            7,
            "list_branches",
            {"owner": "employer", "repo": "private"},
            selected,
            Platform(),
        )
    assert closed


def test_mcp_revalidates_before_reading_vault_or_spawning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = binding()
    selected = github_binding.Binding(
        selected.path,
        selected.root,
        selected.profile,
        tmp_path,
        selected.runtime,
        selected.launchers,
    )

    def denied(*values: object) -> access_core.Profile:
        raise access_core.AccessError("binding-invalid: scope changed")

    def forbidden(*values: object, **keywords: object) -> object:
        pytest.fail("scope failure reached a credential or child process")

    monkeypatch.setattr(github_binding.Binding, "validate", denied)
    monkeypatch.setattr(access_core, "GCMVault", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with pytest.raises(access_core.AccessError, match="scope changed"):
        github_mcp_stdio._upstream_call(
            7,
            "list_branches",
            {"owner": "employer", "repo": "private"},
            selected,
            Platform(),
        )


def test_mcp_reads_rotated_credential_for_each_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = binding()
    selected = github_binding.Binding(
        selected.path,
        selected.root,
        selected.profile,
        tmp_path,
        selected.runtime,
        selected.launchers,
    )
    state = access_core.State(tmp_path, selected.profile)
    first, second = "c" * 32, "d" * 32
    reads: list[str] = []

    class Vault:
        def read(self, version: str) -> str:
            reads.append(version)
            return "github_pat_" + version + "x" * 8

    class Child:
        def __init__(self, identifier: int) -> None:
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(
                json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})
                + "\n"
                + json.dumps({"jsonrpc": "2.0", "id": identifier, "result": {}})
                + "\n"
            )

        def wait(self, timeout: float) -> int:
            return 0

        def kill(self) -> None:
            return None

    identifiers = iter((7, 8))
    monkeypatch.setattr(
        github_binding.Binding,
        "validate",
        lambda item, cwd, platform: item.profile,
    )
    monkeypatch.setattr(access_core, "GCMVault", lambda *values: Vault())
    monkeypatch.setattr(github_mcp_install, "check", lambda *values: Path("server"))
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *values, **keywords: Child(next(identifiers)),
    )
    state.select(first)
    github_mcp_stdio._upstream_call(
        7,
        "list_branches",
        {"owner": "employer", "repo": "private"},
        selected,
        Platform(),
    )
    state.select(second)
    github_mcp_stdio._upstream_call(
        8,
        "list_branches",
        {"owner": "employer", "repo": "private"},
        selected,
        Platform(),
    )
    assert reads == [first, second]
