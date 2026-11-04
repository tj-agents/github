from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import github_tools
import github_windows
import pytest
from access_core import AccessError, Profile, State
from github_platform import PlatformProtocol, current_platform
from github_tools import (
    Repository,
    git_credential_response,
    git_environment,
    parse_git_credential,
    parse_repository,
    run_gh,
    run_git,
    validate_gh_command,
    validate_git_command,
)

TOKEN = "github_pat_" + "a" * 40


@dataclass
class FakeState(State):
    version: str | None = "a" * 32

    def active(self) -> str | None:
        return self.version


@dataclass
class FakeVault:
    reads: list[str] = field(default_factory=list)

    def read(self, version: str) -> str:
        self.reads.append(version)
        return TOKEN

    def write(self, version: str, token: str) -> None:
        raise AssertionError("not expected")

    def erase(self, version: str) -> None:
        raise AssertionError("not expected")


@pytest.fixture
def profile() -> Profile:
    return Profile(
        "employer",
        "employee",
        "private-project",
        ("private-project", "other-project"),
        {"contents": "read"},
    )


@pytest.fixture
def platform() -> PlatformProtocol:
    return current_platform()


@pytest.mark.parametrize(
    "value",
    [
        "employer/private-project",
        "https://github.com/employer/private-project",
        "https://github.com/employer/private-project.git",
    ],
)
def test_parse_repository_accepts_exact_allowlisted_employer_repository(
    profile: Profile, value: str
) -> None:
    assert parse_repository(profile, value) == Repository("employer", "private-project")


@pytest.mark.parametrize(
    "value",
    [
        "other/private-project",
        "employer/unlisted",
        "git@github.com:employer/private-project.git",
        "http://github.com/employer/private-project",
        "https://evil.example/employer/private-project",
        "https://github.com/employer/private-project/extra",
        "employer/%70rivate-project",
        "employer/../private-project",
    ],
)
def test_parse_repository_rejects_other_hosts_and_ambiguous_paths(
    profile: Profile, value: str
) -> None:
    with pytest.raises(AccessError):
        parse_repository(profile, value)


@pytest.mark.parametrize(
    "arguments",
    [
        ("pr", "list", "--repo", "employer/private-project"),
        ("pr", "view", "42", "--repo=employer/private-project"),
        ("issue", "view", "https://github.com/employer/private-project/issues/42"),
        ("run", "list"),
        ("release", "view", "v1"),
    ],
)
def test_gh_validator_allows_bounded_supported_commands(
    profile: Profile, arguments: tuple[str, ...]
) -> None:
    invocation = validate_gh_command(
        profile, arguments, default_repository="employer/private-project"
    )
    assert invocation.repository == Repository("employer", "private-project")
    assert invocation.arguments[-2:] == ("--repo", "employer/private-project")


@pytest.mark.parametrize(
    "arguments",
    [
        ("auth", "status"),
        ("extension", "install", "evil"),
        ("api", "https://api.github.com/user"),
        ("api", "/repos/employer/private-project/../issues"),
        ("api", "/repos/employer/private-project/issues", "-H", "Authorization: evil"),
        ("pr", "view", "42", "--hostname", "evil.example"),
        ("pr", "view", "https://github.com/employer/other-project/pull/42"),
        ("pr", "view", "42", "--repo", "other/private-project"),
        (
            "pr",
            "view",
            "42",
            "--repo",
            "employer/private-project",
            "--repo",
            "employer/other-project",
        ),
        ("graphql", "-f", "query={viewer{login}}"),
    ],
)
def test_gh_validator_rejects_broad_or_mixed_scope_before_vault(
    profile: Profile, arguments: tuple[str, ...]
) -> None:
    vault = FakeVault()
    with pytest.raises(AccessError):
        validate_gh_command(
            profile, arguments, default_repository="employer/private-project"
        )
    assert vault.reads == []


def test_gh_api_is_rest_get_only_inside_selected_repository(profile: Profile) -> None:
    invocation = validate_gh_command(
        profile,
        (
            "api",
            "/repos/employer/private-project/issues",
            "-X",
            "GET",
            "-f",
            "state=open",
            "--paginate",
            "--repo",
            "employer/private-project",
        ),
    )
    assert invocation.arguments[:2] == ("api", "/repos/employer/private-project/issues")
    with pytest.raises(AccessError, match="GET only"):
        validate_gh_command(
            profile,
            (
                "api",
                "/repos/employer/private-project/issues",
                "-X",
                "POST",
                "--repo",
                "employer/private-project",
            ),
        )


def test_gh_api_normalizes_relative_path_forces_get_and_rejects_scope_bypass(
    profile: Profile,
) -> None:
    invocation = validate_gh_command(
        profile,
        (
            "api",
            "repos/employer/private-project/issues",
            "-f",
            "state=open",
            "--repo",
            "employer/private-project",
        ),
    )
    assert invocation.arguments == (
        "api",
        "/repos/employer/private-project/issues",
        "-f",
        "state=open",
        "-X",
        "GET",
    )
    permitted = Profile(
        profile.organization,
        profile.login,
        profile.verify_repository,
        ("private-project", "private-project-other"),
        profile.permissions,
    )
    for arguments in [
        ("api", "/repos/employer/private-project-other/issues"),
        ("api", "/repos/employer/private-project/issues", "-F", "body=@secret"),
        ("api", "/repos/employer/private-project/issues", "-f", "unknown=value"),
    ]:
        with pytest.raises(AccessError):
            validate_gh_command(
                permitted,
                (*arguments, "--repo", "employer/private-project"),
            )


@pytest.mark.parametrize(
    "arguments",
    [
        ("repo", "view", "employer/other-project"),
        ("pr", "view", "https://github.com/employer/private-project/issues/4"),
        ("issue", "view", "https://github.com/employer/private-project/pull/4"),
        ("pr", "view", "employer/private-project"),
        (
            "release",
            "view",
            "https://github.com/employer/private-project/releases/tag/v1",
        ),
        ("pr", "list", "--web"),
        ("pr", "create", "--title", "title"),
        ("pr", "comment", "4"),
        ("pr", "review", "4", "--request-changes"),
        ("pr", "merge", "4"),
        (
            "pr",
            "create",
            "--title",
            "title",
            "--body",
            "body",
            "--head",
            "other:branch",
        ),
        ("pr", "create", "--title", "--body"),
        ("issue", "create", "--body", "body"),
        ("pr", "comment", "4", "--delete-last"),
        ("issue", "comment", "4", "--delete-last"),
    ],
)
def test_gh_validator_rejects_editor_browser_and_mixed_owner_paths(
    profile: Profile, arguments: tuple[str, ...]
) -> None:
    with pytest.raises(AccessError):
        validate_gh_command(
            profile, arguments, default_repository="employer/private-project"
        )


@pytest.mark.parametrize(
    "arguments",
    [
        ("pr", "ready", "4"),
        ("pr", "close", "4", "--delete-branch"),
        ("pr", "reopen", "4"),
        ("pr", "review", "4", "--approve"),
        ("pr", "merge", "4", "--squash", "--match-head-commit", "a" * 40),
        ("pr", "create", "--fill", "--head", "branch"),
        ("pr", "comment", "4", "--body-file", "C:/work/comment.md"),
    ],
)
def test_gh_validator_allows_bounded_noninteractive_pr_workflows(
    profile: Profile, arguments: tuple[str, ...]
) -> None:
    validate_gh_command(
        profile, arguments, default_repository="employer/private-project"
    )


@pytest.mark.parametrize(
    "arguments",
    [
        ("pr", "create", "--title", "title", "--body", "body", "--head", "branch"),
        ("pr", "review", "4", "--approve"),
    ],
)
def test_gh_validator_allows_create_and_review_with_default_repository(
    profile: Profile, arguments: tuple[str, ...]
) -> None:
    invocation = validate_gh_command(
        profile, arguments, default_repository="employer/private-project"
    )
    assert invocation.arguments[-2:] == ("--repo", "employer/private-project")


@pytest.mark.parametrize(
    "arguments",
    [
        (
            "pr",
            "create",
            "--title",
            "title",
            "--body",
            "body",
            "--head",
            "branch",
            "--head=other",
        ),
        (
            "pr",
            "create",
            "--title=title",
            "--body=body",
            "--head=branch",
            "--head",
            "other",
        ),
    ],
)
def test_gh_validator_rejects_repeated_singleton_options(
    profile: Profile, arguments: tuple[str, ...]
) -> None:
    with pytest.raises(AccessError, match="repeated gh option"):
        validate_gh_command(
            profile, arguments, default_repository="employer/private-project"
        )


def test_gh_validator_does_not_treat_title_value_as_body(profile: Profile) -> None:
    with pytest.raises(AccessError):
        validate_gh_command(
            profile,
            ("pr", "create", "--title", "--body"),
            default_repository="employer/private-project",
        )


def test_gh_validator_treats_body_value_as_value_not_head(profile: Profile) -> None:
    invocation = validate_gh_command(
        profile,
        ("pr", "create", "--title", "title", "--body", "--head", "--head", "branch"),
        default_repository="employer/private-project",
    )
    assert invocation.arguments[-2:] == ("--repo", "employer/private-project")


def test_gh_validator_checks_effective_head_after_option_like_title(
    profile: Profile,
) -> None:
    with pytest.raises(AccessError, match="--head must name a branch"):
        validate_gh_command(
            profile,
            (
                "pr",
                "create",
                "--title",
                "--head",
                "--body",
                "body",
                "--head",
                "employer:branch",
            ),
            default_repository="employer/private-project",
        )


def test_gh_validator_emits_repository_view_target_as_positional(
    profile: Profile,
) -> None:
    invocation = validate_gh_command(
        profile,
        ("repo", "view", "employer/private-project", "--json", "name"),
    )
    assert invocation.arguments == (
        "repo",
        "view",
        "--json",
        "name",
        "employer/private-project",
    )


def test_gh_runner_validates_before_reading_and_isolates_child_environment(
    profile: Profile, tmp_path: Path, platform: PlatformProtocol
) -> None:
    script = (
        "import os, sys\nprint(os.environ['GH_TOKEN'])\nprint(os.environ['GH_HOST'])\n"
        "print(os.environ.get('HTTP_PROXY', ''))\n"
        "print(os.environ.get('GH_BROWSER', ''))\n"
        "print(os.environ.get('PYTHONPATH', ''))\n"
        "print(os.getcwd())\nprint(sys.argv[1:])\n"
    )
    executable = tmp_path / ("fake-gh.cmd" if os.name == "nt" else "fake-gh")
    if os.name == "nt":
        payload = tmp_path / "fake-gh.py"
        payload.write_text(script, encoding="utf-8")
        executable.write_text(
            f'@"{sys.executable}" "{payload}" %*\r\n', encoding="utf-8"
        )
    else:
        executable.write_text(f"#!{sys.executable}\n{script}", encoding="utf-8")
        executable.chmod(0o700)
    vault = FakeVault()
    state = FakeState()
    result = run_gh(
        profile,
        state,
        vault,
        executable,
        ("pr", "list"),
        platform=platform,
        cwd=tmp_path,
        default_repository="employer/private-project",
        environment={
            "PATH": os.environ["PATH"],
            "HTTP_PROXY": "http://proxy",
            "GH_TOKEN": "old",
            "GH_BROWSER": "browser",
            "PYTHONPATH": "injected",
        },
    )
    assert vault.reads == ["a" * 32]
    assert TOKEN not in result.stdout
    assert "[redacted]" in result.stdout
    assert "github.com" in result.stdout
    assert "http://proxy" not in result.stdout
    assert "browser" not in result.stdout
    assert "injected" not in result.stdout
    assert str(tmp_path) in result.stdout


def test_gh_runner_uses_caller_directory_for_relative_body_file(
    profile: Profile, tmp_path: Path, platform: PlatformProtocol
) -> None:
    body = tmp_path / "body.md"
    body.write_text("body", encoding="utf-8")
    script = (
        "from pathlib import Path\nimport sys\n"
        "print(Path(sys.argv[sys.argv.index('--body-file') + 1]).read_text())\n"
        "print(Path.cwd())\n"
    )
    executable = tmp_path / ("fake-gh.cmd" if os.name == "nt" else "fake-gh")
    if os.name == "nt":
        payload = tmp_path / "fake-gh.py"
        payload.write_text(script, encoding="utf-8")
        executable.write_text(
            f'@"{sys.executable}" "{payload}" %*\r\n', encoding="utf-8"
        )
    else:
        executable.write_text(f"#!{sys.executable}\n{script}", encoding="utf-8")
        executable.chmod(0o700)
    result = run_gh(
        profile,
        FakeState(),
        FakeVault(),
        executable,
        (
            "pr",
            "create",
            "--title",
            "title",
            "--body-file",
            "body.md",
            "--head",
            "branch",
        ),
        platform=platform,
        cwd=tmp_path,
        default_repository="employer/private-project",
    )
    assert result.stdout.splitlines() == ["body", str(tmp_path)]


def test_gh_runner_rejects_before_vault_read(
    profile: Profile, tmp_path: Path, platform: PlatformProtocol
) -> None:
    vault = FakeVault()
    with pytest.raises(AccessError):
        run_gh(
            profile,
            FakeState(),
            vault,
            Path(sys.executable),
            ("auth", "status"),
            platform=platform,
            cwd=tmp_path,
            default_repository="employer/private-project",
        )
    assert vault.reads == []


def test_gh_runner_rejects_missing_pr_head_before_vault_read(
    profile: Profile, tmp_path: Path, platform: PlatformProtocol
) -> None:
    vault = FakeVault()
    with pytest.raises(AccessError, match="pr create requires --head"):
        run_gh(
            profile,
            FakeState(),
            vault,
            Path(sys.executable),
            ("pr", "create", "--title", "title", "--body", "body"),
            platform=platform,
            cwd=tmp_path,
            default_repository="employer/private-project",
        )
    assert vault.reads == []


def test_gh_runner_honors_explicit_empty_environment(
    profile: Profile,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform: PlatformProtocol,
) -> None:
    captured: dict[str, str] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        environment = kwargs.get("env")
        assert isinstance(environment, dict)
        captured.update(environment)
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setenv("AMBIENT_TEST", "ambient")
    monkeypatch.setattr("github_tools.subprocess.run", fake_run)
    monkeypatch.setattr(platform, "real_git", lambda environment: Path(sys.executable))
    run_gh(
        profile,
        FakeState(),
        FakeVault(),
        Path(sys.executable),
        ("pr", "list"),
        platform=platform,
        cwd=tmp_path,
        default_repository="employer/private-project",
        environment={},
    )
    assert "AMBIENT_TEST" not in captured


@pytest.mark.skipif(os.name != "nt", reason="requires the Windows launcher")
def test_gh_git_child_launcher_removes_tokens_from_actual_git_helper(
    profile: Profile,
    tmp_path: Path,
    platform: PlatformProtocol,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = git_worktree(tmp_path)
    git = git_executable()
    for arguments in (
        ("config", "user.name", "Synthetic User"),
        ("config", "user.email", "synthetic@example.invalid"),
    ):
        subprocess.run([str(git), "-C", str(root), *arguments], check=True)
    tracked = root / "tracked.txt"
    tracked.write_text("before\n", encoding="utf-8")
    subprocess.run([str(git), "-C", str(root), "add", "tracked.txt"], check=True)
    subprocess.run(
        [str(git), "-C", str(root), "commit", "--quiet", "-m", "initial"], check=True
    )
    tracked.write_text("after\n", encoding="utf-8")
    capture = tmp_path / "git-helper.json"
    helper_payload = tmp_path / "git-helper.py"
    helper_payload.write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "temp = Path(os.environ['TEMP'])\n"
        "Path(os.environ['GIT_CHILD_CAPTURE']).write_text(\n"
        "    json.dumps({key: os.environ.get(key) for key in (\n"
        "        'GH_TOKEN', 'GITHUB_PERSONAL_ACCESS_TOKEN',\n"
        "        'GIT_ALLOW_PROTOCOL', 'GIT_NO_LAZY_FETCH', 'TEMP', 'TMP'\n"
        "    )} | {'TEMP_EXISTS': temp.is_dir()}), encoding='utf-8'\n"
        ")\n",
        encoding="utf-8",
    )
    helper = tmp_path / "git-helper.cmd"
    helper.write_text(
        f'@"{sys.executable}" "{helper_payload}" %*\r\n', encoding="utf-8"
    )
    gh_payload = tmp_path / "fake-gh.py"
    gh_payload.write_text(
        "import subprocess, sys\n"
        "raise SystemExit(subprocess.run(['git', 'diff', '--ext-diff']).returncode)\n",
        encoding="utf-8",
    )
    executable = tmp_path / "fake-gh.cmd"
    executable.write_text(
        f'@"{sys.executable}" "{gh_payload}" %*\r\n', encoding="utf-8"
    )
    launched: list[tuple[str, ...]] = []
    command_launcher_contents = platform.command_launcher_contents

    def capture_launcher(script: Path, arguments: tuple[str, ...]) -> bytes:
        launched.append(arguments)
        return command_launcher_contents(script, arguments)

    monkeypatch.setattr(platform, "command_launcher_contents", capture_launcher)
    caller_temp = tmp_path / "caller-temp"
    result = run_gh(
        profile,
        FakeState(),
        FakeVault(),
        executable,
        ("pr", "list"),
        platform=platform,
        cwd=root,
        default_repository="employer/private-project",
        environment={
            "PATH": os.environ["PATH"],
            "SYSTEMROOT": os.environ["SYSTEMROOT"],
            "GIT_EXTERNAL_DIFF": helper.as_posix(),
            "GIT_CHILD_CAPTURE": str(capture),
            "GITHUB_PERSONAL_ACCESS_TOKEN": "sentinel",
            "TEMP": str(caller_temp),
            "TMP": str(caller_temp),
        },
    )
    assert result.returncode == 0
    child = json.loads(capture.read_text(encoding="utf-8"))
    assert {
        key: child[key]
        for key in (
            "GH_TOKEN",
            "GITHUB_PERSONAL_ACCESS_TOKEN",
            "GIT_ALLOW_PROTOCOL",
            "GIT_NO_LAZY_FETCH",
        )
    } == {
        "GH_TOKEN": None,
        "GITHUB_PERSONAL_ACCESS_TOKEN": None,
        "GIT_ALLOW_PROTOCOL": "",
        "GIT_NO_LAZY_FETCH": "1",
    }
    assert child["TEMP"] == child["TMP"]
    assert child["TEMP"] != str(caller_temp)
    assert child["TEMP_EXISTS"] is True
    assert launched == [(str(git.resolve()),)]


def test_git_credential_reader_checks_exact_protocol_host_path_and_login(
    profile: Profile,
) -> None:
    request = (
        "protocol=https\nhost=github.com\npath=employer/private-project.git\n"
        "username=employee\n\n"
    )
    assert parse_git_credential(profile, request) == Repository(
        "employer", "private-project"
    )
    vault = FakeVault()
    answer = git_credential_response(profile, FakeState(), vault, "get", request)
    assert answer == f"username=employee\npassword={TOKEN}\n\n"
    assert vault.reads == ["a" * 32]


@pytest.mark.parametrize(
    "credential_request",
    [
        "protocol=http\nhost=github.com\npath=employer/private-project\n",
        "protocol=https\nhost=evil.example\npath=employer/private-project\n",
        "protocol=https\nhost=github.com\npath=other/private-project\n",
        "protocol=https\nhost=github.com\npath=employer/private-project\nusername=other\n",
        "protocol=https\nhost=github.com\npath=employer/private-project\npath=employer/other-project\n",
    ],
)
def test_git_credential_reader_rejects_before_vault(
    profile: Profile, credential_request: str
) -> None:
    vault = FakeVault()
    with pytest.raises(AccessError):
        git_credential_response(profile, FakeState(), vault, "get", credential_request)
    assert vault.reads == []


def test_git_credential_store_and_erase_do_not_read_or_replace_active_credential(
    profile: Profile,
) -> None:
    vault = FakeVault()
    request = "protocol=https\nhost=github.com\npath=employer/private-project\n"
    assert git_credential_response(profile, FakeState(), vault, "store", request) == ""
    assert git_credential_response(profile, FakeState(), vault, "erase", request) == ""
    assert vault.reads == []


def test_git_command_validator_requires_declared_remote_or_exact_clone_url(
    profile: Profile,
) -> None:
    invocation = validate_git_command(
        profile,
        ("fetch", "origin", "main"),
        remotes={"origin": "https://github.com/employer/private-project.git"},
    )
    assert invocation.repository == Repository("employer", "private-project")
    assert validate_git_command(profile, ("status",), remotes={}).repository is None
    assert validate_git_command(
        profile,
        ("ls-remote", "https://github.com/employer/private-project.git"),
        remotes={},
    ).repository == Repository("employer", "private-project")
    for arguments in [
        ("fetch", "origin", "--upload-pack=evil"),
        ("-c", "credential.helper=store", "fetch", "origin"),
        ("clone", "https://github.com/other/private-project.git"),
        ("clone", "employer/private-project"),
    ]:
        with pytest.raises(AccessError):
            validate_git_command(
                profile,
                arguments,
                remotes={"origin": "https://github.com/employer/private-project.git"},
            )
    with pytest.raises(AccessError):
        validate_git_command(
            profile,
            ("fetch", "origin"),
            remotes={"origin": "employer/private-project"},
        )


def fake_git(tmp_path: Path, script: str) -> Path:
    executable = tmp_path / ("fake-git.cmd" if os.name == "nt" else "fake-git")
    if os.name == "nt":
        payload = tmp_path / "fake-git.py"
        payload.write_text(script, encoding="utf-8")
        executable.write_text(
            f'@"{sys.executable}" "{payload}" %*\r\n', encoding="utf-8"
        )
    else:
        executable.write_text(f"#!{sys.executable}\n{script}", encoding="utf-8")
        executable.chmod(0o700)
    return executable


def git_executable() -> Path:
    executable = shutil.which("git")
    assert executable is not None
    return Path(executable).resolve()


def git_worktree(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    return root


def test_git_environment_overrides_inherited_credentials_and_network_controls(
    tmp_path: Path,
) -> None:
    environment = git_environment(
        {
            "PATH": "tools",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "store",
            "GIT_EXEC_PATH": "evil",
            "HTTP_PROXY": "http://proxy",
            "GH_TOKEN": "old",
            "GIT_SSL_NO_VERIFY": "1",
            "GIT_HTTP_USER_AGENT": "agent",
            "GIT_CURL_VERBOSE": "1",
            "GIT_PROXY_COMMAND": "proxy",
            "SSLKEYLOGFILE": "keys",
            "CURL_SSL_BACKEND": "openssl",
        },
        "C:/agent/github-credential-helper.exe",
        tmp_path,
    )
    assert "HTTP_PROXY" not in environment
    assert "GH_TOKEN" not in environment
    assert "GIT_EXEC_PATH" not in environment
    assert "GIT_SSL_NO_VERIFY" not in environment
    assert "GIT_HTTP_USER_AGENT" not in environment
    assert "GIT_CURL_VERBOSE" not in environment
    assert "GIT_PROXY_COMMAND" not in environment
    assert "SSLKEYLOGFILE" not in environment
    assert "CURL_SSL_BACKEND" not in environment
    assert environment["GIT_CONFIG_COUNT"] == "10"
    assert environment["HOME"] == str(tmp_path)
    assert environment["NETRC"] == os.devnull
    assert environment["GIT_ALLOW_PROTOCOL"] == "https"
    assert environment["GIT_CONFIG_VALUE_1"] == ""
    assert environment["GIT_CONFIG_VALUE_2"] == ""
    assert environment["GIT_CONFIG_VALUE_3"] == ""
    assert environment["GIT_CONFIG_VALUE_4"] == ""
    assert environment["GIT_CONFIG_VALUE_5"] == ""
    assert environment["GIT_CONFIG_VALUE_6"] == "false"
    assert environment["GIT_CONFIG_VALUE_7"] == "true"
    assert environment["GIT_CONFIG_VALUE_8"] == "true"
    assert environment["GIT_CONFIG_VALUE_9"] == "C:/agent/github-credential-helper.exe"


def test_git_runner_uses_credential_free_environment_for_local_commands(
    profile: Profile, tmp_path: Path
) -> None:
    script = (
        "import os\nprint(os.environ.get('GIT_CONFIG_COUNT', ''))\n"
        "print(os.environ.get('GIT_CONFIG_KEY_9', ''))\n"
        "print(os.environ.get('GIT_NO_LAZY_FETCH', ''))\n"
        "print(os.environ.get('HTTP_PROXY', ''))\n"
    )
    executable = fake_git(tmp_path, script)
    result = run_git(
        profile,
        executable,
        ("status",),
        remotes={},
        helper_command="C:/agent/github-credential-helper.exe",
        cwd=tmp_path,
        environment={"PATH": os.environ["PATH"], "HTTP_PROXY": "http://proxy"},
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["", "", "1", ""]


def test_git_runner_rejects_unsafe_effective_config_before_network_launch(
    profile: Profile, tmp_path: Path
) -> None:
    capture = tmp_path / "capture.jsonl"
    script = (
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "record = {'arguments': sys.argv[1:], 'environment': {\n"
        "    key: os.environ.get(key)\n"
        "    for key in ('GIT_CONFIG_COUNT', 'GIT_CONFIG_VALUE_9')\n"
        "}}\n"
        "with Path(os.environ['CAPTURE']).open('a', encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps(record) + '\\n')\n"
        "if sys.argv[1] == 'config':\n"
        "    print('http.proxy\\0', end='')\n"
    )
    with pytest.raises(AccessError, match=r"^git-config-unsafe$"):
        run_git(
            profile,
            fake_git(tmp_path, script),
            ("fetch", "origin"),
            remotes={"origin": "https://github.com/employer/private-project.git"},
            helper_command="C:/agent/github-credential-helper.exe",
            cwd=tmp_path,
            environment={"PATH": os.environ["PATH"], "CAPTURE": str(capture)},
        )
    records = [
        json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines()
    ]
    assert records == [
        {
            "arguments": [
                "config",
                "--includes",
                "--null",
                "--show-scope",
                "--show-origin",
                "--list",
            ],
            "environment": {"GIT_CONFIG_COUNT": "0", "GIT_CONFIG_VALUE_9": None},
        }
    ]


def test_git_runner_normalizes_network_commands_and_isolates_network_child(
    profile: Profile, tmp_path: Path
) -> None:
    capture = tmp_path / "capture.jsonl"
    original_home = tmp_path / "original-home"
    script = (
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "keys = ('GIT_CONFIG_COUNT', 'GIT_CONFIG_VALUE_9', 'HOME', 'NETRC',\n"
        "        'GCM_CREDENTIAL_STORE', 'GNUPGHOME', 'PASSWORD_STORE_DIR',\n"
        "        'HTTP_PROXY', 'GIT_SSL_NO_VERIFY', 'GIT_TRACE', 'SSLKEYLOGFILE',\n"
        "        'GIT_EXEC_PATH', 'GIT_ALLOW_PROTOCOL', 'APPDATA',\n"
        "        'DBUS_SESSION_BUS_ADDRESS')\n"
        "record = {'arguments': sys.argv[1:],\n"
        "          'environment': {key: os.environ.get(key) for key in keys}}\n"
        "record['home_clean'] = not any(\n"
        "    (Path(os.environ['HOME']) / name).exists()\n"
        "    for name in ('.netrc', '_netrc')\n"
        ")\n"
        "with Path(os.environ['CAPTURE']).open('a', encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps(record) + '\\n')\n"
    )
    result = run_git(
        profile,
        fake_git(tmp_path, script),
        ("clone", "https://github.com/employer/private-project.git"),
        remotes={},
        helper_command="C:/agent/github-credential-helper.exe",
        cwd=tmp_path,
        environment={
            "PATH": os.environ["PATH"],
            "CAPTURE": str(capture),
            "HOME": str(original_home),
            "GCM_CREDENTIAL_STORE": "gpg",
            "HTTP_PROXY": "http://proxy",
            "GIT_SSL_NO_VERIFY": "1",
            "GIT_TRACE": "1",
            "SSLKEYLOGFILE": "keys",
            "GIT_EXEC_PATH": "evil",
            "APPDATA": "app-data",
            "DBUS_SESSION_BUS_ADDRESS": "session-bus",
        },
    )
    assert result.returncode == 0
    records = [
        json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["arguments"] == [
        "config",
        "--includes",
        "--null",
        "--show-scope",
        "--show-origin",
        "--list",
    ]
    assert records[1]["arguments"] == [
        "clone",
        "--template=",
        "--no-recurse-submodules",
        "https://github.com/employer/private-project.git",
    ]
    environment = records[1]["environment"]
    assert environment["GIT_CONFIG_COUNT"] == "10"
    assert environment["GIT_CONFIG_VALUE_9"] == "C:/agent/github-credential-helper.exe"
    assert environment["HOME"] != str(original_home)
    assert environment["NETRC"] == os.devnull
    assert environment["GIT_ALLOW_PROTOCOL"] == "https"
    assert environment["GCM_CREDENTIAL_STORE"] == "gpg"
    assert environment["GNUPGHOME"] == str(original_home / ".gnupg")
    assert environment["PASSWORD_STORE_DIR"] == str(original_home / ".password-store")
    assert environment["APPDATA"] == "app-data"
    assert environment["DBUS_SESSION_BUS_ADDRESS"] == "session-bus"
    assert all(
        environment[key] is None
        for key in (
            "HTTP_PROXY",
            "GIT_SSL_NO_VERIFY",
            "GIT_TRACE",
            "SSLKEYLOGFILE",
            "GIT_EXEC_PATH",
        )
    )
    assert records[1]["home_clean"] is True


@pytest.mark.parametrize("command", ["fetch", "pull", "push"])
def test_git_runner_disables_submodule_recursion_for_network_commands(
    profile: Profile,
    tmp_path: Path,
    command: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = (
        "import json, sys\n"
        "if sys.argv[1] != 'config':\n"
        "    print(json.dumps(sys.argv[1:]))\n"
    )
    monkeypatch.setattr(
        github_tools,
        "_effective_git_config",
        lambda executable, cwd, source, timeout: github_tools.GitConfigSnapshot(
            (), (), None
        ),
    )
    result = run_git(
        profile,
        fake_git(tmp_path, script),
        (command, "origin"),
        remotes={"origin": "https://github.com/employer/private-project.git"},
        helper_command="helper",
        cwd=tmp_path,
        environment={"PATH": os.environ["PATH"]},
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == [command, "--recurse-submodules=no", "origin"]


@pytest.mark.parametrize("backend", ["gpg", "secretservice"])
def test_git_environment_preserves_safe_public_credential_backends(
    tmp_path: Path, backend: str
) -> None:
    environment = git_environment(
        {"PATH": "tools", "GCM_CREDENTIAL_STORE": backend}, "helper", tmp_path
    )
    assert environment["GCM_CREDENTIAL_STORE"] == backend


def test_git_runner_rejects_included_local_config_before_network_launch(
    profile: Profile, tmp_path: Path
) -> None:
    root = git_worktree(tmp_path)
    included = tmp_path / "included.config"
    included.write_text("[credential]\n\thelper = store\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(root), "config", "include.path", str(included)], check=True
    )
    with pytest.raises(AccessError, match=r"^git-config-unsafe$"):
        run_git(
            profile,
            git_executable(),
            ("fetch", "origin"),
            remotes={"origin": "https://github.com/employer/private-project.git"},
            helper_command="helper",
            cwd=root,
            environment={"PATH": os.environ["PATH"]},
        )


def test_git_runner_rejects_worktree_config_before_network_launch(
    profile: Profile, tmp_path: Path
) -> None:
    root = git_worktree(tmp_path)
    subprocess.run(
        ["git", "-C", str(root), "config", "extensions.worktreeConfig", "true"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "config",
            "--worktree",
            "remote.origin.proxy",
            "proxy",
        ],
        check=True,
    )
    with pytest.raises(AccessError, match=r"^git-config-unsafe$"):
        run_git(
            profile,
            git_executable(),
            ("fetch", "origin"),
            remotes={"origin": "https://github.com/employer/private-project.git"},
            helper_command="helper",
            cwd=root,
            environment={"PATH": os.environ["PATH"]},
        )


def test_git_runner_allows_ordinary_local_config(
    profile: Profile, tmp_path: Path
) -> None:
    root = git_worktree(tmp_path)
    subprocess.run(
        ["git", "-C", str(root), "config", "core.autocrlf", "false"], check=True
    )
    result = run_git(
        profile,
        git_executable(),
        ("status",),
        remotes={},
        helper_command="helper",
        cwd=root,
        environment={"PATH": os.environ["PATH"]},
    )
    assert result.returncode == 0


def test_git_config_snapshot_preserves_home_relative_remote_for_child(
    profile: Profile, tmp_path: Path
) -> None:
    root = git_worktree(tmp_path)
    original_home = tmp_path / "original-home"
    original_home.mkdir()
    (original_home / "remotes.config").write_text(
        '[remote "origin"]\n\turl = https://github.com/employer/private-project.git\n',
        encoding="utf-8",
    )
    git = git_executable()
    subprocess.run(
        [str(git), "-C", str(root), "config", "include.path", "~/remotes.config"],
        check=True,
    )
    source = {"PATH": os.environ["PATH"], "HOME": str(original_home)}
    snapshot = github_tools._effective_git_config(git, root, source, 60)
    assert any(
        entry.key.casefold().startswith(("include.", "includeif."))
        for entry in snapshot.entries
    )
    home = tmp_path / "sanitized-home"
    home.mkdir()
    github_tools._mirror_config(snapshot, home)
    isolated = github_tools._config_entries(
        git, root, {**source, "HOME": str(home)}, 60
    )
    assert github_tools._config_signature(isolated) == github_tools._config_signature(
        snapshot.entries
    )
    environment = git_environment(source, "helper", home)
    result = subprocess.run(
        [
            str(git),
            "config",
            "--get",
            "remote.origin.url",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert environment["HOME"] == str(home)
    assert result.returncode == 0
    assert result.stdout.strip() == "https://github.com/employer/private-project.git"


def _isolated_config_snapshot(
    root: Path, original_home: Path, isolated_home: Path
) -> tuple[Path, github_tools.GitConfigSnapshot, dict[str, str]]:
    git = git_executable()
    source = {"PATH": os.environ["PATH"], "HOME": str(original_home)}
    snapshot = github_tools._effective_git_config(git, root, source, 60)
    github_tools._mirror_config(snapshot, isolated_home)
    isolated = github_tools._config_entries(
        git, root, {**source, "HOME": str(isolated_home)}, 60
    )
    assert github_tools._config_signature(isolated) == github_tools._config_signature(
        snapshot.entries
    )
    return git, snapshot, source


def test_git_config_snapshot_preserves_tracking_and_push_option_cardinality(
    tmp_path: Path,
) -> None:
    root = git_worktree(tmp_path)
    home = tmp_path / "original-home"
    home.mkdir()
    git = git_executable()
    for arguments in (
        ("config", "branch.main.remote", "origin"),
        ("config", "branch.main.merge", "refs/heads/main"),
        ("config", "--add", "push.pushOption", "first"),
        ("config", "--add", "push.pushOption", ""),
        ("config", "--add", "push.pushOption", "last"),
    ):
        subprocess.run([str(git), "-C", str(root), *arguments], check=True)
    isolated_home = tmp_path / "isolated-home"
    isolated_home.mkdir()
    git, _, source = _isolated_config_snapshot(root, home, isolated_home)
    environment = git_environment(source, "helper", isolated_home)
    merge = subprocess.run(
        [str(git), "config", "--get-all", "branch.main.merge"],
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    options = subprocess.run(
        [str(git), "config", "--get-all", "push.pushOption"],
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert merge.stdout.splitlines() == ["refs/heads/main"]
    assert options.stdout.splitlines() == ["first", "", "last"]


def test_git_config_snapshot_preserves_nested_home_include_order(
    tmp_path: Path,
) -> None:
    root = git_worktree(tmp_path)
    home = tmp_path / "original-home"
    nested = home / "nested"
    nested.mkdir(parents=True)
    (home / "values.config").write_text(
        "[demo]\n\tvalue = home\n[include]\n\tpath = nested/inner.config\n",
        encoding="utf-8",
    )
    (nested / "inner.config").write_text("[demo]\n\tvalue = nested\n", encoding="utf-8")
    config = root / ".git" / "config"
    with config.open("a", encoding="utf-8") as stream:
        stream.write(
            "[demo]\n\tvalue = before\n[include]\n\tpath = ~/values.config\n"
            "[demo]\n\tvalue = after\n"
        )
    isolated_home = tmp_path / "isolated-home"
    isolated_home.mkdir()
    git, _, source = _isolated_config_snapshot(root, home, isolated_home)
    result = subprocess.run(
        [str(git), "config", "--get-all", "demo.value"],
        cwd=root,
        env=git_environment(source, "helper", isolated_home),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["before", "home", "nested", "after"]


def test_git_config_snapshot_rejects_unsafe_nested_include_before_helper(
    profile: Profile, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = git_worktree(tmp_path)
    home = tmp_path / "original-home"
    nested = home / "nested"
    nested.mkdir(parents=True)
    (home / "values.config").write_text(
        "[include]\n\tpath = nested/unsafe.config\n", encoding="utf-8"
    )
    (nested / "unsafe.config").write_text(
        "[credential]\n\thelper = store\n", encoding="utf-8"
    )
    subprocess.run(
        [
            str(git_executable()),
            "-C",
            str(root),
            "config",
            "include.path",
            "~/values.config",
        ],
        check=True,
    )
    monkeypatch.setattr(
        github_tools,
        "git_environment",
        lambda source, helper_command, home: (_ for _ in ()).throw(
            AssertionError("credential helper environment must not be built")
        ),
    )
    with pytest.raises(AccessError, match=r"^git-config-unsafe$"):
        run_git(
            profile,
            git_executable(),
            ("fetch", "origin"),
            remotes={"origin": "https://github.com/employer/private-project.git"},
            helper_command="helper",
            cwd=root,
            environment={"PATH": os.environ["PATH"], "HOME": str(home)},
        )


def test_git_runner_rejects_unsafe_nested_config_before_clone_helper(
    profile: Profile, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = git_worktree(tmp_path)
    included = tmp_path / "included.config"
    nested = tmp_path / "nested.config"
    included.write_text(f"[include]\n\tpath = {nested.as_posix()}\n", encoding="utf-8")
    nested.write_text("[credential]\n\thelper = store\n", encoding="utf-8")
    subprocess.run(
        [
            str(git_executable()),
            "-C",
            str(root),
            "config",
            "include.path",
            str(included),
        ],
        check=True,
    )
    monkeypatch.setattr(
        github_tools,
        "git_environment",
        lambda source, helper_command, home: (_ for _ in ()).throw(
            AssertionError("credential helper environment must not be built")
        ),
    )
    with pytest.raises(AccessError, match=r"^git-config-unsafe$"):
        run_git(
            profile,
            git_executable(),
            ("clone", "https://github.com/employer/private-project.git", "clone"),
            remotes={},
            helper_command="helper",
            cwd=root,
            environment={"PATH": os.environ["PATH"]},
        )


def test_git_config_snapshot_rejects_netrc_destination(
    tmp_path: Path,
) -> None:
    root = git_worktree(tmp_path)
    home = tmp_path / "original-home"
    home.mkdir()
    (home / "_netrc").write_text("[safe]\n\tvalue = value\n", encoding="utf-8")
    subprocess.run(
        [str(git_executable()), "-C", str(root), "config", "include.path", "~/_netrc"],
        check=True,
    )
    with pytest.raises(AccessError, match=r"^git-config-unsafe$"):
        github_tools._effective_git_config(
            git_executable(), root, {"PATH": os.environ["PATH"], "HOME": str(home)}, 60
        )


@pytest.mark.skipif(os.name != "nt", reason="requires Windows path aliases")
@pytest.mark.parametrize(
    ("fixture", "alias"),
    [
        ("_netrc", "_netrc."),
        ("_netrc", "_netrc "),
        (".netrc", ".netrc."),
        ("_netrc", "_netrc::$DATA"),
    ],
)
def test_git_config_snapshot_rejects_windows_netrc_aliases(
    tmp_path: Path, fixture: str, alias: str
) -> None:
    root = git_worktree(tmp_path)
    home = tmp_path / "original-home"
    home.mkdir()
    (home / fixture).write_text("[safe]\n\tvalue = value\n", encoding="utf-8")
    subprocess.run(
        [
            str(git_executable()),
            "-C",
            str(root),
            "config",
            "include.path",
            f"~/{alias}",
        ],
        check=True,
    )
    with pytest.raises(AccessError, match=r"^git-config-unsafe$"):
        github_tools._effective_git_config(
            git_executable(),
            root,
            {"PATH": os.environ["PATH"], "HOME": str(home)},
            60,
        )


def test_git_config_snapshot_preserves_home_relative_hooks_path(tmp_path: Path) -> None:
    root = git_worktree(tmp_path)
    home = tmp_path / "original-home"
    hooks = home / "hooks"
    hooks.mkdir(parents=True)
    capture = tmp_path / "hook-ran"
    hook = hooks / "pre-commit"
    hook.write_text(
        f'#!/bin/sh\nprintf hook > "{capture.as_posix()}"\n', encoding="utf-8"
    )
    hook.chmod(0o755)
    git = git_executable()
    for arguments in (
        ("config", "user.name", "Synthetic User"),
        ("config", "user.email", "synthetic@example.invalid"),
        ("config", "core.hooksPath", "~/hooks"),
    ):
        subprocess.run([str(git), "-C", str(root), *arguments], check=True)
    (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run([str(git), "-C", str(root), "add", "tracked.txt"], check=True)
    isolated_home = tmp_path / "isolated-home"
    isolated_home.mkdir()
    git, snapshot, source = _isolated_config_snapshot(root, home, isolated_home)
    result = subprocess.run(
        [
            str(git),
            *github_tools._normalized_git_arguments(
                github_tools.GitInvocation(None, ("commit", "--quiet", "-m", "hook")),
                snapshot.hooks_path,
            ),
        ],
        cwd=root,
        env=git_environment(source, "helper", isolated_home),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert capture.read_text(encoding="utf-8") == "hook"


def test_git_config_snapshot_preserves_empty_hooks_path(tmp_path: Path) -> None:
    root = git_worktree(tmp_path)
    home = tmp_path / "original-home"
    home.mkdir()
    git = git_executable()
    subprocess.run(
        [str(git), "-C", str(root), "config", "core.hooksPath", ""], check=True
    )
    isolated_home = tmp_path / "isolated-home"
    isolated_home.mkdir()
    _, snapshot, _ = _isolated_config_snapshot(root, home, isolated_home)
    assert snapshot.hooks_path == ""
    assert github_tools._normalized_git_arguments(
        github_tools.GitInvocation(
            Repository("employer", "private-project"), ("fetch", "origin")
        ),
        snapshot.hooks_path,
    ) == ("-c", "core.hooksPath=", "fetch", "--recurse-submodules=no", "origin")


def test_git_runner_rejects_home_dependent_include_condition_before_helper(
    profile: Profile, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    root = git_worktree(home / "work")
    home_config = home / "included.config"
    home_config.write_text("[safe]\n\tvalue = value\n", encoding="utf-8")
    config = root / ".git" / "config"
    with config.open("a", encoding="utf-8") as stream:
        stream.write('[includeIf "gitdir:~/work/"]\n\tpath = ~/included.config\n')
    monkeypatch.setattr(
        github_tools,
        "git_environment",
        lambda source, helper_command, home: (_ for _ in ()).throw(
            AssertionError("credential helper environment must not be built")
        ),
    )
    with pytest.raises(AccessError, match=r"^git-config-isolation:"):
        run_git(
            profile,
            git_executable(),
            ("fetch", "origin"),
            remotes={"origin": "https://github.com/employer/private-project.git"},
            helper_command="helper",
            cwd=root,
            environment={"PATH": os.environ["PATH"], "HOME": str(home)},
        )


@pytest.mark.skipif(os.name != "nt", reason="requires Windows Git resolution")
def test_windows_git_resolver_uses_custom_path_after_generated_launcher(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    generated = state / "github-access" / "bin"
    generated.mkdir(parents=True)
    nested = generated / "binding-key"
    nested.mkdir()
    (nested / "git.exe").write_bytes(b"generated")
    config = tmp_path / "gh-config"
    child = config / "git-child"
    child.mkdir(parents=True)
    (child / "git.exe").write_bytes(b"child")
    custom = tmp_path / "custom"
    custom.mkdir()
    expected = custom / "git.exe"
    expected.write_bytes(b"custom")
    assert (
        github_windows.real_git(
            {
                "PATH": os.pathsep.join((str(nested), str(child), str(custom))),
                "AGENT_STATE_DIRECTORY": str(state),
                "GH_CONFIG_DIR": str(config),
            }
        )
        == expected.resolve()
    )


@pytest.mark.skipif(os.name != "nt", reason="requires Windows Git resolution")
def test_windows_git_resolver_uses_per_user_install(tmp_path: Path) -> None:
    local_app_data = tmp_path / "local-app-data"
    expected = local_app_data / "Programs" / "Git" / "cmd" / "git.exe"
    expected.parent.mkdir(parents=True)
    expected.write_bytes(b"per-user")
    assert (
        github_windows.real_git({"PATH": "", "LOCALAPPDATA": str(local_app_data)})
        == expected.resolve()
    )
