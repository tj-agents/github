from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from access_core import AccessError, Profile
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
class FakeState:
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
        ("pr", "create", "--fill", "--head", "employer:branch"),
        ("pr", "comment", "4", "--body-file", "C:/work/comment.md"),
    ],
)
def test_gh_validator_allows_bounded_noninteractive_pr_workflows(
    profile: Profile, arguments: tuple[str, ...]
) -> None:
    validate_gh_command(
        profile, arguments, default_repository="employer/private-project"
    )


def test_gh_runner_validates_before_reading_and_isolates_child_environment(
    profile: Profile, tmp_path: Path
) -> None:
    script = (
        "import os, sys\nprint(os.environ['GH_TOKEN'])\nprint(os.environ['GH_HOST'])\n"
        "print(os.environ.get('HTTP_PROXY', ''))\n"
        "print(os.environ.get('GH_BROWSER', ''))\n"
        "print(os.environ.get('PYTHONPATH', ''))\nprint(sys.argv[1:])\n"
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
        state,  # type: ignore[arg-type]
        vault,
        executable,
        ("pr", "list"),
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


def test_gh_runner_rejects_before_vault_read(profile: Profile, tmp_path: Path) -> None:
    vault = FakeVault()
    with pytest.raises(AccessError):
        run_gh(
            profile,
            FakeState(),  # type: ignore[arg-type]
            vault,
            Path(sys.executable),
            ("auth", "status"),
            default_repository="employer/private-project",
        )
    assert vault.reads == []


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
    answer = git_credential_response(profile, FakeState(), vault, "get", request)  # type: ignore[arg-type]
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
        git_credential_response(profile, FakeState(), vault, "get", credential_request)  # type: ignore[arg-type]
    assert vault.reads == []


def test_git_credential_store_and_erase_do_not_read_or_replace_active_credential(
    profile: Profile,
) -> None:
    vault = FakeVault()
    request = "protocol=https\nhost=github.com\npath=employer/private-project\n"
    assert git_credential_response(profile, FakeState(), vault, "store", request) == ""  # type: ignore[arg-type]
    assert git_credential_response(profile, FakeState(), vault, "erase", request) == ""  # type: ignore[arg-type]
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


def test_git_environment_overrides_inherited_credentials_and_network_controls() -> None:
    environment = git_environment(
        {
            "PATH": "tools",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "store",
            "HTTP_PROXY": "http://proxy",
            "GH_TOKEN": "old",
        },
        "C:/agent/github-credential-helper.exe",
    )
    assert "HTTP_PROXY" not in environment
    assert "GH_TOKEN" not in environment
    assert environment["GIT_CONFIG_COUNT"] == "10"
    assert environment["GIT_CONFIG_VALUE_1"] == ""
    assert environment["GIT_CONFIG_VALUE_2"] == ""
    assert environment["GIT_CONFIG_VALUE_3"] == ""
    assert environment["GIT_CONFIG_VALUE_4"] == ""
    assert environment["GIT_CONFIG_VALUE_5"] == ""
    assert environment["GIT_CONFIG_VALUE_6"] == "false"
    assert environment["GIT_CONFIG_VALUE_7"] == "true"
    assert environment["GIT_CONFIG_VALUE_8"] == "true"
    assert environment["GIT_CONFIG_VALUE_9"] == "C:/agent/github-credential-helper.exe"


def test_git_runner_uses_isolated_environment_without_vault(
    profile: Profile, tmp_path: Path
) -> None:
    script = (
        "import os\nprint(os.environ['GIT_CONFIG_COUNT'])\n"
        "print(os.environ.get('HTTP_PROXY', ''))\n"
    )
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
    assert result.stdout.splitlines() == ["10", ""]
