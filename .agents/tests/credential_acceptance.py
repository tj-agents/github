"""Exercise copied native launchers without an issuer, vault or agent session."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

from gate_platform import Platform


def run(
    source: Path, platform: Platform, python: Path, environment: Mapping[str, str]
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="tj-credential-native-") as temporary:
        root = Path(temporary)
        producer = root / "producer"
        installed = root / "installed"
        shutil.copytree(source, producer, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(producer, installed)
        shutil.rmtree(producer)
        public = dict(environment)
        public.update(
            AGENT_STATE_DIRECTORY=str(root / "state"),
            HOME=str(root / "home"),
            USERPROFILE=str(root / "home"),
            PYTHONDONTWRITEBYTECODE="1",
        )
        Path(public["HOME"]).mkdir()
        commands = {"tj-credentials": (), "nuget-plugin-tj-credentials": ("nuget",)}
        launchers = {}
        for name, arguments in commands.items():
            target = root / platform.command_launcher_name(name)
            platform.write_launcher(
                target,
                platform.command_launcher_bytes(
                    python, installed / "tj_credentials.py", arguments
                ),
            )
            launchers[name] = target
        check = subprocess.run(
            [str(launchers["tj-credentials"]), "check"],
            cwd=root,
            env=public,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        if check.returncode or json.loads(check.stdout) != {"status": "not-declared"}:
            raise AssertionError(
                "copied credential launcher could not start independently"
            )
        frames = [
            {
                "RequestId": "tj-credentials-handshake",
                "Type": "Response",
                "Method": "Handshake",
                "Payload": {"ResponseCode": "Success", "ProtocolVersion": "2.0.0"},
            },
            {
                "RequestId": "1",
                "Type": "Request",
                "Method": "Handshake",
                "Payload": {
                    "ProtocolVersion": "2.0.0",
                    "MinimumProtocolVersion": "1.0.0",
                },
            },
            {
                "RequestId": "2",
                "Type": "Request",
                "Method": "Initialize",
                "Payload": {},
            },
            {
                "RequestId": "3",
                "Type": "Request",
                "Method": "GetOperationClaims",
                "Payload": {},
            },
            {"RequestId": "4", "Type": "Request", "Method": "Close"},
        ]
        plugin = subprocess.run(
            [str(launchers["nuget-plugin-tj-credentials"]), "-Plugin"],
            input="".join(json.dumps(frame) + "\n" for frame in frames),
            cwd=root,
            env=public,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        responses = [
            value
            for line in plugin.stdout.splitlines()
            if (value := json.loads(line)).get("Type") == "Response"
        ]
        if (
            plugin.returncode
            or len(responses) != 3
            or responses[0]["Payload"]["ProtocolVersion"] != "2.0.0"
            or responses[2]["Payload"]["Claims"] != []
        ):
            raise AssertionError(
                f"native NuGet protocol negotiation failed: "
                f"exit={plugin.returncode}, responses={responses!r}, "
                f"diagnostic={plugin.stderr!r}"
            )
        mcp_input = (
            '{"jsonrpc":"2.0","id":1,"method":"initialize",'
            '"params":{"protocolVersion":"2025-11-25"}}\n'
            '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
            '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
        )
        mcp = subprocess.run(
            [str(launchers["tj-credentials"]), "mcp"],
            input=mcp_input,
            cwd=root,
            env=public,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        listed = [json.loads(line) for line in mcp.stdout.splitlines()]
        if mcp.returncode or len(listed) != 2 or len(listed[1]["result"]["tools"]) != 3:
            raise AssertionError("copied MCP launcher could not list scoped tools")
        if (root / "state").exists():
            raise AssertionError(
                "protocol discovery accessed or created credential state"
            )
    return {
        "credential_launcher": "passed",
        "nuget_native_protocol": "passed",
        "mcp_native_protocol": "passed",
        "producer_removed": True,
    }


def main() -> int:
    from gate_platform import SupportedPlatform
    from github_gate import PLATFORMS

    source = Path(__file__).parents[1] / "github/runtime/gate"
    python = Path(getattr(sys, "_base_executable", sys.executable))
    result = run(
        source, PLATFORMS[SupportedPlatform(os.name)], python, dict(os.environ)
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
