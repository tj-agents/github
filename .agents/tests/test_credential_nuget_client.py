import base64
import hashlib
import http.server
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from typing import override

import pytest
from gate_sandbox import CURRENT_PLATFORM, GATE_SOURCE, Sandbox


class Feed(http.server.HTTPServer):
    package: bytes
    authenticated: int = 0


class Handler(http.server.BaseHTTPRequestHandler):
    server: Feed

    @override
    def log_message(self, message: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        expected = "Basic " + base64.b64encode(b"fixture-user:fixture-only").decode()
        if self.headers.get("Authorization") != expected:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="nonsecret-fixture"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.server.authenticated += 1
        root = f"http://127.0.0.1:{self.server.server_port}"
        if self.path == "/v3/index.json":
            content = json.dumps(
                {
                    "version": "3.0.0",
                    "resources": [
                        {"@id": root + "/flat/", "@type": "PackageBaseAddress/3.0.0"}
                    ],
                }
            ).encode()
        elif self.path.endswith("/index.json"):
            content = b'{"versions":["1.0.0"]}'
        elif self.path.endswith(".sha512"):
            content = base64.b64encode(hashlib.sha512(self.server.package).digest())
        elif self.path.endswith(".nupkg"):
            content = self.server.package
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/octet-stream"
            if self.path.endswith(".nupkg")
            else "application/json",
        )
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


@pytest.mark.allow_hosts(["127.0.0.1"])
def test_real_dotnet_restore_uses_native_provider_and_keeps_secret_out_of_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    program = shutil.which("dotnet")
    assert program is not None, "native acceptance requires the .NET SDK"
    executable = Path(program)
    version = subprocess.run(
        [str(executable), "--version"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    major = int(version.split(".")[0])
    assert major >= 9, "native provider acceptance requires a supported SDK"
    sdks = subprocess.run(
        [str(executable), "--list-sdks"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    sdk = next(line for line in sdks if line.startswith(version + " ["))
    sdk_root = Path(sdk.split("[", 1)[1].removesuffix("]")).parent
    installed = tmp_path / "runtime"
    shutil.copytree(
        GATE_SOURCE, installed, ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copyfile(
        Path(__file__).with_name("nuget_fixture_provider.py"),
        installed / "fixture_provider.py",
    )
    commands = tmp_path / "bin"
    commands.mkdir()
    launcher = commands / CURRENT_PLATFORM.command_launcher_name("nuget-plugin-fixture")
    python = Path(getattr(sys, "_base_executable", sys.executable))
    CURRENT_PLATFORM.write_launcher(
        launcher,
        CURRENT_PLATFORM.command_launcher_bytes(
            python, installed / "fixture_provider.py", ()
        ),
    )
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as package:
        package.writestr(
            "Fixture.NativeAuth.nuspec",
            "<package><metadata><id>Fixture.NativeAuth</id><version>1.0.0</version>"
            "<authors>Fixture</authors><description>Nonsecret native credential "
            "fixture</description></metadata></package>",
        )
        package.writestr("lib/netstandard2.0/_._", "")
    with Feed(("127.0.0.1", 0), Handler) as feed:
        feed.package = payload.getvalue()
        worker = threading.Thread(target=feed.serve_forever, daemon=True)
        worker.start()
        try:
            (tmp_path / "fixture.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                f"<TargetFramework>net{major}.0</TargetFramework>"
                "<NuGetAudit>false</NuGetAudit></PropertyGroup><ItemGroup>"
                '<PackageReference Include="Fixture.NativeAuth" Version="1.0.0" />'
                "</ItemGroup></Project>",
                encoding="utf-8",
            )
            config = tmp_path / "nuget.config"
            config.write_text(
                '<configuration><packageSources><clear/><add key="fixture" '
                f'value="http://127.0.0.1:{feed.server_port}/v3/index.json" '
                'allowInsecureConnections="true" /></packageSources></configuration>',
                encoding="utf-8",
            )
            environment = Sandbox(tmp_path).environment()
            environment.update(
                PATH=os.pathsep.join(
                    (str(commands), str(sdk_root), environment.get("PATH", ""))
                ),
                NUGET_PACKAGES=str(tmp_path / "packages"),
                NUGET_HTTP_CACHE_PATH=str(tmp_path / "http-cache"),
                NUGET_PLUGINS_CACHE_PATH=str(tmp_path / "plugin-cache"),
                DOTNET_CLI_HOME=str(tmp_path / "dotnet-home"),
                DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1",
                DOTNET_CLI_TELEMETRY_OPTOUT="1",
                DOTNET_NOLOGO="1",
                MSBUILDDISABLEWORKLOADRESOLVER="1",
            )
            for name in (
                "GH_TOKEN",
                "GITHUB_TOKEN",
                "GITHUB_NUGET_PASSWORD",
                "NUGET_PLUGIN_PATHS",
                "NUGET_NETCORE_PLUGIN_PATHS",
                "NUGET_NETFX_PLUGIN_PATHS",
                "NUGET_PLUGIN_DIAGNOSTICS",
                "VSS_NUGET_EXTERNAL_FEED_ENDPOINTS",
                "ARTIFACTS_CREDENTIALPROVIDER_FEED_ENDPOINTS",
            ):
                monkeypatch.delenv(name, raising=False)
            for name, value in environment.items():
                monkeypatch.setenv(name, value)
            restored = subprocess.run(
                [
                    str(executable),
                    "restore",
                    str(tmp_path / "fixture.csproj"),
                    "--configfile",
                    str(config),
                    "--disable-parallel",
                    "--verbosity",
                    "minimal",
                ],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        finally:
            feed.shutdown()
            worker.join(timeout=3)
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert feed.authenticated > 0
    assert (
        tmp_path / "packages/fixture.nativeauth/1.0.0/fixture.nativeauth.nuspec"
    ).is_file()
    assert "fixture-only" not in config.read_text(encoding="utf-8")
