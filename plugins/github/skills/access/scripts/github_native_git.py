from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

import access_core
import github_binding
import github_platform
import github_tools

_LOCAL_COMMANDS = frozenset(
    {
        "add",
        "apply",
        "blame",
        "branch",
        "check-attr",
        "check-ignore",
        "cherry-pick",
        "clean",
        "commit",
        "config",
        "describe",
        "diff",
        "diff-files",
        "diff-index",
        "diff-tree",
        "for-each-ref",
        "fsck",
        "gc",
        "grep",
        "init",
        "log",
        "ls-files",
        "ls-tree",
        "merge",
        "merge-base",
        "mv",
        "notes",
        "rebase",
        "reflog",
        "remote",
        "reset",
        "restore",
        "rev-list",
        "rev-parse",
        "revert",
        "rm",
        "shortlog",
        "show",
        "show-ref",
        "sparse-checkout",
        "stash",
        "status",
        "switch",
        "symbolic-ref",
        "tag",
        "update-index",
        "update-ref",
        "verify-commit",
        "verify-tag",
        "worktree",
    }
)
_NETWORK_COMMANDS = frozenset({"clone", "fetch", "pull", "push", "ls-remote"})


def helper_command(binding: github_binding.Binding) -> str:
    script = Path(__file__).with_name("github_access.py")
    return "!" + shlex.join(
        [
            sys.executable,
            "-B",
            str(script),
            "credential",
            "--binding",
            str(binding.path),
        ]
    )


def _effective_cwd(
    arguments: Sequence[str], base: Path
) -> tuple[Path, tuple[str, ...]]:
    current = base.resolve(strict=False)
    index = 0
    while index < len(arguments):
        item = arguments[index]
        if item == "-C":
            if index + 1 == len(arguments):
                raise access_core.AccessError("git-invalid: -C requires one directory")
            directory = arguments[index + 1]
            index += 2
        elif item.startswith("-C") and len(item) > 2:
            directory = item[2:]
            index += 1
        else:
            break
        if not directory:
            raise access_core.AccessError("git-invalid: -C requires one directory")
        candidate = Path(directory).expanduser()
        current = (
            candidate if candidate.is_absolute() else current / candidate
        ).resolve(strict=False)
    return current, tuple(arguments[index:])


def _read(executable: Path, cwd: Path, arguments: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            [str(executable), *arguments],
            text=True,
            capture_output=True,
            check=False,
            cwd=cwd,
            env=github_tools._git_config_environment(os.environ),
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise access_core.AccessError(
            "git-scope-invalid: Git inspection failed"
        ) from error
    if result.returncode:
        raise access_core.AccessError(
            "git-scope-invalid: inspect the selected repository"
        )
    return result.stdout.strip()


def remotes(executable: Path, cwd: Path) -> dict[str, str]:
    result = {}
    for name in _read(executable, cwd, ["remote"]).splitlines():
        urls = _read(executable, cwd, ["remote", "get-url", "--all", name]).splitlines()
        push_urls = _read(
            executable, cwd, ["remote", "get-url", "--push", "--all", name]
        ).splitlines()
        if len(urls) != 1 or push_urls != urls:
            raise access_core.AccessError(
                "git-scope-invalid: each remote needs one matching fetch/push URL"
            )
        result[name] = urls[0]
    return result


def _local_environment(source: Mapping[str, str]) -> dict[str, str]:
    environment = access_core.private_environment(source)
    environment.update(GIT_NO_LAZY_FETCH="1", GIT_ALLOW_PROTOCOL="")
    return environment


def _run(
    executable: Path,
    arguments: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
) -> int:
    try:
        return subprocess.run(
            [str(executable), *arguments], cwd=cwd, env=environment, check=False
        ).returncode
    except OSError as error:
        raise access_core.AccessError(
            "tool-unavailable: git did not complete safely"
        ) from error


def run(
    binding: github_binding.Binding,
    arguments: Sequence[str],
    platform: github_platform.PlatformProtocol,
) -> int:
    if tuple(arguments) == ("--version",):
        return _run(
            binding.git,
            arguments,
            Path.cwd(),
            access_core.private_environment(os.environ),
        )
    cwd, command = _effective_cwd(arguments, Path.cwd())
    if not command:
        raise access_core.AccessError("git-invalid: pass a supported git command")
    profile = binding.validate(cwd, platform)
    if command[0] in _LOCAL_COMMANDS:
        return _run(binding.git, command, cwd, _local_environment(os.environ))
    if command[0] not in _NETWORK_COMMANDS:
        raise access_core.AccessError("git-invalid: pass a supported git command")
    invocation = github_tools.validate_git_command(
        profile, command, remotes=remotes(binding.git, cwd)
    )
    entries = github_tools._effective_git_config(binding.git, cwd, os.environ, 60)
    with tempfile.TemporaryDirectory(prefix="github-access-") as home:
        return _run(
            binding.git,
            github_tools._normalized_git_arguments(invocation, entries),
            cwd,
            github_tools.git_environment(
                os.environ, helper_command(binding), Path(home)
            ),
        )
